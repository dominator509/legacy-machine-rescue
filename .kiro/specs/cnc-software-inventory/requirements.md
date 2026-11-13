# CNC software inventory requirements

Authorship: implemented and documented outside Kiro on 2026-11-13 while its current usage and desktop were unavailable. These files record the work; they are not evidence of a Kiro spec run.

- Flag possible serial/DNC tools, controller configuration, hardware-key drivers, motion/IO drivers and CNC program extensions.
- Match filenames or extensions only, never directory names or file contents.
- Every finding includes full path, volume, category, match reason and a needs-real-hardware-check note.
- Never claim the software works or that a filename proves physical compatibility.
- Preserve existing inventory consumers and FAT behavior. Test synthetic data only.
