# Tasks: disk inspection
- [x] FAT12/16/32 reader (legacyrescue/fat.py)
- [x] Container and partition detection (image.py)
- [x] Inventory and hardware keyword flags (inventory.py)
- [x] Fuzz test (tests/test_fuzz.py)
- [x] Add exFAT/NTFS/unknown detection with a clear "not parsed" warning
  - Delivered by the filesystem-detection spec as `image.unreadable()` /
    `inventory.py`'s `unreadable_volumes`, not the `detect_all_volumes()`/
    `unsupported` design that spec originally proposed. See
    `.kiro/specs/filesystem-detection/tasks.md` for the as-shipped summary.
- [x] Dynamic VHD reader
  - Delivered by the dynamic-vhd-reader spec: `legacyrescue/image.py`
    parses and validates the VHD footer/header/BAT and reconstructs flat
    bytes, capped at a 256 MiB virtual size. Larger or malformed dynamic
    VHDs raise `ValueError`. See `.kiro/specs/dynamic-vhd-reader/tasks.md`.
- [ ] Windows 95/98 detection tests using a legally redistributable image
