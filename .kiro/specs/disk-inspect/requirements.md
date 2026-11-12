# Requirements: disk inspection

1. WHEN a raw floppy or hard-disk image is given, THE SYSTEM SHALL detect MBR partitions and FAT12/16/32 volumes.
2. WHEN a fixed VHD is given, THE SYSTEM SHALL strip the footer and read it as raw.
3. WHEN the container is a dynamic VHD, VMDK, QCOW2 or VDI, THE SYSTEM SHALL say it is unsupported and ask for a raw conversion.
4. THE SYSTEM SHALL identify DOS-family boot files and name the likely OS from file names only (MEASURED).
5. THE SYSTEM SHALL flag files and config lines that suggest hardware keys, serial/DNC links, motion or IO cards, memory managers, CD-ROM drivers, and list CNC-related files. Flags are keyword hints, not proof.
6. IF the image is damaged, truncated or random, THEN THE SYSTEM SHALL return a warning and no crash.
7. PROPERTY: for any byte-mutated or truncated copy of a valid image, inspection returns or raises ValueError, never another exception.
