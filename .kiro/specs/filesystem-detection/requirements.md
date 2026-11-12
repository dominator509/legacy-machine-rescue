# Requirements: Filesystem Detection

## Introduction

This feature adds filesystem detection for NTFS, exFAT, and unknown filesystems from boot-sector signatures. The system currently only detects FAT12/16/32 filesystems. When encountering other filesystems, the inventory shows a generic warning but doesn't specifically identify which non-FAT filesystem was detected. This enhancement will detect NTFS and exFAT from boot sector signatures and handle unknown filesystems gracefully with "detected, not readable" messages rather than crashing.

## Glossary

- **System**: Legacy Machine Rescue system
- **FAT Volume**: File Allocation Table formatted partition (FAT12, FAT16, FAT32)  
- **NTFS**: New Technology File System (Microsoft Windows filesystem)
- **exFAT**: Extended File Allocation Table (flash storage filesystem)
- **Boot Sector**: First sector of a partition containing filesystem metadata and boot code
- **Signature**: Characteristic pattern in the boot sector identifying the filesystem type
- **Partition**: Logical division of a disk image that may contain a filesystem
- **Synthetic Sector-Image**: Artificially created small disk sector images for testing
- **OEM String**: Bytes 3-11 of boot sector containing filesystem identifier
- **MBR**: Master Boot Record containing partition table at sector 0

## Requirements

### Requirement 1: NTFS Detection

**User Story:** As a technician, I want the system to detect NTFS filesystems from boot sector signatures, so that I can identify partitions that likely contain Windows NT-family operating systems.

#### Acceptance Criteria

1. WHEN a boot sector contains the ASCII string "NTFS    " (4E 54 46 53 20 20 20 20) at bytes 3:11, THE SYSTEM SHALL detect it as "NTFS (detected, not readable)"
2. WHEN NTFS is detected by OEM string, THE SYSTEM SHALL not attempt FAT parsing
3. THE SYSTEM SHALL preserve existing FAT12/16/32 detection unchanged (structural BPB parsing)
4. THE SYSTEM SHALL not imply Windows version is installed based on filesystem signature alone
5. PROPERTY: Any valid NTFS boot sector signature shall result in "NTFS (detected, not readable)" detection, not a crash

### Requirement 2: exFAT Detection

**User Story:** As a technician, I want the system to detect exFAT filesystems from boot sector signatures, so that I can identify flash storage or modern Windows removable media.

#### Acceptance Criteria

1. WHEN a boot sector contains the ASCII string "EXFAT   " (45 58 46 41 54 20 20 20) at bytes 3:11, THE SYSTEM SHALL detect it as "exFAT (detected, not readable)"
2. WHEN exFAT is detected by OEM string, THE SYSTEM SHALL not attempt FAT parsing
3. THE SYSTEM SHALL handle only the canonical exFAT OEM string "EXFAT   " with three trailing spaces
4. THE SYSTEM SHALL not imply Windows version is installed based on filesystem signature alone
5. PROPERTY: Any valid exFAT boot sector signature shall result in "exFAT (detected, not readable)" detection, not a crash

### Requirement 3: Unknown Filesystem Handling

**User Story:** As a technician, I want the system to handle unknown filesystems gracefully, so that I get clear information about what was detected rather than crashes or misleading warnings.

#### Acceptance Criteria

1. WHEN a boot sector does not match FAT, NTFS, or exFAT signatures, THE SYSTEM SHALL detect it as "Unknown filesystem (detected, not readable)"
2. WHEN filesystem detection fails, THE SYSTEM SHALL provide clear warning messages about detection results
3. THE SYSTEM SHALL never crash on damaged or unrecognized boot sector data
4. PROPERTY: Any input (including random bytes) shall result in either correct detection or a clear warning, never a crash

### Requirement 4: Boot Sector Signature Detection

**User Story:** As a developer, I want the system to implement boot sector signature detection that reads filesystem OEM strings, so that I can correctly identify different filesystem types.

#### Acceptance Criteria

1. THE SYSTEM SHALL read boot sector OEM strings at bytes 3:11
2. THE SYSTEM SHALL check for NTFS signature "NTFS    " at bytes 3:11
3. THE SYSTEM SHALL check for exFAT signature "EXFAT   " at bytes 3:11
4. THE SYSTEM SHALL handle MBR partition table offsets correctly (boot sector at partition LBA)
5. THE SYSTEM SHALL handle bare floppy images (no partition table) correctly
6. PROPERTY: Detection logic shall be pure string matching at bytes 3:11, no complex parsing required

### Requirement 5: Tiny Synthetic Sector-Image Tests

**User Story:** As a developer, I want synthetic sector-image tests for filesystem detection, so that I can verify detection logic without requiring real disk images.

#### Acceptance Criteria

1. THE SYSTEM SHALL include unit tests with synthetic sector images
2. THE TESTS SHALL cover NTFS signature detection cases using 512-byte fixtures
3. THE TESTS SHALL cover exFAT signature detection cases using 512-byte fixtures  
4. THE TESTS SHALL cover unknown filesystem detection cases
5. THE TESTS SHALL cover damaged/corrupted sector cases
6. THE TESTS SHALL allow multi-sector MBR partition fixtures when needed
7. PROPERTY: Synthetic tests shall be minimal and self-contained

### Requirement 6: Graceful Degradation

**User Story:** As a user, I want the system to provide clear inventory results even for unsupported filesystems, so that I know what was detected even if it can't be read.

#### Acceptance Criteria

1. WHEN NTFS is detected, THE INVENTORY SHALL say "NTFS (detected, not readable)" in the filesystem field
2. WHEN exFAT is detected, THE INVENTORY SHALL say "exFAT (detected, not readable)" in the filesystem field
3. WHEN unknown filesystem is detected, THE INVENTORY SHALL say "Unknown filesystem (detected, not readable)" in the filesystem field
4. THE INVENTORY SHALL continue processing other volumes in the image when one volume is unreadable
5. PROPERTY: Inventory output shall always contain clear filesystem identification for all detected volumes

### Requirement 7: No False Overlap Premise

**User Story:** As a developer, I want clear distinction between filesystem detection methods, so that NTFS and exFAT detection don't conflict or create false positives.

#### Acceptance Criteria

1. NTFS and exFAT OEM strings SHALL be treated as distinct with no overlap
2. THE SYSTEM SHALL verify canonical signatures: NTFS="NTFS    ", exFAT="EXFAT   "
3. THE SYSTEM SHALL not invent or accept variant signatures like "EXFAT  A"
4. PROPERTY: Detection logic shall have zero false positives between NTFS and exFAT signatures