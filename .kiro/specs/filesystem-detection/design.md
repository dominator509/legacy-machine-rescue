# Design: Filesystem Detection

## Overview

This design implements detection of NTFS, exFAT, and unknown filesystems from boot-sector signatures. The system currently detects FAT12/16/32 via structural BPB parsing. This enhancement adds signature-based detection for non-FAT filesystems with clear "detected, not readable" labels in inventory output. The scope is limited to detection, inventory labels, and synthetic tests—no filesystem reading capabilities are added.

## Critical Consumer Requirements

### Existing Consumers (MUST PRESERVE)
1. **report.py**: Accesses `v["fs"]` and `v["files"]`
2. **runbook.py**: Accesses `v["fs"]`, `v["files"]`, `v["label"]`, `v["os"]`, `v["where"]`, `v["boot_files_present"]`, `v["config_files"]`
3. **All keys must exist** for non-FAT volumes to avoid crashes

### Safe Values for Non-FAT Volumes
- `"fs"`: `"NTFS (detected, not readable)"` (clear detection label)
- `"files"`: `0` (no files readable, not measured as zero)
- `"label"`: `""` (empty string, no label parsed)
- `"os"`: `"Unknown"` (no OS guess from filesystem alone)
- `"boot_files_present"`: `[]` (empty list)
- `"config_files"`: `{}` (empty dict)
- `"where"`: `"partition 1"` (description from detection)

## Design Principles

1. **Minimal scope**: Detection only, no parsing or reading of NTFS/exFAT structures
2. **Pure signature matching**: Bytes 3:11 string comparison only for NTFS/exFAT
3. **Preserve existing**: FAT detection and `volumes()` API unchanged
4. **Consumer safety**: All required keys exist with safe values
5. **No false claims**: Clear "(detected, not readable)" labels
6. **No OS inference**: Filesystem signature does not imply installed Windows version

## System Architecture

### Current System

```
image.py
├── load() - reads disk image, strips VHD footer
├── partitions() - detects MBR partitions
├── volumes() - returns FAT volumes (FatVolume objects) - UNCHANGED
└── boot_code_hint() - hints from boot sector text

inventory.py
├── inspect() - main inventory function
└── Returns JSON with volumes, warnings, hardware flags
```

### Enhanced Detection Flow

```
[Image Data]
    │
    ├── volumes() - unchanged, returns (desc, FatVolume) pairs only for FAT
    │
    └── detect_all_volumes() - NEW: returns detection results for all
        │
        ├── Check bytes 3:11 for "NTFS    " → "NTFS"
        ├── Check bytes 3:11 for "EXFAT   " → "exFAT"  
        ├── Check structural FAT BPB → "FAT12"/"FAT16"/"FAT32"
        └── No match → "Unknown"
        │
        ▼
Inventory with safe values for all required keys
```

## Component Design

### 1. Filesystem Detection Function (NEW)

**Location**: `legacyrescue/image.py` (new function)

**Signature**: `detect_filesystem_at_offset(data: bytes, offset: int = 0) -> str`

**Behavior**:
- Input: Full image data and byte offset
- Output: One of: "FAT12", "FAT16", "FAT32", "NTFS", "exFAT", "Unknown"
- Logic:
  1. Extract boot sector slice: `sector = data[offset:offset + 512]`
  2. If `len(sector) < 512`: return "Unknown"
  3. Check bytes 3:11 for signatures:
     - `sector[3:11] == b"NTFS    "`: return "NTFS"
     - `sector[3:11] == b"EXFAT   "`: return "exFAT"
  4. Try FatVolume construction with full data and offset:
     - Success: return FatVolume.kind ("FAT12"/"FAT16"/"FAT32")
     - FatError: return "Unknown"

**Key Decisions**:
- Uses full data and real offset for FAT detection (not sector slices)
- FAT detection remains via existing FatVolume structural parsing
- NTFS/exFAT detection is pure string matching at bytes 3:11
- Returns "Unknown" for short/truncated data

### 2. Volume Detection Enhancement (ADDITIVE)

**Location**: `legacyrescue/image.py` (new function, `volumes()` unchanged)

**New function**: `detect_all_volumes(data: bytes) -> List[Dict]`

**Return structure for non-FAT volumes**:
```python
{
    "where": "partition 1",
    "fs_raw": "NTFS",  # Raw detection: "NTFS", "exFAT", "Unknown"
    "fs": "NTFS (detected, not readable)",
    "files": 0,  # No files readable (not measured as zero)
    "label": "",  # Empty string (no label parsed)
    "oem": "NTFS    ",  # Actual OEM bytes if available
    "size_bytes": None,  # None indicates unknown/not measured
    "bytes_used": None,  # None indicates unknown/not measured
    "os": "Unknown",  # No OS guess from filesystem alone
    "boot_files_present": [],  # Empty list
    "boot_files_missing": [],  # Empty list  
    "config_files": {},  # Empty dict
    # Note: No fat_volume field for non-FAT
}
```

**Return structure for FAT volumes**:
```python
{
    "where": "partition 2",
    "fs_raw": "FAT32",
    "fs": "FAT32",  # No suffix for readable filesystems
    "files": 142,  # Actual count from FatVolume
    "label": "FREEDOS",  # Actual label
    "oem": "MSDOS5.0",  # Actual OEM
    "size_bytes": 1474560,  # Actual size
    "bytes_used": 925696,  # Actual usage
    "os": "FreeDOS",  # OS guess from boot files
    "boot_files_present": ["KERNEL.SYS", "COMMAND.COM"],
    "boot_files_missing": [],
    "config_files": {"CONFIG.SYS": "DEVICE=...", "AUTOEXEC.BAT": "..."},
    "fat_volume": FatVolume_instance  # For internal use only
}
```

**Implementation notes**:
- `volumes()` function remains unchanged (returns `(desc, FatVolume)` pairs)
- New function for additive detection without breaking changes
- All required keys present for consumer compatibility
- `None` for unknown size/bytes (not 0, which implies measured zero)
- `files: 0` for non-FAT (no files readable, different from unknown count)

### 3. Inventory Integration

**Location**: `legacyrescue/inventory.py` (modify `inspect()` function)

**Current behavior**: Only processes FAT volumes, generic warning for others
**Enhanced behavior**: Uses `detect_all_volumes()` for comprehensive detection

**Key changes to `inspect()`**:
1. Replace `vols = img.volumes(data)` with `detected = img.detect_all_volumes(data)`
2. For each detected volume:
   - **FAT volumes**: Process normally with FatVolume (unchanged flow)
   - **Non-FAT volumes**: Use pre-built dictionary from detection

**Non-FAT volume processing**:
```python
if "fat_volume" in volume_info:  # FAT volume
    # Existing processing unchanged
    v = volume_info["fat_volume"]
    files = [(p, e) for p, e in v.walk() if not e["dir"]]
    # ... existing logic
else:  # Non-FAT volume
    # Use pre-built dictionary from detect_all_volumes()
    rep["volumes"].append(volume_info)
```

**Warning messages**:
- **Current**: "No readable FAT volume found. NTFS, exFAT, Linux or unknown formats are not parsed..."
- **Enhanced**: Specific detection in warnings:
  ```python
  if not any("fat_volume" in v for v in detected):
      # No FAT volumes found
      fs_types = [v["fs_raw"] for v in detected]
      if "NTFS" in fs_types:
          rep["warnings"].append("NTFS volume detected (not readable)")
      if "exFAT" in fs_types:
          rep["warnings"].append("exFAT volume detected (not readable)")
      if "Unknown" in fs_types:
          rep["warnings"].append("Unknown filesystem detected (not readable)")
  ```

### 4. Report/Runbook Consumer Safety

**Explicit branches in consumers (NOT in this task)**:
- Current report.py and runbook.py work with provided safe values
- No changes needed to consumers for basic compatibility
- Output is safe: `0` files, `""` label, `"Unknown"` OS, empty lists/dicts

**Future enhancement consideration**:
- Could add explicit unsupported branches in report/runbook
- For now, safe values prevent crashes
- Clear "(detected, not readable)" labels inform users

### 5. Test Design

**Location**: `tests/test_filesystem_detection.py` (new file)

**Test categories**:
1. **Exact signature tests**: Canonical NTFS/exFAT strings
2. **Malformed signature tests**: Near matches, wrong padding
3. **MBR offset tests**: Non-zero partition offsets with mixed filesystems
4. **Consumer safety tests**: report.py and runbook.py integration
5. **Truncated/random input**: Short data, damaged sectors
6. **FAT preservation tests**: Existing tests unchanged
7. **End-to-end tests**: Bare unsupported and mixed partitions

**Synthetic test data (corrected math)**:
```python
# 3 bytes jump + 8 bytes OEM + 499 zeros + 2 bytes signature = 512 bytes
NTFS_EXACT = b"\xEB\x52\x90" + b"NTFS    " + b"\x00" * 499 + b"\x55\xaa"
EXFAT_EXACT = b"\xEB\x76\x90" + b"EXFAT   " + b"\x00" * 499 + b"\x55\xaa"

# Malformed signatures (should not match)
NTFS_MALFORMED = b"\xEB\x52\x90" + b"NTFS   X" + b"\x00" * 499 + b"\x55\xaa"  # Wrong padding
EXFAT_MALFORMED = b"\xEB\x76\x90" + b"EXFAT  A" + b"\x00" * 499 + b"\x55\xaa"  # Variant rejected

# Short data
SHORT_256 = os.urandom(256)

# MBR fixture with unsupported partition first
MBR_MIXED = create_mbr_with_partitions([
    {"lba": 63, "type": 0x07, "size": 1000},  # NTFS/exFAT
    {"lba": 1063, "type": 0x0B, "size": 2000},  # FAT32
])
```

**Test methods**:
- `test_detect_ntfs_exact()` - canonical "NTFS    " (3+8+499+2=512)
- `test_detect_exfat_exact()` - canonical "EXFAT   "
- `test_reject_malformed_signatures()` - near matches rejected
- `test_detect_at_mbr_offset()` - partition table handling
- `test_consumer_safe_values()` - all required keys exist
- `test_report_integration()` - report.py doesn't crash
- `test_runbook_integration()` - runbook.py doesn't crash
- `test_end_to_end_mixed()` - unsupported first, FAT second
- `test_end_to_end_bare_unsupported()` - only non-FAT volumes
- `test_handle_truncated_data()` - short input safety
- `test_unchanged_fat_detection()` - existing FAT tests
- `test_never_crashes_fuzz()` - property test

## Data Structures

### Detection Result
```python
@dataclass
class VolumeDetection:
    where: str  # e.g., "partition 1", "whole image"
    fs_raw: str  # "FAT12", "FAT16", "FAT32", "NTFS", "exFAT", "Unknown"
    fs: str  # Formatted: "FAT32" or "NTFS (detected, not readable)"
    offset: int  # byte offset in image
    size_bytes: Optional[int] = None  # None for unknown, actual for FAT
    bytes_used: Optional[int] = None  # None for unknown, actual for FAT
    files: int = 0  # 0 for non-FAT (no files readable)
    label: str = ""  # Empty for non-FAT
    oem: str = ""  # Empty or actual OEM bytes
    os: str = "Unknown"  # "Unknown" for non-FAT
    boot_files_present: List[str] = field(default_factory=list)
    boot_files_missing: List[str] = field(default_factory=list)
    config_files: Dict[str, str] = field(default_factory=dict)
    fat_volume: Optional[FatVolume] = None  # Internal use only
```

## Implementation Plan

### Phase 1: Core Detection (Additive)
1. Add `detect_filesystem_at_offset()` to `image.py`
2. Add `detect_all_volumes()` to `image.py` (new, `volumes()` unchanged)
3. Add unit tests for exact/malformed signatures (3+8+499+2=512)
4. Add tests for MBR offsets and truncated data

### Phase 2: Inventory Integration
1. Modify `inventory.inspect()` to use `detect_all_volumes()`
2. Implement safe non-FAT volume entries with all required keys
3. Update warning messages with specific detection
4. Add consumer safety tests (report.py, runbook.py)

### Phase 3: End-to-End Testing
1. Create `test_filesystem_detection.py`
2. Add synthetic test fixtures (exact and malformed)
3. Add end-to-end tests for mixed partitions
4. Add consumer integration tests
5. Verify existing FAT tests unchanged
6. Run `python -m unittest discover tests`

## Key Revisions from Feedback

### 1. Consumer Safety
- All required keys exist: `fs`, `files`, `label`, `os`, `where`, `boot_files_present`, `config_files`
- Safe values: `files: 0`, `label: ""`, `os: "Unknown"`, empty lists/dicts
- Prevents crashes in report.py and runbook.py

### 2. No False Measurements
- `size_bytes: None` (not 0) for non-FAT volumes
- `bytes_used: None` (not 0) for non-FAT volumes
- Clear distinction between "no files readable" (`files: 0`) and "unknown count"

### 3. Correct Sector Math
- 3 bytes jump + 8 bytes OEM + 499 zeros + 2 bytes signature = 512 bytes
- Fixed test fixture calculations
- Correct padding in synthetic sectors

### 4. End-to-End Tests
- Bare unsupported partitions (only non-FAT)
- Mixed partitions (unsupported first, FAT second)
- Consumer integration tests (report.py, runbook.py)

### 5. Unverified Targets
- All implementation claims are targets
- Test suite verifies implementation
- No checkmark glyphs in design document

### 6. Additive Design
- `volumes()` API unchanged (returns `(desc, FatVolume)` pairs)
- New `detect_all_volumes()` function for comprehensive detection
- No breaking changes to existing code

## Signature Specifications

### Byte Positions (Boot Sector)
- Bytes 0-2: Jump instruction (EB XX 90)
- Bytes 3-10: OEM string (8 bytes)
- Bytes 510-511: Boot signature (55 AA)

### Canonical Signatures
- **NTFS**: `b"NTFS    "` (4E 54 46 53 20 20 20 20) - 4 trailing spaces
- **exFAT**: `b"EXFAT   "` (45 58 46 41 54 20 20 20) - 3 trailing spaces

### Rejected Variants
- `b"NTFS   X"` (wrong padding)
- `b"EXFAT  A"` (invented variant)
- `b"NTFS"` (too short)
- `b"exfat   "` (wrong case)

## Test Strategy

### Unit Tests
- Pure function tests for detection logic
- Correct 512-byte sectors (3+8+499+2)
- MBR fixtures with partition offsets
- Edge cases (short, corrupted, random)

### Integration Tests
- `inventory.inspect()` with synthetic images
- Mixed filesystem detection (unsupported first)
- Output format validation

### Consumer Tests
- `report.py` integration (doesn't crash)
- `runbook.py` integration (doesn't crash)
- Safe values verification

### Property Tests
- Never crashes on any byte sequence
- Deterministic detection
- FAT detection unchanged

### Test Preservation
- Existing `test_core.py` and `test_fuzz.py` unchanged
- All existing tests continue to pass
- No regressions in FAT detection

## Success Criteria (Targets)

### Functional Targets
1. NTFS detection via exact bytes 3:11 matching
2. exFAT detection via exact bytes 3:11 matching  
3. FAT detection unchanged (structural BPB parsing)
4. Clear "(detected, not readable)" inventory labels
5. All required keys present for consumer safety
6. `None` for unknown size/bytes (not 0)
7. No crashes on damaged/truncated input
8. End-to-end tests for mixed partitions

### Non-functional Targets
1. `volumes()` API unchanged
2. Existing tests pass unchanged
3. Additive design (no breaking changes)
4. Safe output for report/runbook consumers
5. Comprehensive test coverage
6. Correct sector math (3+8+499+2=512)

## Open Questions

### Questions
1. Should `oem` field contain actual bytes or empty string? (Actual bytes if available)
2. Future: explicit unsupported branches in report/runbook? (Out of scope for Task 1)
3. Additional filesystem signatures? (No, keep scope small)

### Risks
1. **Consumer edge cases**: Thorough integration testing needed
2. **Signature collisions**: Test exact vs malformed thoroughly
3. **Performance**: Negligible (string comparison only)


## Design Amendment: Truthfulness in Report/Runbook Output

### Problem Statement
Current design provides safe values but creates false claims:
1. Runbook labels "0 files readable" as **MEASURED** (line 32: `"Filesystem %s, %d files readable | MEASURED |"`)
2. Runbook labels "OS guess: Unknown" as **MEASURED** (line 33: `"OS guess: %s | MEASURED (file names only) |"`)
3. For unsupported filesystems, neither file count nor OS was measured

### Solution: Explicit Unsupported Flag and Minimal Branches
Add explicit `unsupported: True` flag and minimal report/runbook adaptations within Task 1 scope.

#### 1. Add Unsupported Flag to Volume Detection
```python
{
    "where": "partition 1",
    "fs_raw": "NTFS",
    "fs": "NTFS (detected, not readable)",
    "unsupported": True,  # NEW: Explicit flag for report/runbook
    # ... other fields with safe values
}
```

#### 2. Minimal Report.py Adaptation (Task 1)
Current line 28: `'%s</span><span class=badge>%s</span>' % (badge, html.escape(v["fs"] + ", " + str(v["files"]) + " files" if v else "no readable filesystem")`

**Amendment**: Add branch for unsupported volumes:
```python
if v.get("unsupported"):
    badge_text = html.escape(v["fs"])  # Just "NTFS (detected, not readable)"
else:
    badge_text = html.escape(v["fs"] + ", " + str(v["files"]) + " files")
```

#### 3. Minimal Runbook.py Adaptation (Task 1)
Current lines 31-33:
```python
if vols:
    v = vols[0]
    a("| Filesystem %s, %d files readable | MEASURED |" % (v["fs"], v["files"]))
    a("| OS guess: %s | MEASURED (file names only) |" % v["os"])
```

**Amendment**: Add branch for unsupported volumes:
```python
if vols:
    v = vols[0]
    if v.get("unsupported"):
        a("| Filesystem %s detected | NOT READABLE |" % v["fs"])
        # No OS guess line for unsupported
    else:
        a("| Filesystem %s, %d files readable | MEASURED |" % (v["fs"], v["files"]))
        a("| OS guess: %s | MEASURED (file names only) |" % v["os"])
```

Current line 62: `"- %s: %s, label `%s`, %d files, OS: %s" % (v["where"], v["fs"], v["label"], v["files"], v["os"])`

**Amendment**: Add branch:
```python
if v.get("unsupported"):
    a("- %s: %s (detected, not readable)" % (v["where"], v["fs"]))
else:
    a("- %s: %s, label `%s`, %d files, OS: %s" % (v["where"], v["fs"], v["label"], v["files"], v["os"]))
```

### 4. Test Assertions for Wording
Add consumer tests with assertions for correct wording:
```python
def test_runbook_unsupported_wording():
    # Create synthetic NTFS image
    report = inventory.inspect(synthetic_ntfs_path)
    runbook_text = runbook.build(report)
    
    # Should NOT contain "MEASURED" for unsupported
    assert "NOT READABLE" in runbook_text
    assert "MEASURED" not in runbook_text  # For unsupported claims
    assert "(detected, not readable)" in runbook_text
    
def test_report_unsupported_wording():
    report = inventory.inspect(synthetic_ntfs_path)
    html = report.build(report, None, "", "/tmp/test.html")
    
    # Should contain detection label without file count
    assert "NTFS (detected, not readable)" in html
    assert "files" not in html  # No file count for unsupported
```

### Scope Boundaries
- **Task 1**: Minimal branches in report.py and runbook.py for truthfulness
- **Not Task 1**: Full unsupported filesystem feature in runbook
- **Preserved**: FAT branch output unchanged

### Truthfulness Principles
1. **No false MEASURED claims**: Unsupported filesystems → NOT READABLE
2. **No false OS guesses**: Omit OS guess line for unsupported
3. **No false file counts**: Don't claim "0 files readable" as measured
4. **Clear detection status**: "(detected, not readable)" labels

### Implementation Impact
- Small, targeted changes to report.py and runbook.py
- Maintains all other design points
- Ensures truthful output within Task 1 scope
- FAT volumes unchanged, unsupported volumes get truthful labels