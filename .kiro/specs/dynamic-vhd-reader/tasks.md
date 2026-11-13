# Implementation Plan: Dynamic VHD Reader

## Overview

This plan implements dynamic VHD support in `legacyrescue/image.py` by adding constants, parsing/validation helper functions, and a new dynamic-VHD reconstruction pipeline, then wiring them into `load()` per the dispatch precedence in design.md. The raw/fixed-VHD/rejection branches of `load()` are preserved verbatim except for the corrected head/tail windowing and dispatch ordering. Test fixtures (synthetic builder, hand-encoded fixture, mutation generator, read-recording wrapper) are added to `tests/fixtures.py`, and all unit/property tests live in a new `tests/test_dynamic_vhd.py`.

Tasks are ordered so constants and pure struct-parsing helpers land first, then independent validation functions (each depending only on already-built parsing helpers), then the interval/disjointness layer, then the bounded-read/reconstruction layer, then `load()` wiring, then tests layered against each prior piece, finishing with the full-suite completion gate.

## Task Dependency Graph

```
1. Constants
   └─> 2. read_head_tail_windows
        └─> 3. classify_footer_candidate
             └─> 4. parse_footer
                  ├─> 5. validate_footer_checksum
                  ├─> 6. validate_footer_structure
                  └─> 7. validate_footer_pair_consistent
                       └─> 8. parse_header
                            ├─> 9. validate_header_span (needs footer intervals from 7/13)
                            ├─> 10. validate_header_checksum
                            ├─> 11. validate_header_structure
                            └─> 12. validate_block_size
                                 └─> 13. validate_virtual_size
                                      └─> 14. validate_bat_capacity
                                           └─> 15. compute_bat_bytes_len
                                                └─> 16. validate_bat_span
                                                     └─> 17. parse_bat
                                                          ├─> 18. build_metadata_intervals
                                                          └─> 19. build_block_intervals
                                                               ├─> 20. validate_disjoint_sorted
                                                               └─> 21. validate_blocks_do_not_reach_footer
                                                                    └─> 22. read_bitmap_and_block
                                                                         └─> 23. reconstruct_flat_bytes
                                                                              └─> 24. load() dispatch + wiring

Test fixtures (parallel track, needed before corresponding test tasks):
25. synthetic dynamic VHD builder (needs 1, wraps fat12_image)
26. hand-encoded fixture (needs 1; independent of 25)
27. read-recording file wrapper (independent; needed by tests under 24/14)
28. deterministic mutation generator (needs 25)

Test tasks depend on both implementation tasks and fixture tasks as noted per-task below.
Final task 29 depends on all prior tasks.
```

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2"] },
    { "wave": 3, "tasks": ["3", "3.1"] },
    { "wave": 4, "tasks": ["4"] },
    { "wave": 5, "tasks": ["5", "6", "6.1"] },
    { "wave": 6, "tasks": ["7", "7.1", "7.2"] },
    { "wave": 7, "tasks": ["8"] },
    { "wave": 8, "tasks": ["9", "10", "11", "11.1", "11.2"] },
    { "wave": 9, "tasks": ["12", "12.1", "12.2"] },
    { "wave": 10, "tasks": ["13", "13.1", "13.2"] },
    { "wave": 11, "tasks": ["14", "14.1"] },
    { "wave": 12, "tasks": ["15"] },
    { "wave": 13, "tasks": ["16", "28"] },
    { "wave": 14, "tasks": ["16.1", "16.2"] },
    { "wave": 15, "tasks": ["17", "17.1"] },
    { "wave": 16, "tasks": ["18", "19"] },
    { "wave": 17, "tasks": ["20", "21", "21.1", "21.2"] },
    { "wave": 18, "tasks": ["22"] },
    { "wave": 19, "tasks": ["23", "23.1", "23.2"] },
    { "wave": 20, "tasks": ["24", "24.1", "24.2", "24.3"] },
    { "wave": 21, "tasks": ["25"] },
    { "wave": 22, "tasks": ["26", "27"] },
    { "wave": 23, "tasks": ["29", "29.1"] },
    { "wave": 24, "tasks": ["30", "30.1"] },
    { "wave": 25, "tasks": ["31", "31.1"] },
    { "wave": 26, "tasks": ["32"] },
    { "wave": 27, "tasks": ["33"] },
    { "wave": 28, "tasks": ["34"] }
  ]
}
```

## Tasks

- [x] 1. Add VHD constants and data model field tables to `legacyrescue/image.py`
  - Add `SECTOR_SIZE`, `HEADER_SIZE`, `FOOTER_SIZE`, `MAX_VIRTUAL_SIZE`, `MIN_BLOCK_SIZE`, `MAX_BLOCK_SIZE`, `MAX_BAT_ENTRIES`, `DISK_TYPE_FIXED`, `DISK_TYPE_DYNAMIC`, `DISK_TYPE_DIFFERENCING` module-level constants exactly as specified in design.md's Components and Interfaces section
  - Component: `legacyrescue/image.py` constants block
  - _Requirements: 2.3, 2.4, 2.6_

- [x] 2. Implement `read_head_tail_windows(f, size)`
  - Implement the bounded-I/O rule: independently `seek`/`read` the first `min(512, size)` bytes as `head`, and (only when `size >= 512`) independently `seek`/`read` the last 512 bytes as `tail`, with `tail = None` when `size < 512`; `tail` must never be derived from `head`
  - Component: `read_head_tail_windows`
  - _Requirements: 2.1_

- [x] 3. Implement `classify_footer_candidate(window)`
  - Given a (possibly short) window, return `{"cookie_ok": bool, "disk_type": int | None}` without performing checksum or structural validation
  - Component: `classify_footer_candidate`
  - _Requirements: 2.1_

- [ ]* 3.1 Write unit tests for head/tail windowing and classification
  - Cover: file size < 512 (tail is `None`), file size in 512-1023 (head/tail overlap but are independently correct), file size >= 1024 (no overlap), and `classify_footer_candidate` on short/empty/non-cookie windows
  - This directly covers the regression scenario for the historical `tail = head` aliasing bug
  - _Requirements: 2.1_

- [x] 4. Implement `parse_footer(raw)`
  - Pure `struct.unpack`-based parsing of all documented FooterFields per the Data Models table (cookie, features, fileFormatVersion, dataOffset, timestamp, creatorApplication, creatorVersion, creatorHostOS, originalSize, currentSize, diskGeometry, diskType, checksum, uniqueId, savedState, reserved); no validation
  - Component: `parse_footer`, `FooterFields`
  - _Requirements: 2.1_

- [x] 5. Implement `validate_footer_checksum(raw, parsed)`
  - Recompute the ones'-complement sum of all footer bytes with the checksum field treated as zero; raise `ValueError` on mismatch
  - Component: `validate_footer_checksum`
  - _Requirements: 2.6_

- [x] 6. Implement `validate_footer_structure(parsed)`
  - Raise `ValueError` if `cookie != b"conectix"` or `diskType not in {2, 3, 4}` or other documented fields are out of range
  - Component: `validate_footer_structure`
  - _Requirements: 2.1, 2.6_

- [ ]* 6.1 Write unit tests for footer parsing, checksum, and structure validation
  - Cover: a valid footer round-trips through parse/checksum/structure with no error; a corrupted checksum raises `ValueError`; a wrong cookie raises `ValueError`; an out-of-set `diskType` raises `ValueError`
  - _Requirements: 2.1, 2.6_

- [x] 7. Implement `validate_footer_pair_consistent(footer_a, footer_b)`
  - Raise `ValueError` unless `diskType`, `uniqueId`, `originalSize`, `currentSize`, and `dataOffset` all match between the two footers
  - Additionally and independently, raise `ValueError` unless `footer_a.diskType == 3 AND footer_b.diskType == 3` (re-confirmed regardless of the five-field agreement check and never inferred from checksum equality)
  - Component: `validate_footer_pair_consistent`
  - _Requirements: 2.6_

- [ ]* 7.1 Write unit tests for footer-pair consistency
  - Cover: matching pair with both type 3 passes; mismatched `dataOffset` with otherwise-matching valid checksums is rejected (Bug 2 regression); a pair that agrees on all five fields but both report a non-3 type is rejected
  - _Requirements: 2.6_

- [ ]* 7.2 Write property test for footer-pair consistency
  - **Property 10: Footer-pair field consistency is enforced across all five identity fields, and both copies must individually be type 3**
  - **Validates: Requirements 2.6**

- [x] 8. Implement `parse_header(raw)`
  - Pure `struct.unpack`-based parsing of all documented HeaderFields per the Data Models table (cookie, dataOffset, tableOffset, headerVersion, maxTableEntries, blockSize, checksum, parentUniqueId, parentTimestamp, parentUnicodeName, parentLocatorEntries, reserved); no validation
  - Component: `parse_header`, `HeaderFields`
  - _Requirements: 2.2_

- [x] 9. Implement `validate_header_span(header_offset, file_size, footer_intervals)`
  - Raise `ValueError` unless `header_offset >= 512`, `header_offset` is sector-aligned, `header_offset + 1024 <= file_size`, and `[header_offset, header_offset + 1024)` does not overlap either footer interval
  - Component: `validate_header_span`
  - _Requirements: 2.2, 2.5_

- [x] 10. Implement `validate_header_checksum(raw, parsed)`
  - Recompute the ones'-complement sum of all header bytes with the checksum field treated as zero; raise `ValueError` on mismatch
  - Component: `validate_header_checksum`
  - _Requirements: 2.2_

- [x] 11. Implement `validate_header_structure(parsed)`
  - Raise `ValueError` if `cookie != b"cxsparse"`, `headerVersion` is not the expected value, or `tableOffset` is not sector-aligned and `>= 512`
  - Component: `validate_header_structure`
  - _Requirements: 2.2_

- [ ]* 11.1 Write unit tests for header span, parsing, checksum, and structure validation
  - Cover: valid header passes all four checks; header offset not sector-aligned is rejected; header span overlapping a footer is rejected; header truncated/corrupted checksum is rejected; wrong cookie is rejected
  - _Requirements: 2.2_

- [ ]* 11.2 Write property test for invalid footer/header rejection before use
  - **Property 6: Invalid footer/header is always rejected before use**
  - **Validates: Requirements 2.1, 2.2**

- [x] 12. Implement `validate_block_size(block_size)`
  - Raise `ValueError` unless `MIN_BLOCK_SIZE <= block_size <= MAX_BLOCK_SIZE`, `block_size` is a power of two, and `block_size` is a multiple of `SECTOR_SIZE`; independent of virtual size and BAT capacity
  - Component: `validate_block_size`
  - _Requirements: 2.3_

- [ ]* 12.1 Write unit tests for block size validation
  - Cover: zero, negative, non-power-of-two, non-sector-aligned, exactly `MAX_BLOCK_SIZE` (accepted), and `MAX_BLOCK_SIZE + 1` (rejected)
  - _Requirements: 2.3_

- [ ]* 12.2 Write property test for block size validation
  - **Property 7: Block size validation is independent and total**
  - **Validates: Requirements 2.3**

- [x] 13. Implement `validate_virtual_size(virtual_size)`
  - Raise `ValueError` unless `0 < virtual_size <= MAX_VIRTUAL_SIZE` and `virtual_size % SECTOR_SIZE == 0`
  - Component: `validate_virtual_size`
  - _Requirements: 2.4_

- [ ]* 13.1 Write unit test for the exact 256 MiB virtual size boundary
  - Single concrete (non-randomized) test: `virtualSize == 268435456` accepted; `268435456 + 1` rejected; a non-sector-aligned in-range value rejected
  - _Requirements: 2.4_

- [ ]* 13.2 Write property test for virtual size boundary enforcement
  - **Property 8: Virtual size cap is enforced exactly at the boundary**
  - **Validates: Requirements 2.4**

- [x] 14. Implement `validate_bat_capacity(max_table_entries, virtual_size, block_size)`
  - Raise `ValueError` unless `max_table_entries == ceil(virtual_size / block_size)` (exact equality) AND `max_table_entries <= MAX_BAT_ENTRIES`
  - Must be called, and must pass, before any BAT bytes length is computed or read
  - Component: `validate_bat_capacity`
  - _Requirements: 2.5, 2.6_

- [ ]* 14.1 Write unit tests for BAT capacity exact-equality policy
  - Cover: `maxTableEntries` one less than and one more than the exact `ceil(virtualSize/blockSize)` value, both rejected; exact value accepted; value above `MAX_BAT_ENTRIES` rejected even if it would otherwise match
  - _Requirements: 2.5, 2.6_

- [x] 15. Implement `compute_bat_bytes_len(max_table_entries)`
  - Return `ceil(max_table_entries * 4 / 512) * 512`; document the precondition that `max_table_entries` has already passed `validate_bat_capacity`
  - Component: `compute_bat_bytes_len`
  - _Requirements: 2.5_

- [x] 16. Implement `validate_bat_span(table_offset, bat_bytes_len, file_size, prior_intervals)`
  - Raise `ValueError` unless `table_offset >= 512`, `table_offset + bat_bytes_len <= file_size`, and `[table_offset, table_offset + bat_bytes_len)` does not overlap any interval in `prior_intervals`
  - Component: `validate_bat_span`
  - _Requirements: 2.5_

- [ ]* 16.1 Write unit test for the oversized-BAT-claim pre-read-rejection proof
  - Uses the read-recording file wrapper (task 27) to assert `validate_bat_capacity`/`validate_bat_span` reject a `maxTableEntries` whose implied `batLen` exceeds file size strictly before any read targets the claimed BAT region
  - This is one of the two tests in the suite permitted to use the read-recording wrapper instead of calling `load()` only through observable outcomes
  - _Requirements: 2.5, 2.6_

- [ ]* 16.2 Write property test for BAT capacity exactness and pre-read bounding
  - **Property 9: BAT capacity is exact and bounded before any BAT read**
  - Uses the read-recording file wrapper (task 27) to confirm no BAT-region read occurs for invalid cases, per design.md's Testing Strategy
  - **Validates: Requirements 2.5, 2.6**

- [x] 17. Implement `parse_bat(raw, max_table_entries)`
  - Parse exactly `max_table_entries` big-endian uint32 sector offsets from `raw`; `0xFFFFFFFF` marks an entry unallocated
  - Component: `parse_bat`
  - _Requirements: 2.5_

- [ ]* 17.1 Write unit test for BAT entry alignment rejection
  - Cover: a BAT entry whose implied block interval overlaps the header is rejected, and an entry with invalid alignment raises `ValueError`
  - _Requirements: 2.5_

- [x] 18. Implement `build_metadata_intervals(leading_footer_present, file_size, header_offset, table_offset, bat_bytes_len)`
  - Return `(start, end)` half-open tuples for the trailing footer, the leading footer (if present), the header, and the BAT, exactly as specified in design.md
  - Component: `build_metadata_intervals`
  - _Requirements: 2.5_

- [x] 19. Implement `build_block_intervals(bat, block_size)`
  - For each allocated BAT entry, compute the `(start, end)` interval covering its sector bitmap (rounded up to a whole sector) plus data block, in BAT order; unallocated entries contribute nothing
  - Component: `build_block_intervals`
  - _Requirements: 2.5, 3.1, 3.2_

- [x] 20. Implement `validate_disjoint_sorted(intervals, file_size)`
  - Sort all intervals (both footers, header, BAT, every allocated block) by start, then perform a single linear adjacent-pair scan raising `ValueError` on `start < 0`, `end > file_size`, or any adjacent overlap
  - Component: `validate_disjoint_sorted`
  - _Requirements: 2.5_

- [x] 21. Implement `validate_blocks_do_not_reach_footer(block_intervals, file_size)`
  - Raise `ValueError` if any block interval's `end > file_size - 512`; applied only to `block_intervals`, separately from and in addition to `validate_disjoint_sorted`
  - Component: `validate_blocks_do_not_reach_footer`
  - _Requirements: 2.5_

- [ ]* 21.1 Write unit tests for interval construction and disjointness/bound checks
  - Cover: a block interval overlapping the header is rejected by `validate_disjoint_sorted`; a block interval ending exactly at `file_size - 512` passes `validate_blocks_do_not_reach_footer`; a block interval ending at `file_size - 511` is rejected by it even though it would pass the plain `end <= file_size` check
  - _Requirements: 2.5_

- [ ]* 21.2 Write property test for interval disjointness and block-vs-footer bound
  - **Property 12: All intervals are disjoint and in-bounds; blocks additionally never reach the trailing footer**
  - Include generated cases placing a block interval's end at exactly `file_size - 512` (must pass) and at `file_size - 511` (must fail)
  - **Validates: Requirements 2.5**

- [x] 22. Implement `read_bitmap_and_block(f, bat_entry_sectors, block_size)`
  - Bounded read: seek to `bat_entry_sectors * SECTOR_SIZE`, read the sector bitmap (rounded up to a whole sector), then read `block_size` bytes immediately following
  - Component: `read_bitmap_and_block`
  - _Requirements: 2.5, 3.1, 3.2_

- [x] 23. Implement `reconstruct_flat_bytes(virtual_size, block_size, bat, f)`
  - Allocate a zero-filled `bytearray` of `virtual_size` bytes; for each allocated BAT entry, read its bitmap and block via `read_bitmap_and_block`, and for each sector copy it into the output buffer when the bitmap bit is set, leaving zeros otherwise; return `bytes(output)`
  - Component: `reconstruct_flat_bytes`
  - _Requirements: 1.1, 1.4, 3.1, 3.2, 3.3_

- [ ]* 23.1 Write unit tests for sparse/used sector reconstruction
  - Cover: a fully-sparse block reconstructs to all zeros of the correct size; a block with a mix of used/sparse sectors copies used sectors verbatim and zero-fills sparse ones
  - _Requirements: 3.1, 3.2, 3.3_

- [ ]* 23.2 Write property tests for sparse-zero and used-sector-copy behavior
  - **Property 3: Sparse sectors are exactly zero**
  - **Property 4: Used sectors are copied verbatim**
  - **Validates: Requirements 3.1, 3.2, 3.3**

- [x] 24. Wire the dynamic VHD dispatch and parsing pipeline into `load()`
  - Replace only the dynamic-rejection branch of the existing `load()` with the full dispatch precedence from design.md's Architecture section: type-4 rejection at either position, type-3 dynamic path entry (invoking the full validation/reconstruction pipeline built in tasks 1-23 in the documented order: both footers -> pair consistency -> header span/parse/checksum/structure -> block size -> virtual size -> BAT capacity -> BAT span -> BAT parse -> metadata/block intervals -> disjointness + block-footer bound -> reconstruction), trailing-fallback fixed-VHD strip, leading-fallback rejection, VMDK/QCOW2/VDI signature rejection, and raw fallback -- in that exact order
  - Preserve the existing raw/fixed/signature-rejection code paths' exact byte-for-byte behavior and info dict fields (`path`, `size`, `container`) for all non-type-3/non-type-4 inputs
  - Add `"dynamic VHD"` container value and `virtual_size` info field for the new path
  - Component: `load()`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 4.1, 4.2, 4.3_

- [ ]* 24.1 Write unit tests for dispatch precedence and backward compatibility
  - Cover: raw image loads unchanged; fixed VHD (trailing type 2) loads with footer stripped; the 512-1023 byte head/tail aliasing regression case classifies correctly; both-copies-type-2 fixed-vs-dynamic disambiguation; leading type-4 rejected with differencing error; leading non-dynamic-valid cookie rejected with historical message; VMDK/QCOW2/VDI signatures rejected with historical message
  - _Requirements: 1.2, 1.3, 2.1, 4.1, 4.2_

- [ ]* 24.2 Write property test for raw/fixed output preservation
  - **Property 2: Raw and fixed-VHD output preserved for all non-type-3/non-type-4 inputs**
  - **Validates: Requirements 1.2, 1.3, 4.1, 4.2**

- [ ]* 24.3 Write property test for total ordered dispatch precedence
  - **Property 5: Dispatch precedence is total and ordered**
  - **Validates: Requirements 1.2, 1.3, 2.1**

- [x] 25. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 26. Add synthetic dynamic VHD builder to `tests/fixtures.py`
  - Add a builder function that wraps `fat12_image()` bytes in a valid dynamic VHD container: computes block size/virtual size/BAT entries from the wrapped content, writes correctly-checksummed leading and trailing footers, a correctly-checksummed header, a BAT with allocated entries covering the wrapped content, and per-block sector bitmaps (all-used, to make the wrapped bytes recoverable exactly)
  - Component: `tests/fixtures.py` `Synthetic_Builder`
  - _Requirements: 5.1, 5.2_

- [x] 27. Add an independently hand-encoded dynamic VHD fixture to `tests/fixtures.py`
  - Add a second fixture, built by directly packing footer/header/BAT/bitmap/block bytes with `struct.pack` at fixed, literal offsets (not calling the builder from task 26), to serve as an independent cross-check against the builder's own encoding
  - Component: `tests/fixtures.py` hand-encoded fixture
  - _Requirements: 5.1_

- [x] 28. Add the read-recording file-like wrapper to `tests/fixtures.py`
  - Add a wrapper class around a real file object that records every `(offset, length)` pair passed to `seek`/`read`, for use only by the two pre-read-rejection proofs (tasks 16.1 and 16.2)
  - Component: `tests/fixtures.py` read-recording wrapper
  - _Requirements: 2.5, 2.6_

- [x] 29. Add the deterministic mutation generator to `tests/fixtures.py`
  - Add a generator, seeded with a fixed `random.Random` instance, that produces exactly 100 mutation iterations against the synthetic dynamic VHD from task 26, where each iteration is pre-classified at generation time as "expected valid" (targets only a reserved/padding byte no validation function inspects) or "expected invalid" (targets a field a validation function checks), per design.md's Property 11 definition; no iteration may leave its expected outcome ambiguous
  - Component: `tests/fixtures.py` `Corruption_Generator`
  - _Requirements: 5.3, 5.4, 6.2_

- [x]* 29.1 Write unit tests for the test fixtures themselves
  - Cover: the synthetic builder's output passes footer/header checksum validation and round-trips through `load()`; the hand-encoded fixture independently round-trips through `load()`; the read-recording wrapper correctly records `seek`/`read` calls; the mutation generator produces exactly 100 iterations each with a determinate classification
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 30. Create `tests/test_dynamic_vhd.py` and write the round-trip and corruption-rejection unit tests
  - Using the synthetic builder (task 26) and hand-encoded fixture (task 27): a minimal valid synthetic dynamic VHD (one allocated block, rest sparse) round-trips through `load()`; a fully-sparse dynamic VHD reconstructs to all-zero flat bytes of correct virtual size; corrupted footer checksum, corrupted header checksum, and corrupted BAT entry alignment each individually raise `ValueError`
  - Component: `tests/test_dynamic_vhd.py`
  - _Requirements: 1.1, 1.4, 2.1, 2.2, 2.5, 5.2, 5.3_

- [x]* 30.1 Write property test for dynamic VHD round-trip reconstruction
  - **Property 1: Dynamic VHD round-trip reconstruction**
  - Vary block size, virtual size, and allocated/sparse block patterns across a modest number of synthetic `Fat12_Image`-wrapping dynamic VHDs built with task 26's builder
  - **Validates: Requirements 1.1, 1.4, 5.2**

- [x] 31. Write the mutation robustness test in `tests/test_dynamic_vhd.py`
  - Using the mutation generator (task 29), run all 100 pre-classified iterations against `load()`, asserting each iteration's specific pre-classified outcome (successful reconstruction with correct flat bytes, or `ValueError`); assert no iteration raises any exception other than `ValueError` and no iteration hangs
  - Component: `tests/test_dynamic_vhd.py`
  - _Requirements: 5.3, 5.4, 6.2_

- [x]* 31.1 Write property test for mutation robustness
  - **Property 11: Mutation robustness with pre-classified expected outcomes**
  - **Validates: Requirements 5.3, 5.4, 6.2**

- [x] 32. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 33. Write integration-style checks against `inventory.py`, `fat.py`, `runbook.py`, and `report.py` in `tests/test_dynamic_vhd.py`
  - For a small number of representative synthetic dynamic VHDs, confirm `inventory.py`, `fat.py`, `runbook.py`, and `report.py` each produce identical output when given the dynamic VHD's flat bytes versus the same content loaded as a raw image (excluding container metadata fields)
  - _Requirements: 3.4, 4.4_

- [x] 34. Final checkpoint - run full suite and confirm all tests pass
  - Run `python -m unittest discover -s tests -t .` and confirm all tests pass
  - This is the completion gate for the feature; resolve any failures before considering the feature complete

## Notes

- Tasks marked with `*` are optional (test-writing sub-tasks) and are implemented as part of normal execution per this workflow's testing patterns, but are visually distinguished and may be skipped for a faster MVP.
- Top-level tasks are never marked optional.
- Every property-based test task references its design.md property number and the requirements it validates, per the Property Annotation Format.
- Tasks 1-24 must preserve `load()`'s existing raw/fixed/rejection behavior byte-for-byte for all non-type-3/non-type-4 inputs; task 24's dispatch rewiring is the only change to the existing branches, and it is limited to windowing/ordering corrections described in design.md.
- Tasks 26-29 are fixture-only tasks (no test assertions); test-case tasks (30, 31, 33) depend on them.

### Status note on unchecked optional (`*`) boxes

Several optional sub-tasks above (e.g. 3.1, 6.1, 7.1/7.2, 11.1/11.2,
12.1/12.2, 13.1/13.2, 14.1, 16.1/16.2, 17.1, 21.1/21.2, 23.1/23.2,
24.1/24.2/24.3) are left unchecked in this file even though
`tests/test_dynamic_vhd.py` contains corresponding test classes/functions
(spot-checked: `TestReadHeadTailWindows`, `TestParseFooter`,
`TestFooterPairConsistent`, `TestFooterPairConsistentProperty`,
`TestParseHeader`, `TestValidateBatCapacity`,
`TestIntervalConstructionAndDisjointness`, and others by matching class
name to task). The checkboxes were not kept in sync with the test file
as work landed. These boxes are intentionally left unchecked
here rather than blanket-checked, because this note is based on a
class-name spot check, not a line-by-line re-verification of every
optional box's exact scope against its test body. Before relying on this
list, re-run `python -m unittest discover -s tests -t .` (133 tests
ran, 0 failures, 2 missing-image skips, as of this note) and check the
specific test class/function named in the task you care about.
