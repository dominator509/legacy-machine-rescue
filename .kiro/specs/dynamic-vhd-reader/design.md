# Design Document

## Overview

This document describes the technical design for dynamic VHD reader support in Legacy Machine Rescue. The feature extends `legacyrescue/image.py`'s `load()` function to recognize Microsoft Virtual Hard Disk (VHD) files using the dynamic (sparse) allocation format, reconstruct the flat disk bytes a dynamic VHD represents, and return them in the same `(data, info)` shape `load()` already returns for raw images and fixed VHDs.

The design's guiding principle is **strict isolation of new behavior**: raw-image and fixed-VHD code paths keep their exact current algorithm, I/O pattern, and output, byte-for-byte and field-for-field. All new validation, new bounded reads, and new structure parsing apply only once a file is positively classified as a dynamic VHD (disk type 3). This avoids regressing the two paths that already work and that other modules (`inventory`, `fat`, `runbook`, `report`) depend on.

A second guiding principle is **validate spans before reading, and validate all intervals before allocating the output buffer**. Because the header's and BAT's own file locations are read from the footer and header respectively, the design validates each structure's location is in-bounds and non-overlapping *before* performing the bounded read of that structure's bytes, and validates that every allocated block's interval is disjoint from all other intervals *before* allocating the (potentially large) output buffer.

A third guiding principle, made explicit after two rounds of review, is **validate cheap, attacker-controlled scalars before using them to size any read**. `maxTableEntries` comes straight from the header and is not yet trustworthy at the point it is parsed. It must be validated and capped (`<= 524288`) *before* it is used to compute `bat_bytes_len` or to size the bounded BAT read. No intermediate value derived from an unvalidated header field is ever allowed to size a buffer or a read.

## Architecture

### Current Behavior (unchanged for raw and fixed paths)

```
load(path)
    ├── read entire file into memory (f.read())
    ├── trailing 512 bytes == "conectix" (offset -512..-504) → Fixed VHD: strip footer, return data[:-512]
    ├── leading 8 bytes == "conectix" → raise ValueError("dynamic VHD is not supported yet; convert to raw first")
    ├── leading 4/8 bytes match KDMV / QFI\xfb / "<<< Orac" → raise ValueError("VMDK/QCOW2/VDI images are not supported yet; convert to raw first")
    └── otherwise → raw image, return data unchanged
```

### New Dispatch Precedence (replaces the dynamic-rejection branch only)

The two positions that can hold a "conectix" footer are:
- **Trailing position**: last 512 bytes of the file (`data[-512:]`), the standard VHD footer location for fixed and dynamic VHDs.
- **Leading position**: first 512 bytes of the file (`data[:512]`), the backup footer location used by dynamic (and differencing) VHDs only.

Classification reads only these two 512-byte windows first (see "Bounded I/O" below), never the whole file for classification purposes. The dispatch order is evaluated exactly in this sequence, and **no later rule is reached once an earlier rule matches**:

```mermaid
flowchart TD
    A[Stat file size; read head and tail windows per Bounded I/O rules] --> B{Either position:\ncookie=conectix AND diskType=4?}
    B -- yes --> R1[reject: ValueError\n"Differencing VHD is not supported"]
    B -- no --> C{Either position:\ncookie=conectix AND diskType=3?}
    C -- yes --> D[Dynamic path:\nstrictly validate BOTH footer copies,\nthen header, BAT, bitmaps, blocks]
    C -- no --> E{Trailing position\ncookie=conectix?\n any other/malformed type}
    E -- yes --> F[Historical fixed-footer-strip:\nreturn data-without-last-512, container=fixed VHD\nNO new validation]
    E -- no --> G{Leading position\ncookie=conectix?\n non-dynamic-valid}
    G -- yes --> H[Historical rejection:\nValueError dynamic VHD is not supported yet]
    G -- no --> I{Leading bytes match\nKDMV / QFI / Orac signatures?}
    I -- yes --> J[Historical rejection:\nValueError unsupported format]
    I -- no --> K[Raw image: return data unchanged]
```

This precedence fixes a dispatch-order bug from an earlier draft, which checked "trailing non-3/non-4" *before* checking the leading candidate; that ordering could short-circuit a valid leading dynamic/differencing candidate whenever the trailing bytes happened to look like a malformed-but-cookie-bearing footer. The corrected order always checks **both positions for type 4, then both positions for type 3, before** considering either position's fallback behavior.

#### Bounded I/O for classification (`head` / `tail` windows)

Classification needs two independent 512-byte windows, `head` and `tail`, read as follows. These rules replace an earlier, incorrect draft that aliased `tail` to `head` for files in the 512–1023 byte range; that earlier rule changed legacy fixed-VHD detection behavior for files in that size range and is not used in this design.

```
size = stat(path).st_size

# head: first up to 512 bytes, independent of tail
seek(0)
head = read(min(512, size))

# tail: only defined when the file is at least 512 bytes long
if size >= 512:
    seek(size - 512)
    tail = read(512)
else:
    tail = None   # file too short to have a valid trailing footer window
```

Key points:
- `head` and `tail` are always read via their own `seek`/`read` pair. `tail` is never assigned from `head` or vice versa, even when their underlying byte ranges overlap in the file (which happens whenever `512 <= size < 1024`, since `head` covers `[0, min(512, size))` and `tail` covers `[size-512, size)`; for `512 <= size < 1024` these two ranges overlap but are not identical, and each must independently reflect its own seek position).
- When `size < 512`, there is no valid trailing-footer window at all; `tail` is `None` and the trailing-position cookie check (`tail is not None and tail[:8] == b"conectix"`) is simply false. This matches current behavior: a short file never matches `data[-512:-504] == b"conectix"`, because Python slicing on a too-short `bytes` object yields fewer than 8 bytes and the equality fails.
- All classification comparisons (`conectix` cookie check, disk-type byte check at the appropriate offset) operate on `head` and `tail` as independently-read 512-byte (or shorter, for `head` when `size < 512`) windows.

This fixes the bug in an earlier draft where the branch `elif size >= 512: tail = head` caused `tail` to silently become a copy of the first-512-bytes window instead of the true last-512-bytes window whenever `512 <= size < 1024`. Under the buggy version, a file in that size range with a trailing "conectix" cookie but a non-"conectix" leading region would be misclassified, because `tail` would actually hold `head`'s bytes. The corrected rule above always seeks and reads `tail` from `size - 512`, independent of what `head` contains, for every `size >= 512`.

### Dynamic VHD Parsing Pipeline (new, only reached via step D above)

```mermaid
flowchart TD
    D[Dynamic path entered] --> V1[Validate trailing footer span in-bounds and read it\nValidate leading footer span in-bounds and read it]
    V1 --> V2[Validate both footer copies individually\nchecksum, cookie, structure]
    V2 --> V3[Validate footer copies are consistent with each other:\nDisk Type, Unique ID, Original Size, Current Size, Data Offset all match]
    V3 --> V4[Validate header span H..H+1024 in-bounds,\nnot overlapping either footer, sector-aligned, H >= 512]
    V4 --> V5[Bounded read of header bytes; validate cookie + checksum + structure]
    V5 --> V6[Validate block size: range, power-of-two, sector-aligned]
    V6 --> V7[Validate virtual size: <= 256 MiB cap]
    V7 --> V8[Validate maxTableEntries: exact match to ceil(virtualSize/blockSize),\nand <= 524288 capacity cap]
    V8 --> V9[ONLY NOW compute bat_bytes_len from the validated/capped maxTableEntries;\nvalidate BAT span T..T+bat_bytes_len in-bounds,\nnot overlapping footers or header]
    V9 --> V10[Bounded read of BAT bytes; parse maxTableEntries entries]
    V10 --> V11[Compute each allocated block's interval\nbitmap + data region]
    V11 --> V12[Build full interval list: 2 footers + header + BAT + each allocated block;\nvalidate all end <= fileSize via sort + linear adjacent-pair scan;\nseparately validate every block interval's end <= fileSize - 512]
    V12 --> V13[Allocate output bytearray of virtualSize bytes,\nzero-filled]
    V13 --> V14[For each allocated BAT entry: bounded read of bitmap,\nbounded read of block, copy used sectors into output]
    V14 --> V15[Return bytes(output), info with container=dynamic VHD, size=physical file size, virtual_size=virtualSize]
```

The critical ordering change from an earlier draft is the position of V6–V9 relative to the BAT read. Block size, virtual size, and `maxTableEntries` (including the capacity cap) are now validated strictly **before** `bat_bytes_len` is computed or any BAT bytes are read. `maxTableEntries` is an attacker-controlled 32-bit field taken directly from the header; if it were used to size a read before being capped, a crafted header could cause a read attempt sized by an arbitrary 32-bit value (up to roughly 2^32 * 4 / 512 sectors' worth of implied length) before validation ever rejects it. By capping `maxTableEntries` at 524288 (and requiring exact equality with `ceil(virtualSize/blockSize)`, itself already bounded by the 256 MiB virtual size cap validated in V7) before V9, the design guarantees `bat_bytes_len` is always a small, bounded value by the time it is used to size any read.

## Components and Interfaces

### `legacyrescue/image.py` additions

```
CONST SECTOR_SIZE = 512
CONST HEADER_SIZE = 1024
CONST FOOTER_SIZE = 512
CONST MAX_VIRTUAL_SIZE = 256 * 1024 * 1024        # 256 MiB, dynamic VHD only
CONST MIN_BLOCK_SIZE = 512
CONST MAX_BLOCK_SIZE = 2 * 1024 * 1024            # 2 MiB
CONST MAX_BAT_ENTRIES = 524288                    # capacity cap, independent of virtual size
CONST DISK_TYPE_FIXED = 2
CONST DISK_TYPE_DYNAMIC = 3
CONST DISK_TYPE_DIFFERENCING = 4

FUNCTION load(path) -> (data: bytes, info: dict)
    """
    Entry point. Preserves existing raw/fixed/raw-rejection behavior exactly.
    Adds dynamic VHD dispatch and parsing per the Architecture section above.
    """

FUNCTION read_head_tail_windows(f, size) -> (head: bytes, tail: bytes | None)
    """
    Implements the Bounded I/O rules above. Returns the independently-read
    head window (first min(512, size) bytes) and tail window (last 512
    bytes, or None if size < 512).
    """

FUNCTION classify_footer_candidate(window: bytes) -> dict | None
    """
    Given a 512-byte (or shorter) window, returns a parsed-footer-like dict
    with at least {"cookie_ok": bool, "disk_type": int | None} for dispatch
    purposes, or None if the window is too short to contain a cookie field.
    Does NOT perform full footer validation (checksum etc.) -- that is done
    later, only once the dynamic path is entered.
    """

FUNCTION parse_footer(raw: bytes) -> FooterFields
    """
    Parses all documented VHD footer fields from a 512-byte buffer:
    cookie, features, fileFormatVersion, dataOffset, timestamp,
    creatorApplication, creatorVersion, creatorHostOS, originalSize,
    currentSize, diskGeometry, diskType, checksum, uniqueId,
    savedState, reserved.
    Does not validate; pure struct unpack into a FooterFields value.
    """

FUNCTION validate_footer_checksum(raw: bytes, parsed: FooterFields) -> None
    """
    Recomputes the VHD footer checksum (ones'-complement sum of all bytes
    with the checksum field itself treated as zero) and raises ValueError
    if it does not match parsed.checksum.
    """

FUNCTION validate_footer_structure(parsed: FooterFields) -> None
    """
    Raises ValueError if cookie != b"conectix", or diskType not in
    {2, 3, 4}, or any other structurally-required field is out of its
    documented valid range.
    """

FUNCTION validate_footer_pair_consistent(footer_a: FooterFields, footer_b: FooterFields) -> None
    """
    Raises ValueError unless footer_a and footer_b agree on ALL of:
      - diskType
      - uniqueId
      - originalSize
      - currentSize
      - dataOffset

    In addition to (not instead of) the five-field mutual-agreement check
    above, this function separately requires that footer_a.diskType == 3
    AND footer_b.diskType == 3, individually. Agreement between the two
    copies alone is not sufficient: two footers could in principle agree
    with each other while both reporting some other value (an invalid or
    unrecognized disk type number), and such a pair must still be
    rejected here. Type 4 is already excluded earlier in dispatch and the
    dynamic path is only entered once at least one position shows type 3,
    but this function does not rely on that dispatch history -- it
    re-confirms both copies individually report diskType == 3 as an
    explicit, independent check.

    Checksum equality between the two copies is NOT used as a proxy for
    field agreement: two footers can independently have internally-valid,
    matching checksums while disagreeing on dataOffset (or any other
    field), because each footer's checksum only certifies internal
    self-consistency, not agreement with the other copy. This function
    performs an explicit field-by-field comparison across all five fields
    above regardless of checksum equality.
    """

FUNCTION validate_header_span(header_offset: int, file_size: int, footer_intervals: list) -> None
    """
    Raises ValueError unless:
      - header_offset >= 512
      - header_offset is a multiple of 512 (sector-aligned)
      - header_offset + 1024 <= file_size
      - the interval [header_offset, header_offset + 1024) does not
        overlap either footer's interval
    """

FUNCTION parse_header(raw: bytes) -> HeaderFields
    """
    Parses all documented VHD dynamic header fields from a 1024-byte
    buffer: cookie, dataOffset (0xFFFFFFFFFFFFFFFF sentinel), tableOffset,
    headerVersion, maxTableEntries, blockSize, checksum, parentUniqueId,
    parentTimestamp, parentUnicodeName, parentLocatorEntries, reserved.
    Does not validate; pure struct unpack into a HeaderFields value.
    """

FUNCTION validate_header_checksum(raw: bytes, parsed: HeaderFields) -> None
    """
    Recomputes the VHD header checksum (ones'-complement sum of all bytes
    with the checksum field itself treated as zero) and raises ValueError
    if it does not match parsed.checksum.
    """

FUNCTION validate_header_structure(parsed: HeaderFields) -> None
    """
    Raises ValueError if cookie != b"cxsparse", or headerVersion is not the
    expected value, or tableOffset is not sector-aligned and >= 512.
    """

FUNCTION validate_block_size(block_size: int) -> None
    """
    Raises ValueError unless:
      - MIN_BLOCK_SIZE <= block_size <= MAX_BLOCK_SIZE
      - block_size is a power of two
      - block_size is a multiple of SECTOR_SIZE
    This check is independent of virtual size and BAT capacity -- it is a
    pure property of the block_size field's own value.
    """

FUNCTION validate_virtual_size(virtual_size: int) -> None
    """
    Raises ValueError unless 0 < virtual_size <= MAX_VIRTUAL_SIZE AND
    virtual_size % SECTOR_SIZE == 0 (sector-aligned). Exactly
    MAX_VIRTUAL_SIZE (268,435,456 bytes) is allowed; 268,435,456 is itself
    a multiple of 512, so the boundary case satisfies both checks.
    A virtual_size that is in range but not a multiple of 512 (e.g. it
    would imply a final partial sector) is rejected, independent of the
    range check.
    """

FUNCTION validate_bat_capacity(max_table_entries: int, virtual_size: int, block_size: int) -> None
    """
    Raises ValueError unless:
      max_table_entries == ceil(virtual_size / block_size)   (EXACT match)
      AND max_table_entries <= MAX_BAT_ENTRIES

    Policy note: this design deliberately requires EXACT equality between
    max_table_entries and ceil(virtual_size / block_size), not merely
    ">=". A header claiming more BAT entries than the virtual size implies
    is rejected, not silently truncated or tolerated. This is the final,
    adopted policy for this feature; it is stated here explicitly so that
    future work does not re-litigate "exact" vs "at least" -- exact
    equality is simpler to reason about, removes an entire class of
    "extra declared-but-unused BAT entries" ambiguity, and is sufficient
    to satisfy every acceptance criterion in the requirements document.

    Must be called, and must pass, strictly before bat_bytes_len is
    computed or any BAT bytes are read (see Architecture, V6-V9).
    """

FUNCTION compute_bat_bytes_len(max_table_entries: int) -> int
    """
    Returns ceil(max_table_entries * 4 / 512) * 512, i.e. the BAT region
    rounded up to a whole number of sectors.
    PRECONDITION (enforced by caller ordering, not re-checked here):
    max_table_entries has already passed validate_bat_capacity, so this
    value is bounded by ceil(524288 * 4 / 512) * 512 bytes. Computed
    directly: 524288 * 4 = 2,097,152 bytes exactly (2 MiB), and since
    2,097,152 is already an exact multiple of 512 (2,097,152 / 512 =
    4,096 exactly), the sector-rounding in ceil(...) * 512 does not add
    anything further -- the bound is exactly 2,097,152 bytes (2 MiB), not
    4,194,304 bytes. This function must never be called with an
    unvalidated max_table_entries.
    """

FUNCTION validate_bat_span(table_offset: int, bat_bytes_len: int, file_size: int, prior_intervals: list) -> None
    """
    Raises ValueError unless:
      - table_offset >= 512
      - table_offset + bat_bytes_len <= file_size
      - the interval [table_offset, table_offset + bat_bytes_len) does not
        overlap any interval in prior_intervals (both footers, header)
    """

FUNCTION parse_bat(raw: bytes, max_table_entries: int) -> list[int]
    """
    Parses exactly max_table_entries big-endian uint32 sector offsets from
    raw. A value of 0xFFFFFFFF marks the entry as unallocated (sparse).
    Loops exactly max_table_entries times -- this count is, by this point
    in the pipeline, already validated to exactly equal
    ceil(virtual_size / block_size), so no separately-computed entry count
    is used anywhere else in the pipeline.
    """

FUNCTION build_metadata_intervals(leading_footer_present: bool, file_size: int, header_offset: int, table_offset: int, bat_bytes_len: int) -> list[(int, int)]
    """
    Returns a list of (start, end) half-open intervals -- i.e. each tuple
    means the byte range [start, end), NOT (start, length) -- for:
      - the trailing footer: (file_size - 512, file_size)
      - the leading footer, if leading_footer_present: (0, 512)
      - the header: (header_offset, header_offset + 1024)
      - the BAT: (table_offset, table_offset + bat_bytes_len)
    Every tuple returned is unconditionally (start, end).
    """

FUNCTION build_block_intervals(bat: list[int], block_size: int) -> list[(int, int)]
    """
    For each allocated entry in bat (value != 0xFFFFFFFF), computes the
    interval occupied by that entry's sector bitmap plus data block and
    returns it as a (start, end) half-open tuple, where:
      bitmap_sectors = ceil(block_size / SECTOR_SIZE / 8 / SECTOR_SIZE) sectors
                       rounded up to a whole sector (at least 1 sector)
      start = bat_entry_value * SECTOR_SIZE
      end   = start + (bitmap_sectors * SECTOR_SIZE) + block_size
    Returns one (start, end) tuple per allocated entry, in BAT order (not
    yet sorted). Unallocated entries (0xFFFFFFFF) contribute no interval.
    All tuples returned are (start, end), consistent with
    build_metadata_intervals.
    """

FUNCTION validate_disjoint_sorted(intervals: list[(int, int)], file_size: int) -> None
    """
    Given a list of (start, end) half-open intervals (ALL intervals: both
    footers, header, BAT, and every allocated block), sorts them by start
    and performs a single linear adjacent-pair scan, raising ValueError if
    any of the following hold for any interval (start, end) in the list:
      - start < 0
      - end > file_size          <-- bound is the FULL file size, not
                                      file_size - 512. The trailing
                                      footer's own interval legitimately
                                      has end == file_size, so using
                                      file_size - 512 as the bound here
                                      would incorrectly reject every valid
                                      dynamic VHD (the trailing footer
                                      interval would always appear to
                                      exceed the limit). This function
                                      checks only the universal
                                      "in-bounds for the whole file" rule;
                                      it does not encode the
                                      blocks-cannot-reach-the-footer rule.
      - any two adjacent (after sort) intervals overlap, i.e. for sorted
        intervals i and i+1: intervals[i].end > intervals[i+1].start
    Runs in O(N log N) due to the sort; the scan itself is O(N).
    """

FUNCTION validate_blocks_do_not_reach_footer(block_intervals: list[(int, int)], file_size: int) -> None
    """
    Separate, additional check applied ONLY to block_intervals (not to the
    footer intervals, header interval, or BAT interval). Raises ValueError
    if any block interval (start, end) has end > file_size - 512, i.e. if
    any allocated block's bitmap+data region would extend into or past the
    trailing 512-byte footer region. This rule exists because, unlike the
    header, BAT, and leading footer (which the format allows to be placed
    anywhere earlier in the file, subject to the general disjointness
    check above), a data block is never permitted to encroach on the
    trailing footer -- doing so would mean the "last 512 bytes" read used
    for classification and footer parsing could actually be live block
    data, which the format does not allow.
    This check is independent of, and in addition to, validate_disjoint_sorted.
    """

FUNCTION read_bitmap_and_block(f, bat_entry_sectors: int, block_size: int) -> (bitmap: bytes, block: bytes)
    """
    Bounded read: seeks to bat_entry_sectors * SECTOR_SIZE, reads the
    sector bitmap (rounded up to a whole sector), then reads block_size
    bytes immediately following the bitmap.
    """

FUNCTION reconstruct_flat_bytes(virtual_size: int, block_size: int, bat: list[int], f) -> bytes
    """
    Allocates a zero-filled bytearray of virtual_size bytes. For each of
    the (already-validated-count) BAT entries, if allocated, reads its
    bitmap and block via read_bitmap_and_block and copies each sector
    whose bitmap bit is set into the corresponding offset in the output
    buffer. Returns bytes(output).
    """
```

### Interaction with existing modules

`inventory.py`, `fat.py`, `runbook.py`, and `report.py` all consume `load()`'s `(data, info)` return value and never inspect `info["container"]` to change their own parsing logic (confirmed by reading their current source); they operate purely on `data`. Because dynamic VHD reconstruction produces the same flat disk byte layout as a raw image or stripped fixed VHD, no changes are required in any of these modules.

## Data Models

### FooterFields

| Field | Type | Offset | Size | Notes |
|---|---|---|---|---|
| cookie | bytes | 0 | 8 | must equal `b"conectix"` |
| features | uint32 (BE) | 8 | 4 | |
| fileFormatVersion | uint32 (BE) | 12 | 4 | |
| dataOffset | uint64 (BE) | 16 | 8 | absolute offset of VHD header for dynamic/differencing; `0xFFFFFFFFFFFFFFFF` for fixed |
| timestamp | uint32 (BE) | 24 | 4 | |
| creatorApplication | bytes | 28 | 4 | |
| creatorVersion | uint32 (BE) | 32 | 4 | |
| creatorHostOS | bytes | 36 | 4 | |
| originalSize | uint64 (BE) | 40 | 8 | |
| currentSize | uint64 (BE) | 48 | 8 | |
| diskGeometry | bytes | 56 | 4 | |
| diskType | uint32 (BE) | 60 | 4 | 2=fixed, 3=dynamic, 4=differencing |
| checksum | uint32 (BE) | 64 | 4 | ones'-complement sum of all other bytes |
| uniqueId | bytes | 68 | 16 | |
| savedState | uint8 | 84 | 1 | |
| reserved | bytes | 85 | 427 | |

### HeaderFields

| Field | Type | Offset | Size | Notes |
|---|---|---|---|---|
| cookie | bytes | 0 | 8 | must equal `b"cxsparse"` |
| dataOffset | uint64 (BE) | 8 | 8 | reserved, `0xFFFFFFFFFFFFFFFF` |
| tableOffset | uint64 (BE) | 16 | 8 | absolute file offset of the BAT |
| headerVersion | uint32 (BE) | 24 | 4 | |
| maxTableEntries | uint32 (BE) | 28 | 4 | number of BAT entries |
| blockSize | uint32 (BE) | 32 | 4 | bytes per data block, excluding bitmap |
| checksum | uint32 (BE) | 36 | 4 | ones'-complement sum of all other bytes |
| parentUniqueId | bytes | 40 | 16 | differencing only |
| parentTimestamp | uint32 (BE) | 56 | 4 | differencing only |
| reserved1 | bytes | 60 | 4 | |
| parentUnicodeName | bytes | 64 | 512 | differencing only |
| parentLocatorEntries | bytes | 576 | 192 | differencing only |
| reserved2 | bytes | 768 | 256 | |

### Interval

Represented uniformly throughout this design as a `(start, end)` tuple of integers, meaning the half-open byte range `[start, end)`. This design never represents an interval as `(start, length)`; every function listed in Components and Interfaces that produces or consumes a list of intervals (`build_metadata_intervals`, `build_block_intervals`, `validate_disjoint_sorted`, `validate_bat_span`, `validate_blocks_do_not_reach_footer`) uses `(start, end)` exclusively. This is called out explicitly because an earlier draft of this design mixed `(start, length)` tuples into the same lists as `(start, end)` tuples in places, which silently corrupted the overlap comparison; this design removes that ambiguity by fixing `(start, end)` as the one and only interval representation.

### Info dict additions

| Key | Type | Notes |
|---|---|---|
| container | str | `"dynamic VHD"` for the new path; unchanged values `"raw"` / `"fixed VHD"` for existing paths |
| size | int | physical file size in bytes (unchanged meaning from existing paths) |
| virtual_size | int | present only for `"dynamic VHD"`; the reconstructed flat disk size in bytes |

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Dynamic VHD round-trip reconstruction

*For any* synthetic dynamic VHD built by wrapping a `Fat12_Image`'s bytes in dynamic VHD container format, `load()` on that dynamic VHD SHALL produce flat disk bytes identical to the original `Fat12_Image` bytes.

**Validates: Requirements 1.1, 1.4, 5.2**

### Property 2: Raw and fixed-VHD output preserved for all non-type-3/non-type-4 inputs

*For any* input file where NEITHER the leading 512-byte window NOR the trailing 512-byte window is recognized (per the dispatch rules in Architecture) as having `cookie == b"conectix"` AND `diskType` in `{3, 4}`, `load()` SHALL produce exactly the same `(data, info)` result as the pre-dynamic-VHD-feature implementation: raw images are returned unchanged, fixed VHDs (trailing type 2 or any other trailing-cookie-bearing-but-non-3/non-4 case) have their trailing 512 bytes stripped, and the pre-existing leading-cookie and signature-based rejections are raised exactly as before.

This property is deliberately scoped to exclude inputs where either footer position is recognized as type 3 or type 4. For those inputs, the new dispatch (dynamic-path parsing for type 3, or explicit differencing rejection for type 4) is the correct and intended behavior introduced by this feature, and it supersedes whatever the pre-feature code did with such inputs. In particular, before this feature existed, any file with `cookie == b"conectix"` in the leading window was unconditionally rejected with "dynamic VHD is not supported yet" regardless of its actual disk type; that blanket rejection is not a correctness guarantee this feature preserves for type-3/type-4 inputs, it is simply the old code's limitation, and routing type-3 inputs to the new dynamic-reconstruction path instead of rejecting them is precisely the feature being added. This property makes no claim, hedge, or apology about type-3/type-4 inputs one way or the other; it is scoped purely to the complementary set of inputs where behavior is unchanged.

**Validates: Requirements 1.2, 1.3, 4.1, 4.2**

### Property 3: Sparse sectors are exactly zero

*For any* dynamic VHD and any sector whose bitmap bit is 0, the corresponding 512-byte range in the reconstructed flat disk bytes SHALL be exactly 512 zero bytes (`b"\x00" * 512`).

**Validates: Requirements 3.1, 3.3**

### Property 4: Used sectors are copied verbatim

*For any* dynamic VHD and any sector whose bitmap bit is 1, the corresponding 512-byte range in the reconstructed flat disk bytes SHALL be byte-for-byte identical to the 512 bytes stored in that sector's position within the data block.

**Validates: Requirements 3.2**

### Property 5: Dispatch precedence is total and ordered

*For any* input file, exactly one branch of the dispatch sequence (type-4 rejection, type-3 dynamic path, trailing-fallback fixed-strip, leading-fallback rejection, signature rejection, raw) SHALL be taken, evaluated in that fixed order, such that an earlier-listed branch's match always prevents any later branch from being evaluated.

**Validates: Requirements 1.2, 1.3, 2.1**

### Property 6: Invalid footer/header is always rejected before use

*For any* dynamic VHD candidate whose trailing or leading footer fails checksum or structural validation, or whose header fails checksum or structural validation, `load()` SHALL raise `ValueError` and SHALL NOT attempt to read any BAT, bitmap, or block bytes.

**Validates: Requirements 2.1, 2.2**

### Property 7: Block size validation is independent and total

*For any* integer value presented as `blockSize`, `validate_block_size` SHALL raise `ValueError` if and only if the value is outside `[512, 2097152]`, is not a power of two, or is not a multiple of 512; otherwise it SHALL accept the value regardless of virtual size or BAT capacity.

**Validates: Requirements 2.3**

### Property 8: Virtual size cap is enforced exactly at the boundary

*For any* `virtualSize` value, `validate_virtual_size` SHALL accept values in `(0, 268435456]` that are also a multiple of `512` (sector-aligned), and SHALL raise `ValueError` for any value `> 268435456`, `<= 0`, or not a multiple of `512`; a `virtualSize` of exactly `268435456` SHALL always be accepted (subject to other independent checks), since `268435456 % 512 == 0`.

**Validates: Requirements 2.4**

### Property 9: BAT capacity is exact and bounded before any BAT read

*For any* header fields `(maxTableEntries, virtualSize, blockSize)`, `validate_bat_capacity` SHALL raise `ValueError` unless `maxTableEntries` is exactly equal to `ceil(virtualSize / blockSize)` AND `maxTableEntries <= 524288`; and SHALL do so before `bat_bytes_len` is computed or any BAT byte is read, so that no file whose claimed `maxTableEntries` would require reading beyond the capped BAT region ever causes a read into or past that region.

**Validates: Requirements 2.5, 2.6**

### Property 10: Footer-pair field consistency is enforced across all five identity fields, and both copies must individually be type 3

*For any* two footer copies (leading and trailing) that are each individually valid (pass checksum and structural validation), `validate_footer_pair_consistent` SHALL raise `ValueError` unless `diskType`, `uniqueId`, `originalSize`, `currentSize`, AND `dataOffset` are all equal between the two copies; equal checksums between the two copies SHALL NOT be treated as sufficient evidence of field agreement, and a mismatch in any one of these five fields alone (even with all others equal) SHALL be sufficient to raise `ValueError`. In addition to (not instead of) this five-field mutual-agreement check, `validate_footer_pair_consistent` SHALL also raise `ValueError` unless BOTH copies individually report `diskType == 3`; two copies that agree with each other but both report some other disk type value (an invalid or unrecognized type number) SHALL still be rejected, even though they satisfy the mutual-agreement check in the abstract.

**Validates: Requirements 2.6**

### Property 11: Mutation robustness with pre-classified expected outcomes

*For* 100 deterministic mutation iterations (seeded standard-library randomization) applied to a known-valid synthetic dynamic VHD, where each iteration is pre-classified by the test generator, at mutation-construction time, as either "expected valid" (the mutation does not change any field relied on for validation or reconstruction correctness, e.g. an unused reserved/padding byte that this design's validation functions do not check) or "expected invalid" (the mutation corrupts a field that at least one of this design's validation functions is known to check), `load()` SHALL produce the SPECIFIC pre-classified outcome for that iteration: successful reconstruction producing correct flat disk bytes for "expected valid" iterations, or `ValueError` for "expected invalid" iterations. `load()` SHALL never raise an exception other than `ValueError`, and SHALL never hang, for any of the 100 iterations.

Every one of the 100 iterations SHALL have exactly one specific, asserted expected outcome determined at generation time; there is no "either outcome is acceptable" fallback for any iteration. This is a strict requirement, not a default that admits exceptions: the test generator's mutation set SHALL be constructed so that every mutation's target field and expected outcome are deterministically knowable before the mutated VHD is passed to `load()`. The only classification vocabulary permitted is: a mutation targeting a reserved/padding byte that no validation function in this design inspects is classified "expected valid"; a mutation targeting any field that a validation function in this design does check is classified "expected invalid." No iteration may be constructed by mutating multiple fields together in a way that leaves the combined outcome unknown at generation time -- if a mutation would touch more than one field, the generator must either resolve the combination to one specific, computable expected outcome (by reasoning about which validation check fires first per this design's documented validation order) or avoid generating that combination.

**Validates: Requirements 5.3, 5.4, 6.2**

### Property 12: All intervals are disjoint and in-bounds; blocks additionally never reach the trailing footer

*For any* dynamic VHD that passes all prior validation steps, the full set of intervals (both footer copies if a leading one is present, the header, the BAT, and every allocated block's bitmap+data region) SHALL satisfy: every interval's `end <= file_size`, and no two intervals overlap. Additionally and separately, every allocated block's bitmap+data interval SHALL satisfy `end <= file_size - 512` (blocks never extend into or past the trailing footer region), a stricter bound that applies only to block intervals and not to the trailing footer interval itself.

**Validates: Requirements 2.5**

## Error Handling

All error conditions raise `ValueError` with a descriptive message, per the existing `load()` contract and the `tech.md` steering rule that parsing code must never crash on damaged input.

| Condition | Message (example) |
|---|---|
| Trailing footer fails checksum (dynamic candidate) | `"dynamic VHD: trailing footer checksum mismatch"` |
| Leading footer fails checksum (dynamic candidate) | `"dynamic VHD: leading footer checksum mismatch"` |
| Footer cookie wrong on a recognized dynamic/differencing candidate | `"dynamic VHD: invalid footer cookie"` |
| Disk type is 4 (differencing) at either position | `"differencing VHD is not supported"` |
| Footer pair disagrees on diskType/uniqueId/originalSize/currentSize/dataOffset | `"dynamic VHD: leading and trailing footer copies are inconsistent"` |
| Header span out of bounds or overlapping a footer | `"dynamic VHD: header location is invalid"` |
| Header checksum mismatch | `"dynamic VHD: header checksum mismatch"` |
| Header cookie wrong | `"dynamic VHD: invalid header cookie"` |
| Block size out of range / not power of two / not sector-aligned | `"dynamic VHD: invalid block size"` |
| Virtual size exceeds 256 MiB or is non-positive | `"dynamic VHD: virtual size exceeds supported maximum"` |
| Virtual size is not a multiple of 512 (not sector-aligned) | `"dynamic VHD: virtual size is not sector-aligned"` |
| `maxTableEntries` does not exactly equal `ceil(virtualSize/blockSize)`, or exceeds 524288 | `"dynamic VHD: BAT entry count is invalid"` |
| BAT span out of bounds or overlapping footer/header | `"dynamic VHD: BAT location is invalid"` |
| Any interval exceeds file size or overlaps another | `"dynamic VHD: overlapping or out-of-bounds structure detected"` |
| A block interval reaches into or past the trailing footer | `"dynamic VHD: data block overlaps trailing footer"` |
| BAT entry offset/alignment invalid | `"dynamic VHD: invalid block allocation table entry"` |

Raw detection and fixed-VHD stripping raise no new errors beyond what the current implementation already raises (none, for those two paths); the differencing-VHD and unsupported-signature messages are unchanged from the current implementation's wording except where this document's dispatch reordering changes which branch is reached first.

## Memory Behavior

Peak memory usage for the dynamic VHD path has three additive components, stated here together so no conflicting figure appears elsewhere in this document:

1. **Output buffer, approximately 2x virtual size**: once for the zero-filled output `bytearray` (up to 256 MiB, per the virtual size cap), and transiently for whatever portion of the file has been read into `bytes` objects for bounded structure reads (footers, header, BAT, and one bitmap+block at a time during reconstruction, never the whole file at once for the dynamic path). The BAT read itself is bounded to at most `524288 * 4 = 2,097,152` bytes (2 MiB) per `compute_bat_bytes_len`'s precondition -- not 4,194,304 bytes.
2. **BAT entry list and interval list overhead, on the order of tens of megabytes at the maximum BAT capacity**: the BAT entry list and the interval lists are bounded by `MAX_BAT_ENTRIES` (524288) entries/intervals, but each is a full Python object, not a packed C array. On CPython, a list of 524288 boxed `int` objects (values generally too large to hit the small-int cache) costs roughly 28 bytes of object overhead per `int` plus 8 bytes per list slot pointer, and a list of roughly 524288 `(start, end)` 2-tuples costs roughly 56-64 bytes per tuple (tuple header plus two pointers) plus 8 bytes per list slot. At the 524288-entry bound this is on the order of `524288 * (36 + 64) ≈ 52 MiB` -- an approximate order-of-magnitude figure, not a precise guarantee -- which is "tens of megabytes," not "a few megabytes."
3. **Bounded transient buffers**: one bitmap and one block read at a time during reconstruction (at most `MAX_BLOCK_SIZE` plus one sector per allocated entry, never accumulated).

Combined, the worst-case peak for the dynamic VHD path is approximately **(2 x virtual size) + (tens of MiB of list/tuple overhead) + (bounded bitmap/block transient buffers)**. The disjointness check (`validate_disjoint_sorted`) is O(N log N) in the number of intervals due to the sort, which is negligible in time cost at this bound and does not materially add to the memory figure above. Raw and fixed-VHD paths are unchanged and continue to read the entire file into memory at once, as today.

## Testing Strategy

**Dual testing approach**: unit tests for specific examples, edge cases, and error conditions; property-based tests (using only Python standard library `random` with a fixed seed, per the no-external-dependencies constraint) for the universal properties listed above. Both are run via `python -m unittest` per the `tech.md` steering rule.

**Scope of the read-recording wrapper (narrow, explicit exception)**: every test in this suite, unit and property-based alike, exercises the feature through the public `legacyrescue.image.load(path)` API against a real file written to a temporary directory (using `tempfile`), never by calling internal helper functions directly -- with exactly one narrow exception. The read-recording file-like wrapper described below (under the oversized-BAT-claim test) is reserved ONLY for tests that must prove a rejection happens strictly before a specific read occurs -- a "pre-read-rejection proof." The two tests in this design that need such a proof are the oversized-BAT-claim test and its Property 9 equivalent (confirming no BAT-region read occurs before `validate_bat_capacity` rejects an invalid `maxTableEntries`). Every other test in the suite -- all remaining unit tests and all remaining property-based tests, including Properties 1 through 8, 10, 11, and 12 -- uses a real temporary file and calls `legacyrescue.image.load(path)` exclusively; it never uses the read-recording wrapper and never calls internal helpers or mocks file I/O. This ensures the overwhelming majority of tests validate actual end-to-end behavior including real `seek`/`read` boundary conditions, while the two pre-read-rejection proofs use the wrapper strictly because no observable-from-the-outside assertion (such as "`load()` raised `ValueError`") can, by itself, distinguish "rejected before reading the BAT" from "rejected after reading the BAT" -- that distinction requires recording what reads actually occurred.

### Unit tests (representative examples and edge cases)

- Raw image (no recognizable signature) loads unchanged (existing behavior, regression-protected).
- Fixed VHD (trailing type 2 footer) loads with footer stripped (existing behavior, regression-protected).
- **Regression test for Bug 1 (classification window fix)**: a file of size in the 512–1023 byte (inclusive) range whose trailing 512 bytes contain a valid `"conectix"` cookie (and whose leading bytes do NOT contain a `"conectix"` cookie) is correctly classified as a fixed VHD (or correctly routed per disk type if the trailing footer's disk type is 3/4) -- i.e. the trailing cookie is detected using the true last-512-bytes window, not a window aliased to the first-512-bytes window. This exercises exactly the file-size range where the old buggy `elif size >= 512: tail = head` branch would have produced incorrect classification.
- **Fixed-vs-dynamic disambiguation test**: both footer copies (leading and trailing) individually valid and reporting `diskType == 2` (fixed). Since `diskType == 2` is neither 3 nor 4, this input must NOT enter the dynamic path; it must be classified using the historical trailing-footer-strip rule (Property 5's precedence), producing `container == "fixed VHD"` with the trailing 512 bytes stripped, exactly as today. This confirms a leading backup-footer-shaped region containing a type-2 footer does not accidentally trigger dynamic-path or rejection logic.
- Leading `"conectix"` cookie with disk type 4 at either position is rejected with the differencing-VHD error.
- A minimal valid synthetic dynamic VHD (one allocated block, rest sparse) round-trips correctly through `load()`.
- A synthetic dynamic VHD with zero allocated blocks (fully sparse) reconstructs to all-zero flat bytes of the correct virtual size.
- **Oversized-BAT-claim test (exercises Bug 3's fix)**: a dynamic VHD candidate whose header declares a `maxTableEntries` value implying a `batLen` that would exceed the file's actual size (e.g. `maxTableEntries` large enough that `compute_bat_bytes_len` would exceed `file_size`) must raise `ValueError` during header-derived validation, strictly before any attempt to read BAT bytes. This is verified using a file-like wrapper object that records every `(offset, length)` pair passed to its `read`/`seek` methods; the test asserts that no recorded read ever targets an offset/length combination that falls within the claimed BAT region, confirming validation rejects the file before `parse_bat` (or any BAT-region read) is reached.
- **Footer-pair dataOffset-mismatch test (exercises Bug 2's fix)**: two footer copies constructed so that their checksums are each independently valid and equal to each other, both report `diskType == 3`, and `uniqueId`/`originalSize`/`currentSize` all agree, but `dataOffset` differs between the two copies. `validate_footer_pair_consistent` must raise `ValueError`. This specifically confirms checksum equality between the two copies is never treated as implying full field agreement.
- Block size values that are zero, negative, not a power of two, not sector-aligned, and at the 2 MiB upper boundary (2,097,152 accepted, 2,097,153 rejected) are each individually tested against `validate_block_size`.
- **Single explicit maximum-virtual-size boundary case**: a synthetic dynamic VHD with `virtualSize` of exactly 268,435,456 bytes (256 MiB) is constructed as one specific, non-randomized unit test and confirmed to load successfully (subject to its other fields being valid). This is deliberately a single concrete boundary test, not part of the randomized property iterations below.
- `maxTableEntries` that is one less than, and one more than, the exact `ceil(virtualSize/blockSize)` value are each tested and confirmed to raise `ValueError` (confirms exact-equality policy, not `>=`).
- A BAT entry whose implied block interval overlaps the header is rejected.
- A BAT entry whose implied block interval would extend past `file_size - 512` (into the trailing footer region) is rejected by `validate_blocks_do_not_reach_footer`, even though its `end` does not exceed `file_size` itself (confirms Bug 4's fix distinguishes the two checks).
- A header offset that is not sector-aligned is rejected.
- Corrupted footer checksum, corrupted header checksum, and corrupted BAT entry alignment are each individually tested and confirmed to raise `ValueError`.

### Property-based tests

- **Property 1** (round-trip reconstruction): generated using a modest number of sensible generated cases -- varying block size (from the valid set), virtual size, and allocated/sparse block patterns across a small number of representative synthetic `Fat12_Image`-wrapping dynamic VHDs (not a fixed large per-property iteration count; enough cases to cover a handful of block-size and sparsity-pattern combinations).
- **Property 2** (raw/fixed preservation for non-3/non-4 inputs): generated using a modest number of sensible generated cases covering raw images, fixed VHDs, and the historical rejection paths, confirming output is byte-identical to the pre-feature implementation's output for each generated case.
- **Property 3 / Property 4** (sparse-zero and used-sector-copy): generated using a modest number of sensible generated cases varying which sectors within a block are marked used vs sparse.
- **Property 5** (dispatch precedence): generated using a modest number of sensible generated cases covering every combination of {type 2, type 3, type 4, malformed-cookie, no-cookie} at each of the leading and trailing positions.
- **Property 6** (invalid footer/header always rejected before use): generated using a modest number of sensible generated cases corrupting one field at a time across footer and header.
- **Property 7** (block size validation independent and total): generated using a modest number of sensible generated cases spanning in-range, out-of-range, non-power-of-two, and non-sector-aligned values.
- **Property 8** (virtual size cap boundary): generated using a modest number of sensible generated cases near the boundary, plus the single explicit maximum-size unit test described above (not randomized).
- **Property 9** (BAT capacity exact and bounded before read): generated using a modest number of sensible generated cases, each verified with the read-recording file wrapper described above to confirm no BAT-region read occurs for invalid cases.
- **Property 10** (footer-pair consistency across all five fields): generated using a modest number of sensible generated cases, each independently varying exactly one of the five fields (`diskType`, `uniqueId`, `originalSize`, `currentSize`, `dataOffset`) between the two footer copies while holding the rest equal and each copy's own checksum valid.
- **Property 11** (mutation robustness): exactly 100 total deterministic mutation iterations using a fixed-seed standard-library `random.Random` instance, applied to a known-valid synthetic dynamic VHD. This is the one property in this design that uses a large fixed iteration count; it is not 100 iterations "per property" generally, it is specifically the mutation-fuzz property's own defined scope. Each of the 100 iterations is pre-classified at construction time as "expected valid" or "expected invalid" (see Property 11's statement above), and the test asserts the specific expected outcome for that iteration, not merely "success or ValueError." No iteration is permitted to assert "either outcome is acceptable": the generator picks exactly one target field (or a combination whose combined outcome it can resolve deterministically, per this design's documented validation order) per iteration, classifies the expected outcome as "expected valid" only for reserved/padding bytes this design's validation does not inspect, and as "expected invalid" for any field this design's validation checks, and the test asserts that specific, single expected outcome for every one of the 100 iterations.
- **Property 12** (interval disjointness and block-vs-footer bound): generated using a modest number of sensible generated cases varying BAT entry placement, including at least one case per generated scenario where a block interval is deliberately placed to end exactly at `file_size - 512` (must pass) and one where it is placed to end at `file_size - 511` (must fail), to directly exercise the boundary this property depends on.

Each property-based test is tagged with a comment referencing its design property, in the format:
**Feature: dynamic-vhd-reader, Property N: {property title}**

No property-based test in this design is specified to run a fixed large number of iterations (such as "100 iterations") except Property 11, which is explicitly and only a 100-iteration mutation-fuzz test by definition of what it is testing. All other properties use a modest, deliberately-unspecified-as-a-large-fixed-number set of generated cases sufficient to cover the input variations called out above.

### Integration-style checks

- `inventory.py`, `fat.py`, `runbook.py`, and `report.py` produce identical output when given the flat bytes from a dynamic VHD versus the same content as a raw image, confirmed via a small number of representative examples (not a property test, since this checks wiring between modules rather than a universal property of the VHD reader itself).
