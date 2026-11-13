"""Tiny synthetic disk images for tests. Built in memory, no downloads, no real shop data."""
import random
import struct

SECTOR = 512


def _boot_sector(total, fat_sectors, root_entries, label=b"TESTDISK   "):
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x3c\x90"
    b[3:11] = b"MSDOS5.0"
    struct.pack_into("<HBHBHHBHHHI", b, 11, SECTOR, 1, 1, 2, root_entries, total, 0xF0, fat_sectors, 18, 2, 0)
    b[36] = 0
    b[38] = 0x29
    b[43:54] = label
    b[54:62] = b"FAT12   "
    b[510:512] = b"\x55\xaa"
    return b


def fat12_image(files=None, total=2880):
    """Valid FAT12 floppy (1.44 MB geometry by default) with root-directory files.
    files: {"NAME.EXT": bytes}. One cluster is one sector. Files must be small (a few KB)."""
    files = files if files is not None else {"CONFIG.SYS": b"DEVICE=HASPDOS.SYS\r\n", "README.TXT": b"hello\r\n"}
    fat_sectors, root_entries = 9, 224
    img = bytearray(total * SECTOR)
    img[0:SECTOR] = _boot_sector(total, fat_sectors, root_entries)
    fat = bytearray(fat_sectors * SECTOR)
    fat[0:3] = b"\xf0\xff\xff"
    root_start = 1 + 2 * fat_sectors
    data_start = root_start + (root_entries * 32 + SECTOR - 1) // SECTOR
    next_cluster = 2

    def set_fat(c, v):
        i = c + c // 2
        cur = struct.unpack_from("<H", fat, i)[0]
        cur = (cur & 0x000F) | (v << 4) if c & 1 else (cur & 0xF000) | v
        struct.pack_into("<H", fat, i, cur)

    for idx, (name, content) in enumerate(files.items()):
        base, _, ext = name.partition(".")
        n = max(1, (len(content) + SECTOR - 1) // SECTOR)
        clusters = list(range(next_cluster, next_cluster + n))
        next_cluster += n
        for k, c in enumerate(clusters):
            set_fat(c, clusters[k + 1] if k + 1 < len(clusters) else 0xFFF)
            off = (data_start + c - 2) * SECTOR
            chunk = content[k * SECTOR:(k + 1) * SECTOR]
            img[off:off + len(chunk)] = chunk
        e = bytearray(32)
        e[0:11] = base.upper().ljust(8).encode()[:8] + ext.upper().ljust(3).encode()[:3]
        e[11] = 0x20
        struct.pack_into("<H", e, 26, clusters[0])
        struct.pack_into("<I", e, 28, len(content))
        o = root_start * SECTOR + idx * 32
        img[o:o + 32] = e
    for k in range(2):
        o = (1 + k * fat_sectors) * SECTOR
        img[o:o + len(fat)] = fat
    return bytes(img)


def ntfs_boot_sector():
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x52\x90"
    b[3:11] = b"NTFS    "
    b[510:512] = b"\x55\xaa"
    return bytes(b) + bytes(SECTOR * 63)


def exfat_boot_sector():
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x76\x90"
    b[3:11] = b"EXFAT   "
    b[510:512] = b"\x55\xaa"
    return bytes(b) + bytes(SECTOR * 63)


def unknown_sector(size=SECTOR * 64):
    return bytes((i * 7 + 3) & 0xFF for i in range(size))


def mbr_disk(ptype, partition_bytes, start_lba=63):
    """MBR disk with one partition of the given type holding partition_bytes."""
    mbr = bytearray(SECTOR)
    e = bytearray(16)
    e[0] = 0x80
    e[4] = ptype
    struct.pack_into("<II", e, 8, start_lba, len(partition_bytes) // SECTOR)
    mbr[446:462] = e
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr) + bytes(SECTOR * (start_lba - 1)) + partition_bytes


def vhd_checksum(buf, checksum_offset):
    """Compute the VHD ones'-complement checksum of buf with the 4 bytes
    at checksum_offset treated as zero, per design.md."""
    tmp = bytearray(buf)
    tmp[checksum_offset:checksum_offset + 4] = b"\x00\x00\x00\x00"
    return (~sum(tmp)) & 0xFFFFFFFF


def build_vhd_footer(disk_type, data_offset, current_size=0, original_size=0, unique_id=None):
    """Build a complete, correctly-checksummed 512-byte VHD footer."""
    if unique_id is None:
        unique_id = bytes(range(16))
    buf = bytearray(512)
    buf[0:8] = b"conectix"
    struct.pack_into(">I", buf, 8, 2)                 # features
    struct.pack_into(">I", buf, 12, 0x00010000)       # fileFormatVersion
    struct.pack_into(">Q", buf, 16, data_offset)       # dataOffset
    struct.pack_into(">I", buf, 24, 0)                 # timestamp
    buf[28:32] = b"wa  "                                # creatorApplication
    struct.pack_into(">I", buf, 32, 0x00010000)        # creatorVersion
    buf[36:40] = b"Wi2k"                                # creatorHostOS
    struct.pack_into(">Q", buf, 40, original_size)     # originalSize
    struct.pack_into(">Q", buf, 48, current_size)      # currentSize
    buf[56:60] = bytes([0, 16, 63, 63])                 # diskGeometry
    struct.pack_into(">I", buf, 60, disk_type)         # diskType
    struct.pack_into(">I", buf, 64, 0)                 # checksum placeholder
    buf[68:84] = unique_id
    buf[84] = 0
    buf[85:512] = b"\x00" * (512 - 85)
    checksum = vhd_checksum(buf, 64)
    struct.pack_into(">I", buf, 64, checksum)
    return bytes(buf)


def build_vhd_header(table_offset, max_table_entries, block_size):
    """Build a complete, correctly-checksummed 1024-byte VHD header."""
    buf = bytearray(1024)
    buf[0:8] = b"cxsparse"
    struct.pack_into(">Q", buf, 8, 0xFFFFFFFFFFFFFFFF)
    struct.pack_into(">Q", buf, 16, table_offset)
    struct.pack_into(">I", buf, 24, 0x00010000)
    struct.pack_into(">I", buf, 28, max_table_entries)
    struct.pack_into(">I", buf, 32, block_size)
    struct.pack_into(">I", buf, 36, 0)
    buf[40:56] = bytes(16)
    struct.pack_into(">I", buf, 56, 0)
    buf[60:64] = bytes(4)
    buf[64:576] = bytes(512)
    buf[576:768] = bytes(192)
    buf[768:1024] = bytes(256)
    checksum = vhd_checksum(buf, 36)
    struct.pack_into(">I", buf, 36, checksum)
    return bytes(buf)


def dynamic_vhd_image(files=None, total=2880, block_size=512):
    """Synthetic_Builder (requirements.md glossary): wraps valid fat12_image
    content in a complete, correctly-checksummed dynamic VHD container.

    Calls fat12_image(files, total) to get the flat payload bytes, then
    splits that payload into block_size chunks, allocates a BAT entry for
    every chunk (no sparse blocks), and marks every sector in every
    per-block bitmap as used -- so the wrapped fat12_image bytes are
    recoverable exactly, byte for byte, via image.load().

    Layout: [leading footer 512][header 1024][BAT, rounded to sector]
            [per-block bitmap + block]...[trailing footer 512].

    Returns the complete dynamic VHD bytes.
    """
    payload = fat12_image(files, total)

    num_blocks = -(-len(payload) // block_size)  # ceil
    virtual_size = block_size * num_blocks
    padded_payload = payload + b"\x00" * (virtual_size - len(payload))

    header_offset = 512
    table_offset = header_offset + 1024
    max_table_entries = num_blocks
    bat_bytes_len = -(-max_table_entries * 4 // 512) * 512

    sectors_per_block = -(-block_size // 512)
    bitmap_bytes = -(-sectors_per_block // 8)
    bitmap_sectors = max(1, -(-bitmap_bytes // 512))
    bitmap_len = bitmap_sectors * 512

    blocks_start = table_offset + bat_bytes_len
    bat_entries = []
    block_region = bytearray()
    next_sector = blocks_start // 512
    for b in range(num_blocks):
        bat_entries.append(next_sector)
        bitmap = bytearray(bitmap_len)
        for s in range(sectors_per_block):
            byte_index = s // 8
            bit_index = 7 - (s % 8)
            bitmap[byte_index] |= (1 << bit_index)
        block_data = padded_payload[b * block_size:(b + 1) * block_size]
        block_region += bitmap
        block_region += block_data
        next_sector += (bitmap_len + block_size) // 512

    trailing_footer = build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
    leading_footer = build_vhd_footer(3, header_offset, current_size=virtual_size, original_size=virtual_size)
    header = build_vhd_header(table_offset, max_table_entries, block_size)
    bat_raw = bytearray(bat_bytes_len)
    for i, entry in enumerate(bat_entries):
        struct.pack_into(">I", bat_raw, i * 4, entry)

    out = bytearray()
    out += leading_footer
    out += header
    out += bat_raw
    out += block_region
    out += trailing_footer
    return bytes(out)


def hand_encoded_dynamic_vhd_sample():
    """A single, complete, minimal, valid dynamic VHD built by directly
    packing footer/header/BAT/bitmap/block bytes at fixed, literal byte
    offsets with struct.pack -- written from scratch in this function.

    This is deliberately independent of dynamic_vhd_image()/build_vhd_footer()/
    build_vhd_header(): it does not call any of them, even though some of
    the byte layout logic is duplicated here. The point is that a bug
    shared by the builder and its own checksum/offset logic could hide a
    defect if this fixture reused that code; encoding the layout a second
    time, independently, lets this fixture serve as a cross-check on the
    real implementation instead of just a cross-check on the builder.

    Wraps one fixed 512-byte block of literal payload (not a full
    fat12_image) since this fixture exists for structural cross-checking,
    not filesystem content testing.
    """
    block_size = 512
    virtual_size = 512
    header_offset = 512
    table_offset = header_offset + 1024  # 1536
    max_table_entries = 1
    bat_bytes_len = 512  # one sector, rounded up from 4 bytes
    bitmap_len = 512     # one sector, rounded up from 1 bit-per-sector = 1 byte
    block_data_sector = (table_offset + bat_bytes_len) // 512  # sector index of bitmap start
    blocks_start = block_data_sector * 512
    block_region_len = bitmap_len + block_size
    trailing_footer_offset = blocks_start + block_region_len
    file_size = trailing_footer_offset + 512

    unique_id = bytes(range(16, 32))

    def footer(disk_type):
        buf = bytearray(512)
        buf[0:8] = b"conectix"
        struct.pack_into(">I", buf, 8, 2)
        struct.pack_into(">I", buf, 12, 0x00010000)
        struct.pack_into(">Q", buf, 16, header_offset)
        struct.pack_into(">I", buf, 24, 0)
        buf[28:32] = b"wa  "
        struct.pack_into(">I", buf, 32, 0x00010000)
        buf[36:40] = b"Wi2k"
        struct.pack_into(">Q", buf, 40, virtual_size)
        struct.pack_into(">Q", buf, 48, virtual_size)
        buf[56:60] = bytes([0, 16, 63, 63])
        struct.pack_into(">I", buf, 60, disk_type)
        struct.pack_into(">I", buf, 64, 0)
        buf[68:84] = unique_id
        buf[84] = 0
        # checksum is the ones'-complement of the sum of all bytes with
        # the checksum field (offset 64, 4 bytes) treated as zero.
        total = sum(buf[0:64]) + sum(buf[68:512])
        checksum = (~total) & 0xFFFFFFFF
        struct.pack_into(">I", buf, 64, checksum)
        return bytes(buf)

    def header():
        buf = bytearray(1024)
        buf[0:8] = b"cxsparse"
        struct.pack_into(">Q", buf, 8, 0xFFFFFFFFFFFFFFFF)
        struct.pack_into(">Q", buf, 16, table_offset)
        struct.pack_into(">I", buf, 24, 0x00010000)
        struct.pack_into(">I", buf, 28, max_table_entries)
        struct.pack_into(">I", buf, 32, block_size)
        struct.pack_into(">I", buf, 36, 0)
        buf[40:56] = bytes(16)
        struct.pack_into(">I", buf, 56, 0)
        buf[60:64] = bytes(4)
        buf[64:576] = bytes(512)
        buf[576:768] = bytes(192)
        buf[768:1024] = bytes(256)
        total = sum(buf[0:36]) + sum(buf[40:1024])
        checksum = (~total) & 0xFFFFFFFF
        struct.pack_into(">I", buf, 36, checksum)
        return bytes(buf)

    bat = bytearray(bat_bytes_len)
    struct.pack_into(">I", bat, 0, block_data_sector)

    bitmap = bytearray(bitmap_len)
    bitmap[0] = 0x80  # single sector in this block, marked used

    payload = (b"HANDENCODEDSAMPLE" * 32)[:block_size]

    out = bytearray()
    out += footer(3)       # leading footer
    out += header()
    out += bytes(bat)
    out += bytes(bitmap)
    out += payload
    out += footer(3)       # trailing footer
    assert len(out) == file_size
    return bytes(out)


def generate_mutation_iterations(seed=0xD5D7A5, count=100):
    """Corruption_Generator (requirements.md glossary): produce `count`
    deterministic single-mutation iterations against a known-valid
    synthetic dynamic VHD (dynamic_vhd_image()'s default output), each
    pre-classified at generation time -- never by parsing or by calling
    legacyrescue.image.load() -- as "valid" or "invalid".

    ADOPTED POLICY (do not re-litigate; mirrors how design.md documents
    validate_bat_capacity's "exact equality" policy):

    1. Exactly two mutation categories are produced, nothing else:

       - "valid": the mutation touches ONLY a reserved/padding byte or
         bit that NO validate_* function in legacyrescue/image.py
         inspects: a footer's `reserved` bytes (offsets 85-511), a
         header's `reserved1` (60-63), `parentUniqueId` (40-55),
         `parentTimestamp` (56-59), `parentUnicodeName` (64-575),
         `parentLocatorEntries` (576-767), or `reserved2` (768-1023)
         bytes, a BAT padding byte beyond `max_table_entries * 4` but
         within the sector-rounded BAT region, or a bitmap padding bit
         beyond `sectors_per_block` but within the sector-rounded
         bitmap region. If the mutated byte falls within a
         checksum-covered region (footer or header), the checksum is
         recomputed and repaired after the mutation so the footer/header
         remains individually valid and internally self-consistent --
         a stale checksum is never left behind, because that would make
         the iteration's outcome ambiguous between "a harmless reserved
         byte changed" and "the checksum is now wrong".

       - "invalid": the mutation touches a field that at least one
         validate_* function actually checks: a footer's cookie,
         diskType, or checksum field itself; or a header's cookie,
         checksum, headerVersion, tableOffset, maxTableEntries, or
         blockSize. This is expected to raise ValueError deterministically
         when the mutated VHD is loaded.

    2. Allocated block data bytes and bitmap bits that are actually
       consumed (i.e. bits for sectors < sectors_per_block) are NEVER
       mutated. Changing either can still produce a structurally VALID
       dynamic VHD -- the parser has no way to know the "right" content,
       so accepting changed content there is correct parser behavior,
       not a bug -- but this generator cannot know in advance, without
       itself parsing/re-deriving an expectation (which design.md's
       Property 11 disallows), whether such a change still round-trips
       to the baseline's own known flat bytes. Block-data bytes and
       consumed bitmap bits are therefore excluded from the mutation
       target pool entirely.

    3. Unused BAT/bitmap padding bytes and bits ARE valid "valid"-category
       targets (per point 1), since no validator inspects them and
       mutating them can never change the reconstructed flat bytes.

    4. Every iteration has exactly one unambiguous expected outcome,
       known by construction, never by running/parsing the implementation:
       "valid" iterations never touch data/bitmap bits, so their expected
       flat bytes are always identical to the baseline's own known flat
       bytes (fixtures.fat12_image()'s output, padded with zeros to the
       virtual size by dynamic_vhd_image() -- the padding itself is also
       never touched by a "valid" mutation, as it falls past the
       fat12_image payload length but is still copied from an allocated,
       fully-used block, not from a padding/reserved region). No
       iteration is ever "either outcome is acceptable".

    Uses only random.Random(seed) (standard library, fixed seed) to pick,
    per iteration: the category, the specific target, and the mutated
    value.

    Returns a list of exactly `count` dicts, each with keys:
      "data": bytes               -- the complete mutated VHD
      "expected": "valid" | "invalid"
      "target": str                -- human-readable target description
      "expected_flat_bytes": bytes | None
                                    -- present (not None) only when
                                       expected == "valid"; always equal
                                       to the baseline's own flat bytes
    """
    rng = random.Random(seed)

    baseline = dynamic_vhd_image()
    payload = fat12_image()
    block_size = 512
    num_blocks = -(-len(payload) // block_size)
    virtual_size = block_size * num_blocks
    expected_flat_bytes = payload + b"\x00" * (virtual_size - len(payload))

    header_offset = 512
    table_offset = header_offset + 1024
    max_table_entries = num_blocks
    bat_bytes_len = -(-max_table_entries * 4 // 512) * 512
    sectors_per_block = -(-block_size // 512)
    bitmap_bytes_used = -(-sectors_per_block // 8)
    bitmap_sectors = max(1, -(-bitmap_bytes_used // 512))
    bitmap_len = bitmap_sectors * 512

    leading_footer_start = 0
    trailing_footer_start = len(baseline) - 512

    def repair_footer_checksum(buf, start):
        checksum = vhd_checksum(bytes(buf[start:start + 512]), 64)
        struct.pack_into(">I", buf, start + 64, checksum)

    def repair_header_checksum(buf, start):
        checksum = vhd_checksum(bytes(buf[start:start + 1024]), 36)
        struct.pack_into(">I", buf, start + 36, checksum)

    # "valid" targets: (region_start, region_len, is_footer, is_header)
    # offsets are relative to the region start.
    footer_reserved_relative_offsets = list(range(85, 512))
    header_reserved_relative_offsets = (
        list(range(40, 56)) +    # parentUniqueId
        list(range(56, 60)) +    # parentTimestamp
        list(range(60, 64)) +    # reserved1
        list(range(64, 576)) +   # parentUnicodeName
        list(range(576, 768)) +  # parentLocatorEntries
        list(range(768, 1024))   # reserved2
    )
    bat_padding_relative_offsets = list(range(max_table_entries * 4, bat_bytes_len))

    # Bitmap padding bits: bit positions >= sectors_per_block, within
    # the sector-rounded bitmap region, for the first allocated block
    # (block 0), whose bitmap lives at [blocks_start, blocks_start + bitmap_len).
    blocks_start = table_offset + bat_bytes_len
    bitmap_padding_bit_positions = list(range(sectors_per_block, bitmap_len * 8))

    def mutate_valid(buf):
        choices = []
        choices.append("footer_reserved")
        choices.append("header_reserved")
        if bat_padding_relative_offsets:
            choices.append("bat_padding")
        if bitmap_padding_bit_positions:
            choices.append("bitmap_padding")
        kind = rng.choice(choices)

        if kind == "footer_reserved":
            footer_start = rng.choice([leading_footer_start, trailing_footer_start])
            rel = rng.choice(footer_reserved_relative_offsets)
            # XOR with a random nonzero delta in [1, 255] instead of
            # assigning a plain new value, so the byte is guaranteed to
            # actually change (never a same-value no-op).
            buf[footer_start + rel] ^= rng.randrange(1, 256)
            repair_footer_checksum(buf, footer_start)
            target = "footer(%d).reserved[%d]" % (footer_start, rel)
        elif kind == "header_reserved":
            rel = rng.choice(header_reserved_relative_offsets)
            buf[header_offset + rel] ^= rng.randrange(1, 256)
            repair_header_checksum(buf, header_offset)
            target = "header.reserved_region[%d]" % (rel,)
        elif kind == "bat_padding":
            rel = rng.choice(bat_padding_relative_offsets)
            buf[table_offset + rel] ^= rng.randrange(1, 256)
            # BAT is not checksum-covered; nothing to repair.
            target = "bat_padding[%d]" % (rel,)
        else:  # bitmap_padding
            bit_pos = rng.choice(bitmap_padding_bit_positions)
            byte_index, bit_index = bit_pos // 8, 7 - (bit_pos % 8)
            buf[blocks_start + byte_index] ^= (1 << bit_index)
            # Bitmap is not checksum-covered; nothing to repair.
            target = "bitmap_padding_bit[%d]" % (bit_pos,)
        return target

    def mutate_invalid(buf):
        kind = rng.choice([
            "footer_cookie", "footer_disk_type", "footer_checksum",
            "header_cookie", "header_checksum", "header_version",
            "header_table_offset", "header_max_table_entries", "header_block_size",
        ])
        footer_start = rng.choice([leading_footer_start, trailing_footer_start])

        if kind == "footer_cookie":
            pos = rng.randrange(0, 8)
            buf[footer_start + pos] ^= 0xFF
            target = "footer(%d).cookie[%d]" % (footer_start, pos)
        elif kind == "footer_disk_type":
            cur_disk_type = struct.unpack_from(">I", buf, footer_start + 60)[0]
            candidates = [v for v in (0, 1, 5, 6, 255) if v != cur_disk_type]
            bad = rng.choice(candidates)
            struct.pack_into(">I", buf, footer_start + 60, bad)
            # Deliberately leave the checksum stale OR repair it: either
            # way diskType not in {2,3,4} still raises ValueError in
            # validate_footer_structure, so the outcome stays "invalid"
            # regardless of checksum state. Repair it so the failure is
            # attributable only to diskType, not an incidental checksum
            # mismatch.
            repair_footer_checksum(buf, footer_start)
            target = "footer(%d).diskType" % (footer_start,)
        elif kind == "footer_checksum":
            delta = rng.randrange(1, 0xFFFFFFFF)
            cur = struct.unpack_from(">I", buf, footer_start + 64)[0]
            struct.pack_into(">I", buf, footer_start + 64, (cur ^ (delta | 1)) & 0xFFFFFFFF)
            target = "footer(%d).checksum" % (footer_start,)
        elif kind == "header_cookie":
            pos = rng.randrange(0, 8)
            buf[header_offset + pos] ^= 0xFF
            target = "header.cookie[%d]" % (pos,)
        elif kind == "header_checksum":
            delta = rng.randrange(1, 0xFFFFFFFF)
            cur = struct.unpack_from(">I", buf, header_offset + 36)[0]
            struct.pack_into(">I", buf, header_offset + 36, (cur ^ (delta | 1)) & 0xFFFFFFFF)
            target = "header.checksum"
        elif kind == "header_version":
            cur_version = struct.unpack_from(">I", buf, header_offset + 24)[0]
            candidates = [v for v in (0x0, 0x00010001, 0x00020000, 0xFFFFFFFF) if v != cur_version]
            bad = rng.choice(candidates)
            struct.pack_into(">I", buf, header_offset + 24, bad)
            repair_header_checksum(buf, header_offset)
            target = "header.headerVersion"
        elif kind == "header_table_offset":
            # Break sector-alignment of tableOffset (validate_header_structure).
            # table_offset + 1 is inherently always different from
            # table_offset (adding 1 can never equal the original), so no
            # reroll-if-equal is needed here.
            struct.pack_into(">Q", buf, header_offset + 16, table_offset + 1)
            repair_header_checksum(buf, header_offset)
            target = "header.tableOffset"
        elif kind == "header_max_table_entries":
            # delta is never 0, and the reroll below guards the case where
            # max_table_entries + delta happens to land back on the
            # original value (e.g. a negative delta wrapping via max(0, ...)).
            delta = rng.choice([-1, 1, rng.randrange(2, 100)])
            bad = max(0, max_table_entries + delta)
            if bad == max_table_entries:
                bad = max_table_entries + 1
            struct.pack_into(">I", buf, header_offset + 28, bad)
            repair_header_checksum(buf, header_offset)
            target = "header.maxTableEntries"
        else:  # header_block_size
            cur_block_size = struct.unpack_from(">I", buf, header_offset + 32)[0]
            candidates = [v for v in (0, 3, 7, 1024 * 1024 * 3) if v != cur_block_size]
            bad = rng.choice(candidates)
            struct.pack_into(">I", buf, header_offset + 32, bad)
            repair_header_checksum(buf, header_offset)
            target = "header.blockSize"
        return target

    iterations = []
    for _ in range(count):
        buf = bytearray(baseline)
        is_valid = rng.random() < 0.5
        if is_valid:
            target = mutate_valid(buf)
            iterations.append({
                "data": bytes(buf),
                "expected": "valid",
                "target": target,
                "expected_flat_bytes": expected_flat_bytes,
            })
        else:
            target = mutate_invalid(buf)
            iterations.append({
                "data": bytes(buf),
                "expected": "invalid",
                "target": target,
                "expected_flat_bytes": None,
            })
    return iterations


class ReadRecordingFile:
    """Wraps a real open file object and records every (offset, length)
    pair passed to read, as (current_position, requested_size) at the time
    read() is called.

    Reserved ONLY for the two pre-read-rejection proofs (dynamic-vhd-reader
    spec tasks 16.1 and 16.2) per design.md's Testing Strategy. Every other
    test in that suite exercises legacyrescue.image.load() against a real
    temporary file with no wrapping. Do not use this wrapper elsewhere:
    distinguishing "rejected before a read" from "rejected after a read"
    is the only reason it exists.
    """

    def __init__(self, f):
        self._f = f
        self.reads = []

    def seek(self, offset, whence=0):
        return self._f.seek(offset, whence)

    def read(self, size=-1):
        offset = self._f.tell()
        self.reads.append((offset, size))
        return self._f.read(size)

    def tell(self):
        return self._f.tell()

    def fileno(self):
        return self._f.fileno()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._f.close()
        return False
