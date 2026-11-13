"""Disk image detection: raw floppy, raw MBR disk, fixed VHD (footer stripped)."""
import os
import struct
from .fat import FatVolume, FatError

# VHD format constants (used by fixed-VHD detection above and dynamic-VHD parsing).
SECTOR_SIZE = 512
HEADER_SIZE = 1024
FOOTER_SIZE = 512
MAX_VIRTUAL_SIZE = 256 * 1024 * 1024        # 256 MiB, dynamic VHD only
MIN_BLOCK_SIZE = 512
MAX_BLOCK_SIZE = 2 * 1024 * 1024            # 2 MiB
MAX_BAT_ENTRIES = 524288                    # capacity cap, independent of virtual size
DISK_TYPE_FIXED = 2
DISK_TYPE_DYNAMIC = 3
DISK_TYPE_DIFFERENCING = 4

PTYPES = {0x01: "FAT12", 0x04: "FAT16<32M", 0x06: "FAT16", 0x0B: "FAT32", 0x0C: "FAT32 LBA",
          0x0E: "FAT16 LBA", 0x0F: "Extended LBA", 0x05: "Extended", 0x07: "NTFS/HPFS/exFAT",
          0x83: "Linux", 0x82: "Linux swap"}


def read_head_tail_windows(f, size):
    """Read the independent head/tail classification windows for VHD dispatch.

    `head` is the first min(512, size) bytes. `tail` is the last 512 bytes,
    read via its own seek/read pair, or None when size < 512. `tail` is never
    derived from `head`, even when their byte ranges overlap in the file
    (which happens for 512 <= size < 1024).
    """
    f.seek(0)
    head = f.read(min(SECTOR_SIZE, size))
    if size >= SECTOR_SIZE:
        f.seek(size - SECTOR_SIZE)
        tail = f.read(SECTOR_SIZE)
    else:
        tail = None
    return head, tail


def classify_footer_candidate(window):
    """Cheaply classify a (possibly short) footer candidate window.

    Returns {"cookie_ok": bool, "disk_type": int | None}. Performs only a
    cookie-presence check (bytes 0:8 == b"conectix") and a raw read of the
    disk-type field (big-endian uint32 at offset 60, length 4) when the
    window is long enough to contain it. Does NOT perform checksum or
    structural validation -- that happens later, only once the dynamic
    path is entered.
    """
    cookie_ok = window[:8] == b"conectix"
    if len(window) >= 64:
        disk_type = struct.unpack_from(">I", window, 60)[0]
    else:
        disk_type = None
    return {"cookie_ok": cookie_ok, "disk_type": disk_type}


def parse_footer(raw):
    """Parse all documented VHD footer fields from a 512-byte buffer.

    Pure `struct.unpack`-based parsing per the FooterFields table in
    design.md's Data Models section. Performs no validation -- checksum,
    cookie, and disk-type checks happen later, only once the dynamic path
    is entered.

    Returns a dict with keys: cookie, features, fileFormatVersion,
    dataOffset, timestamp, creatorApplication, creatorVersion,
    creatorHostOS, originalSize, currentSize, diskGeometry, diskType,
    checksum, uniqueId, savedState, reserved.
    """
    return {
        "cookie": raw[0:8],
        "features": struct.unpack_from(">I", raw, 8)[0],
        "fileFormatVersion": struct.unpack_from(">I", raw, 12)[0],
        "dataOffset": struct.unpack_from(">Q", raw, 16)[0],
        "timestamp": struct.unpack_from(">I", raw, 24)[0],
        "creatorApplication": raw[28:32],
        "creatorVersion": struct.unpack_from(">I", raw, 32)[0],
        "creatorHostOS": raw[36:40],
        "originalSize": struct.unpack_from(">Q", raw, 40)[0],
        "currentSize": struct.unpack_from(">Q", raw, 48)[0],
        "diskGeometry": raw[56:60],
        "diskType": struct.unpack_from(">I", raw, 60)[0],
        "checksum": struct.unpack_from(">I", raw, 64)[0],
        "uniqueId": raw[68:84],
        "savedState": raw[84],
        "reserved": raw[85:512],
    }


def validate_footer_checksum(raw, parsed):
    """Recompute the VHD footer checksum and raise ValueError on mismatch.

    The checksum is the ones'-complement of the sum of all 512 footer
    bytes, with bytes 64-67 (the checksum field itself) temporarily
    treated as zero during the sum.
    """
    buf = bytearray(raw[:512])
    buf[64:68] = b"\x00\x00\x00\x00"
    total = sum(buf)
    computed = (~total) & 0xFFFFFFFF
    if computed != parsed["checksum"]:
        raise ValueError(
            "VHD footer checksum mismatch: expected 0x%08X, computed 0x%08X"
            % (parsed["checksum"], computed)
        )


def validate_footer_structure(parsed):
    """Raise ValueError if the footer's structurally-required fields are
    out of their documented valid range.

    Checks cookie == b"conectix" and diskType in {2, 3, 4} (fixed,
    dynamic, differencing).
    """
    if parsed["cookie"] != b"conectix":
        raise ValueError(
            "VHD footer cookie mismatch: expected b'conectix', got %r" % (parsed["cookie"],)
        )
    if parsed["diskType"] not in (DISK_TYPE_FIXED, DISK_TYPE_DYNAMIC, DISK_TYPE_DIFFERENCING):
        raise ValueError(
            "VHD footer diskType %r is not one of the supported types (2, 3, 4)"
            % (parsed["diskType"],)
        )


def validate_footer_pair_consistent(footer_a, footer_b):
    """Raise ValueError unless the two footer copies are consistent.

    Two independent checks are performed:

    1. Five-field mutual agreement: footer_a and footer_b must agree on
       ALL of diskType, uniqueId, originalSize, currentSize, and
       dataOffset. A mismatch in any single one of these fields is
       sufficient to raise ValueError, even if all others match and even
       if both footers' own checksums are independently valid (checksum
       equality between the two copies is NOT used as a proxy for field
       agreement -- each footer's checksum only certifies its own
       internal self-consistency, not agreement with the other copy).

    2. Both-type-3 check, performed independently of check 1: both
       footer_a.diskType and footer_b.diskType must individually equal
       DISK_TYPE_DYNAMIC (3). This is re-confirmed regardless of whether
       the five-field agreement check passes -- two footers could agree
       with each other while both reporting some other disk type value,
       and such a pair must still be rejected here.
    """
    mismatched_fields = [
        field
        for field in ("diskType", "uniqueId", "originalSize", "currentSize", "dataOffset")
        if footer_a[field] != footer_b[field]
    ]
    if mismatched_fields:
        raise ValueError(
            "VHD footer pair mismatch: fields %s differ between the leading "
            "and trailing footer copies" % (", ".join(mismatched_fields),)
        )
    if footer_a["diskType"] != DISK_TYPE_DYNAMIC or footer_b["diskType"] != DISK_TYPE_DYNAMIC:
        raise ValueError(
            "VHD footer pair is not both diskType == 3 (dynamic): got %r and %r"
            % (footer_a["diskType"], footer_b["diskType"])
        )


def parse_header(raw):
    """Parse all documented VHD dynamic header fields from a 1024-byte buffer.

    Pure `struct.unpack`-based parsing per the HeaderFields table in
    design.md's Data Models section. Performs no validation -- checksum
    and structural checks happen in validate_header_checksum and
    validate_header_structure.

    Returns a dict with keys: cookie, dataOffset, tableOffset,
    headerVersion, maxTableEntries, blockSize, checksum, parentUniqueId,
    parentTimestamp, reserved1, parentUnicodeName, parentLocatorEntries,
    reserved2.
    """
    return {
        "cookie": raw[0:8],
        "dataOffset": struct.unpack_from(">Q", raw, 8)[0],
        "tableOffset": struct.unpack_from(">Q", raw, 16)[0],
        "headerVersion": struct.unpack_from(">I", raw, 24)[0],
        "maxTableEntries": struct.unpack_from(">I", raw, 28)[0],
        "blockSize": struct.unpack_from(">I", raw, 32)[0],
        "checksum": struct.unpack_from(">I", raw, 36)[0],
        "parentUniqueId": raw[40:56],
        "parentTimestamp": struct.unpack_from(">I", raw, 56)[0],
        "reserved1": raw[60:64],
        "parentUnicodeName": raw[64:576],
        "parentLocatorEntries": raw[576:768],
        "reserved2": raw[768:1024],
    }


def validate_header_span(header_offset, file_size, footer_intervals):
    """Raise ValueError unless the header's 1024-byte span is valid.

    Checks:
      - header_offset >= 512
      - header_offset is a multiple of 512 (sector-aligned)
      - header_offset + 1024 <= file_size
      - [header_offset, header_offset + 1024) does not overlap either
        interval in footer_intervals (a list of (start, end) tuples)
    """
    if header_offset < SECTOR_SIZE:
        raise ValueError(
            "VHD header offset %d is less than the minimum of %d" % (header_offset, SECTOR_SIZE)
        )
    if header_offset % SECTOR_SIZE != 0:
        raise ValueError(
            "VHD header offset %d is not sector-aligned (multiple of %d)"
            % (header_offset, SECTOR_SIZE)
        )
    header_end = header_offset + HEADER_SIZE
    if header_end > file_size:
        raise ValueError(
            "VHD header span [%d, %d) exceeds file size %d" % (header_offset, header_end, file_size)
        )
    for (start, end) in footer_intervals:
        if header_offset < end and start < header_end:
            raise ValueError(
                "VHD header span [%d, %d) overlaps footer interval [%d, %d)"
                % (header_offset, header_end, start, end)
            )


def validate_header_checksum(raw, parsed):
    """Recompute the VHD header checksum and raise ValueError on mismatch.

    The checksum is the ones'-complement of the sum of all 1024 header
    bytes, with bytes 36-39 (the checksum field itself) temporarily
    treated as zero during the sum.
    """
    buf = bytearray(raw[:HEADER_SIZE])
    buf[36:40] = b"\x00\x00\x00\x00"
    total = sum(buf)
    computed = (~total) & 0xFFFFFFFF
    if computed != parsed["checksum"]:
        raise ValueError(
            "VHD header checksum mismatch: expected 0x%08X, computed 0x%08X"
            % (parsed["checksum"], computed)
        )


def validate_header_structure(parsed):
    """Raise ValueError if the header's structurally-required fields are
    out of their documented valid range.

    Checks cookie == b"cxsparse", headerVersion == 0x00010000, and
    tableOffset is sector-aligned and >= 512.
    """
    if parsed["cookie"] != b"cxsparse":
        raise ValueError(
            "VHD header cookie mismatch: expected b'cxsparse', got %r" % (parsed["cookie"],)
        )
    if parsed["headerVersion"] != 0x00010000:
        raise ValueError(
            "VHD header version %r is not the expected value 0x00010000" % (parsed["headerVersion"],)
        )
    table_offset = parsed["tableOffset"]
    if table_offset < SECTOR_SIZE or table_offset % SECTOR_SIZE != 0:
        raise ValueError(
            "VHD header tableOffset %d is not sector-aligned and >= %d" % (table_offset, SECTOR_SIZE)
        )


def validate_block_size(block_size):
    """Raise ValueError unless block_size is a structurally valid VHD block size.

    Checks, independent of virtual size and BAT capacity:
      - MIN_BLOCK_SIZE <= block_size <= MAX_BLOCK_SIZE
      - block_size is a power of two
      - block_size is a multiple of SECTOR_SIZE

    This check is a pure property of the block_size field's own value; it
    does not consult virtual_size or maxTableEntries.
    """
    if block_size < MIN_BLOCK_SIZE or block_size > MAX_BLOCK_SIZE:
        raise ValueError(
            "VHD block size %d is outside the valid range [%d, %d]"
            % (block_size, MIN_BLOCK_SIZE, MAX_BLOCK_SIZE)
        )
    if block_size & (block_size - 1) != 0:
        raise ValueError("VHD block size %d is not a power of two" % (block_size,))
    if block_size % SECTOR_SIZE != 0:
        raise ValueError(
            "VHD block size %d is not a multiple of the sector size %d" % (block_size, SECTOR_SIZE)
        )


def validate_virtual_size(virtual_size):
    """Raise ValueError unless virtual_size is a valid dynamic VHD virtual size.

    Checks:
      - 0 < virtual_size <= MAX_VIRTUAL_SIZE
      - virtual_size % SECTOR_SIZE == 0 (sector-aligned)

    Exactly MAX_VIRTUAL_SIZE (268,435,456 bytes) is allowed; it is itself a
    multiple of 512, so the boundary case satisfies both checks. A
    virtual_size that is in range but not sector-aligned (e.g. it would
    imply a final partial sector) is rejected independently of the range
    check.
    """
    if virtual_size <= 0 or virtual_size > MAX_VIRTUAL_SIZE:
        raise ValueError(
            "VHD virtual size %d is outside the valid range (0, %d]"
            % (virtual_size, MAX_VIRTUAL_SIZE)
        )
    if virtual_size % SECTOR_SIZE != 0:
        raise ValueError(
            "VHD virtual size %d is not sector-aligned (multiple of %d)"
            % (virtual_size, SECTOR_SIZE)
        )


def validate_bat_capacity(max_table_entries, virtual_size, block_size):
    """Raise ValueError unless max_table_entries exactly matches the BAT
    capacity implied by virtual_size and block_size, and is within the
    absolute capacity cap.

    Checks:
      max_table_entries == ceil(virtual_size / block_size)   (EXACT match)
      AND max_table_entries <= MAX_BAT_ENTRIES

    Policy: this requires EXACT equality, not merely ">=". A header
    claiming more BAT entries than the virtual size implies is rejected,
    not silently truncated or tolerated.

    Must be called, and must pass, strictly before bat_bytes_len is
    computed or any BAT bytes are read.
    """
    expected = -(-virtual_size // block_size)  # ceil(virtual_size / block_size)
    if max_table_entries != expected:
        raise ValueError(
            "VHD maxTableEntries %d does not exactly match ceil(virtual_size / block_size) = %d"
            % (max_table_entries, expected)
        )
    if max_table_entries > MAX_BAT_ENTRIES:
        raise ValueError(
            "VHD maxTableEntries %d exceeds the maximum allowed BAT capacity of %d"
            % (max_table_entries, MAX_BAT_ENTRIES)
        )


def compute_bat_bytes_len(max_table_entries):
    """Return the BAT region length in bytes, rounded up to a whole sector.

    Returns ceil(max_table_entries * 4 / 512) * 512.

    PRECONDITION (enforced by caller ordering, not re-checked here):
    max_table_entries has already passed validate_bat_capacity, so this
    value is bounded by ceil(524288 * 4 / 512) * 512 bytes. Computed
    directly: 524288 * 4 = 2,097,152 bytes exactly (2 MiB), and since
    2,097,152 is already an exact multiple of 512 (2,097,152 / 512 =
    4,096 exactly), the sector-rounding does not add anything further --
    the bound is exactly 2,097,152 bytes (2 MiB), not 4,194,304 bytes.
    This function must never be called with an unvalidated
    max_table_entries.
    """
    bat_raw_len = max_table_entries * 4
    return -(-bat_raw_len // SECTOR_SIZE) * SECTOR_SIZE


def validate_bat_span(table_offset, bat_bytes_len, file_size, prior_intervals):
    """Raise ValueError unless the BAT's byte span is valid.

    Checks:
      - table_offset >= 512
      - table_offset + bat_bytes_len <= file_size
      - [table_offset, table_offset + bat_bytes_len) does not overlap any
        interval in prior_intervals (a list of (start, end) tuples: both
        footers, header)
    """
    if table_offset < SECTOR_SIZE:
        raise ValueError(
            "VHD BAT offset %d is less than the minimum of %d" % (table_offset, SECTOR_SIZE)
        )
    table_end = table_offset + bat_bytes_len
    if table_end > file_size:
        raise ValueError(
            "VHD BAT span [%d, %d) exceeds file size %d" % (table_offset, table_end, file_size)
        )
    for (start, end) in prior_intervals:
        if table_offset < end and start < table_end:
            raise ValueError(
                "VHD BAT span [%d, %d) overlaps interval [%d, %d)"
                % (table_offset, table_end, start, end)
            )


def parse_bat(raw, max_table_entries):
    """Parse exactly max_table_entries big-endian uint32 sector offsets
    from raw.

    A value of 0xFFFFFFFF marks the entry as unallocated (sparse). Loops
    exactly max_table_entries times -- this count is, by this point in
    the pipeline, already validated (via validate_bat_capacity) to
    exactly equal ceil(virtual_size / block_size), so no separately-
    computed entry count is used anywhere else in the pipeline.

    Returns a list of max_table_entries integers, each either a sector
    offset (to be multiplied by SECTOR_SIZE for a byte offset) or
    0xFFFFFFFF for unallocated entries.
    """
    return [struct.unpack_from(">I", raw, i * 4)[0] for i in range(max_table_entries)]


def build_metadata_intervals(leading_footer_present, file_size, header_offset, table_offset, bat_bytes_len):
    """Build the (start, end) half-open intervals for all metadata structures.

    Returns a list of (start, end) tuples -- i.e. each tuple means the
    byte range [start, end), NOT (start, length) -- for:
      - the trailing footer: (file_size - 512, file_size)
      - the leading footer, if leading_footer_present: (0, 512)
      - the header: (header_offset, header_offset + 1024)
      - the BAT: (table_offset, table_offset + bat_bytes_len)

    file_size is the total physical file size (NOT file_size - 512); the
    trailing footer interval's own end deliberately equals file_size.
    """
    intervals = [(file_size - FOOTER_SIZE, file_size)]
    if leading_footer_present:
        intervals.append((0, FOOTER_SIZE))
    intervals.append((header_offset, header_offset + HEADER_SIZE))
    intervals.append((table_offset, table_offset + bat_bytes_len))
    return intervals


def build_block_intervals(bat, block_size):
    """Build the (start, end) half-open intervals for each allocated BAT entry.

    For each allocated entry in bat (value != 0xFFFFFFFF), computes the
    interval occupied by that entry's sector bitmap plus data block:
      bitmap_sectors = ceil(ceil(block_size / SECTOR_SIZE) / 8 / SECTOR_SIZE)
                       sectors, rounded up to a whole sector (at least 1)
      start = bat_entry_value * SECTOR_SIZE
      end   = start + (bitmap_sectors * SECTOR_SIZE) + block_size

    Returns one (start, end) tuple per allocated entry, in BAT order (not
    sorted). Unallocated entries (0xFFFFFFFF) contribute no interval.
    """
    intervals = []
    for entry in bat:
        if entry == 0xFFFFFFFF:
            continue
        sectors_per_block = -(-block_size // SECTOR_SIZE)  # ceil(block_size / 512)
        bitmap_bytes = -(-sectors_per_block // 8)          # ceil(sectors_per_block / 8)
        bitmap_sectors = -(-bitmap_bytes // SECTOR_SIZE)   # ceil(bitmap_bytes / 512)
        if bitmap_sectors < 1:
            bitmap_sectors = 1
        start = entry * SECTOR_SIZE
        end = start + (bitmap_sectors * SECTOR_SIZE) + block_size
        intervals.append((start, end))
    return intervals


def validate_disjoint_sorted(intervals, file_size):
    """Validate that all intervals are in-bounds and pairwise disjoint.

    Given a list of (start, end) half-open intervals (ALL intervals: both
    footers, header, BAT, and every allocated block), sorts them by start
    and performs a single linear adjacent-pair scan, raising ValueError
    if any of the following hold for any interval (start, end):
      - start < 0
      - end > file_size   (the FULL file size, not file_size - 512; the
        trailing footer's own interval legitimately has end == file_size)
      - any two adjacent (after sort) intervals overlap, i.e. for sorted
        intervals i and i+1: intervals[i].end > intervals[i+1].start

    Runs in O(N log N) due to the sort; the scan itself is O(N).
    """
    ordered = sorted(intervals, key=lambda iv: iv[0])
    for (start, end) in ordered:
        if start < 0:
            raise ValueError("VHD interval [%d, %d) has a negative start" % (start, end))
        if end > file_size:
            raise ValueError(
                "VHD interval [%d, %d) exceeds file size %d" % (start, end, file_size)
            )
    for i in range(len(ordered) - 1):
        cur_end = ordered[i][1]
        next_start = ordered[i + 1][0]
        if cur_end > next_start:
            raise ValueError(
                "VHD intervals overlap: %r and %r" % (ordered[i], ordered[i + 1])
            )


def validate_blocks_do_not_reach_footer(block_intervals, file_size):
    """Validate that no allocated block's interval encroaches on the
    trailing footer region.

    Separate, additional check applied ONLY to block_intervals (not to
    the footer intervals, header interval, or BAT interval). Raises
    ValueError if any block interval (start, end) has
    end > file_size - 512, i.e. if any allocated block's bitmap+data
    region would extend into or past the trailing 512-byte footer
    region.

    This check is independent of, and in addition to,
    validate_disjoint_sorted.
    """
    limit = file_size - FOOTER_SIZE
    for (start, end) in block_intervals:
        if end > limit:
            raise ValueError(
                "VHD block interval [%d, %d) reaches into the trailing footer "
                "region (must end at or before %d)" % (start, end, limit)
            )


def read_bitmap_and_block(f, bat_entry_sectors, block_size):
    """Bounded read of a single allocated BAT entry's sector bitmap and
    data block.

    Seeks to bat_entry_sectors * SECTOR_SIZE and reads the sector bitmap
    (rounded up to a whole sector, using the same bitmap_sectors formula
    as build_block_intervals: ceil(ceil(block_size / SECTOR_SIZE) / 8 /
    SECTOR_SIZE) sectors, at least 1), then reads block_size bytes
    immediately following the bitmap.

    Validates that each read returned exactly the number of bytes
    requested. Python's file.read(n) is permitted to return fewer than n
    bytes (e.g. near end-of-file); a short read here means the VHD's
    validated intervals claimed data that the underlying file does not
    actually contain, which is corruption/truncation, not something that
    should be allowed to silently propagate as a too-short buffer to the
    caller.

    Returns (bitmap, block) where bitmap is exactly bitmap_sectors *
    SECTOR_SIZE bytes and block is exactly block_size bytes.
    """
    sectors_per_block = -(-block_size // SECTOR_SIZE)  # ceil(block_size / 512)
    bitmap_bytes = -(-sectors_per_block // 8)          # ceil(sectors_per_block / 8)
    bitmap_sectors = -(-bitmap_bytes // SECTOR_SIZE)   # ceil(bitmap_bytes / 512)
    if bitmap_sectors < 1:
        bitmap_sectors = 1
    bitmap_len = bitmap_sectors * SECTOR_SIZE

    f.seek(bat_entry_sectors * SECTOR_SIZE)
    bitmap = f.read(bitmap_len)
    if len(bitmap) != bitmap_len:
        raise ValueError(
            "VHD block data truncated: expected %d bitmap bytes, got %d"
            % (bitmap_len, len(bitmap))
        )
    block = f.read(block_size)
    if len(block) != block_size:
        raise ValueError(
            "VHD block data truncated: expected %d block bytes, got %d"
            % (block_size, len(block))
        )
    return bitmap, block


def reconstruct_flat_bytes(virtual_size, block_size, bat, f):
    """Reconstruct the flat disk bytes a dynamic VHD represents.

    Allocates a zero-filled bytearray of virtual_size bytes. For each
    allocated BAT entry (value != 0xFFFFFFFF), reads its bitmap and block
    via read_bitmap_and_block, then for each sector within the block
    whose bitmap bit is set, copies that sector's 512 bytes from the
    block into the correct offset in the output buffer. Sectors whose
    bit is not set are left as zero.

    Bit order: most-significant-bit first -- bit 7 of byte 0 of the
    bitmap corresponds to sector 0 of the block, bit 6 of byte 0 to
    sector 1, and so on.

    entry_index (the 0-based position of the entry within the bat list)
    determines the block's starting offset in the OUTPUT buffer
    (entry_index * block_size); this is distinct from the entry's VALUE,
    which is the sector offset into the FILE where that block's bitmap
    and data physically live (passed to read_bitmap_and_block).

    The last block of the disk may be partial: only
    min(block_size, virtual_size - entry_index * block_size) bytes of it
    actually fall within the output buffer, and within that, each
    sector's copy is itself bounded by the remaining space in the
    output buffer.

    read_bitmap_and_block guarantees bitmap and block are always exactly
    their expected lengths (raising ValueError otherwise), so the
    per-sector slices below never silently under-copy due to a short
    underlying read; no redundant length check is duplicated here.

    Returns bytes(output).
    """
    output = bytearray(virtual_size)
    sectors_per_block = -(-block_size // SECTOR_SIZE)  # ceil(block_size / 512)

    for entry_index, entry in enumerate(bat):
        if entry == 0xFFFFFFFF:
            continue

        output_offset = entry_index * block_size
        if output_offset >= virtual_size:
            continue

        bitmap, block = read_bitmap_and_block(f, entry, block_size)

        # The amount of this block that actually falls within the
        # (possibly smaller) virtual disk -- relevant only for the last
        # block, which may be partial.
        block_len_in_output = min(block_size, virtual_size - output_offset)

        for sector_index in range(sectors_per_block):
            sector_output_offset = output_offset + sector_index * SECTOR_SIZE
            if sector_output_offset >= output_offset + block_len_in_output:
                break

            byte_index = sector_index // 8
            bit_index = 7 - (sector_index % 8)
            bit_set = (bitmap[byte_index] >> bit_index) & 1
            if not bit_set:
                continue

            sector_start_in_block = sector_index * SECTOR_SIZE
            sector_len = min(SECTOR_SIZE, output_offset + block_len_in_output - sector_output_offset)
            sector_data = block[sector_start_in_block:sector_start_in_block + sector_len]
            output[sector_output_offset:sector_output_offset + sector_len] = sector_data

    return bytes(output)


def _load_dynamic_vhd(path, file_size):
    """Full validation/reconstruction pipeline for a dynamic (type 3) VHD.

    Performs the bounded-read pipeline documented in design.md's
    Architecture section, in order: read and validate both footer
    copies, validate pair consistency, locate and validate the header
    span, read and validate the header, validate block size, validate
    virtual size, validate BAT capacity, compute bat_bytes_len, validate
    BAT span, read and parse the BAT, build metadata/block intervals,
    validate disjointness and the block-footer bound, then reconstruct
    the flat disk bytes.

    Raises ValueError (propagated from the helper functions) on any
    validation failure. Returns (bytes(output), info) on success, with
    info = {"path": path, "size": file_size, "container": "dynamic VHD",
    "virtual_size": virtual_size}.
    """
    if file_size < FOOTER_SIZE:
        raise ValueError("dynamic VHD: file is too short to contain a valid footer")

    with open(path, "rb") as f:
        f.seek(0)
        leading_raw = f.read(FOOTER_SIZE)
        f.seek(file_size - FOOTER_SIZE)
        trailing_raw = f.read(FOOTER_SIZE)

        leading_footer = parse_footer(leading_raw)
        trailing_footer = parse_footer(trailing_raw)

        validate_footer_checksum(leading_raw, leading_footer)
        validate_footer_structure(leading_footer)
        validate_footer_checksum(trailing_raw, trailing_footer)
        validate_footer_structure(trailing_footer)

        validate_footer_pair_consistent(leading_footer, trailing_footer)

        header_offset = trailing_footer["dataOffset"]
        footer_intervals = [(0, FOOTER_SIZE), (file_size - FOOTER_SIZE, file_size)]
        validate_header_span(header_offset, file_size, footer_intervals)

        f.seek(header_offset)
        header_raw = f.read(HEADER_SIZE)
        header = parse_header(header_raw)
        validate_header_checksum(header_raw, header)
        validate_header_structure(header)

        block_size = header["blockSize"]
        validate_block_size(block_size)

        virtual_size = trailing_footer["currentSize"]
        validate_virtual_size(virtual_size)

        max_table_entries = header["maxTableEntries"]
        validate_bat_capacity(max_table_entries, virtual_size, block_size)

        bat_bytes_len = compute_bat_bytes_len(max_table_entries)
        table_offset = header["tableOffset"]
        prior_intervals = [(0, FOOTER_SIZE), (file_size - FOOTER_SIZE, file_size),
                           (header_offset, header_offset + HEADER_SIZE)]
        validate_bat_span(table_offset, bat_bytes_len, file_size, prior_intervals)

        f.seek(table_offset)
        bat_raw = f.read(bat_bytes_len)
        bat = parse_bat(bat_raw, max_table_entries)

        metadata_intervals = build_metadata_intervals(
            True, file_size, header_offset, table_offset, bat_bytes_len
        )
        block_intervals = build_block_intervals(bat, block_size)

        validate_disjoint_sorted(metadata_intervals + block_intervals, file_size)
        validate_blocks_do_not_reach_footer(block_intervals, file_size)

        output = reconstruct_flat_bytes(virtual_size, block_size, bat, f)

    info = {"path": path, "size": file_size, "container": "dynamic VHD", "virtual_size": virtual_size}
    return bytes(output), info


def load(path):
    file_size = os.stat(path).st_size
    with open(path, "rb") as f:
        head, tail = read_head_tail_windows(f, file_size)
    head_classified = classify_footer_candidate(head)
    tail_classified = classify_footer_candidate(tail) if tail is not None else {"cookie_ok": False, "disk_type": None}

    head_is_candidate = head_classified["cookie_ok"]
    tail_is_candidate = tail_classified["cookie_ok"]

    if (head_is_candidate and head_classified["disk_type"] == DISK_TYPE_DIFFERENCING) or \
       (tail_is_candidate and tail_classified["disk_type"] == DISK_TYPE_DIFFERENCING):
        raise ValueError("Differencing VHD is not supported")

    if (head_is_candidate and head_classified["disk_type"] == DISK_TYPE_DYNAMIC) or \
       (tail_is_candidate and tail_classified["disk_type"] == DISK_TYPE_DYNAMIC):
        return _load_dynamic_vhd(path, file_size)

    data = open(path, "rb").read()
    info = {"path": path, "size": len(data), "container": "raw"}
    if len(data) >= 512 and data[-512:-504] == b"conectix":
        info["container"] = "fixed VHD"
        data = data[:-512]
    elif data[:8] == b"conectix":
        raise ValueError("dynamic VHD is not supported yet; convert to raw first")
    elif data[:4] in (b"KDMV", b"QFI\xfb") or data[:8] == b"<<< Orac":
        raise ValueError("VMDK/QCOW2/VDI images are not supported yet; convert to raw first")
    return data, info


def partitions(data):
    out = []
    if data[510:512] != b"\x55\xaa":
        return out
    for i in range(4):
        e = data[446 + 16 * i:462 + 16 * i]
        t = e[4]
        if t == 0:
            continue
        lba, n = struct.unpack_from("<II", e, 8)
        out.append({"index": i + 1, "type": t, "type_name": PTYPES.get(t, "0x%02x" % t),
                    "active": e[0] == 0x80, "lba": lba, "sectors": n})
    return out


def volumes(data):
    """Return list of (description, FatVolume). Handles bare floppy or MBR."""
    vols = []
    try:
        v = FatVolume(data, 0)
        # A valid-looking MBR can also pass; a real FAT has sane BPB and a FAT type label
        vols.append(("whole image", v))
        return vols
    except FatError:
        pass
    for p in partitions(data):
        if p["type"] in (0x01, 0x04, 0x06, 0x0B, 0x0C, 0x0E):
            try:
                vols.append(("partition %d (%s)" % (p["index"], p["type_name"]),
                             FatVolume(data, p["lba"] * 512)))
            except FatError:
                pass
    return vols


def detect_filesystem_at_offset(data, offset=0):
    """Detect filesystem type at given byte offset.
    
    Returns: "FAT12", "FAT16", "FAT32", "NTFS", "exFAT", or "Unknown"
    """
    # Extract boot sector
    sector = data[offset:offset + 512]
    if len(sector) < 512:
        return "Unknown"
    
    # Check bytes 3:11 for NTFS/exFAT signatures
    oem = sector[3:11]
    if oem == b"NTFS    ":
        return "NTFS"
    if oem == b"EXFAT   ":
        return "exFAT"
    
    # Try FAT detection via structural BPB parsing
    try:
        fat = FatVolume(data, offset)
        return fat.kind  # "FAT12", "FAT16", or "FAT32"
    except FatError:
        return "Unknown"


def unreadable(data):
    """Detect unreadable (non-FAT) volumes.
    
    Returns list of dictionaries with detection results for NTFS, exFAT, 
    and unknown filesystems. FAT volumes are not included.
    """
    detected = []
    
    # First, check for valid bare FAT image (preserve early return behavior)
    try:
        FatVolume(data, 0)
        return detected  # Valid FAT at offset 0, no unreadable volumes
    except FatError:
        pass  # Not a bare FAT floppy
    
    # Check if we have valid MBR partitions
    part_list = partitions(data)
    
    if not part_list:
        # No MBR partitions, check whole image for non-FAT filesystems
        fs_type = detect_filesystem_at_offset(data, 0)
        if fs_type in ("NTFS", "exFAT", "Unknown"):
            sector = data[0:512] if len(data) >= 512 else b""
            oem = sector[3:11].decode("ascii", "ignore").strip() if len(sector) >= 11 else ""
            detected.append({
                "where": "whole image",
                "fs": fs_type,
                "status": "detected, not readable",
                "oem": oem
            })
        return detected
    
    # Check partitions
    for p in part_list:
        offset = p["lba"] * 512
        fs_type = detect_filesystem_at_offset(data, offset)
        
        if fs_type in ("NTFS", "exFAT", "Unknown"):
            desc = f"partition {p['index']} ({p['type_name']})"
            sector = data[offset:offset + 512] if len(data) >= offset + 512 else b""
            oem = sector[3:11].decode("ascii", "ignore").strip() if len(sector) >= 11 else ""
            detected.append({
                "where": desc,
                "fs": fs_type,
                "status": "detected, not readable",
                "oem": oem
            })
    
    return detected


def boot_code_hint(data, offset=0):
    b = data[offset:offset + 512]
    t = b.decode("latin-1")
    for k, v in (("MSWIN4.1", "Windows 95 OSR2/98 boot sector"), ("MSDOS5.0", "MS-DOS 5/6 or Win95 boot sector"),
                 ("IBM  3.3", "PC-DOS 3.3 boot sector"), ("FRDOS", "FreeDOS boot sector"),
                 ("NTFS", "NTFS boot sector")):
        if k in t[:11] or (k == "NTFS" and "NTFS" in t[3:11]):
            return v
    if "Invalid system disk" in t or "Non-System" in t or "Non-system" in t:
        return "DOS-style boot sector"
    return None
