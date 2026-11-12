# Legacy Machine Rescue

Many machine shops still run CNC machines from Windows 98 or DOS PCs. When that disk dies, production stops. This tool takes a disk image, checks it, boots it in an emulator, and writes a recovery runbook.

**What it proves:** the disk contents were read, and the image did or did not start in a PC emulator (QEMU), with screenshots and the text on the guest screen as evidence.
**What it does not prove:** that the real motion card, hardware key (dongle), serial/DNC link or CNC controller will work. Every report says so. Those items are listed as risks to check on the real machine.

## Use

Requires Python 3.8+ and QEMU (`qemu-system-x86_64`). If QEMU is unpacked outside the system path, set `LEGACYRESCUE_QEMU_ROOT` to the unpacked root.

    python -m legacyrescue disk.img                       # inventory + runbook (no emulator)
    python -m legacyrescue disk.img --boot --html report.html -o runbook.md

Web app: `python -m legacyrescue.web 8080` then open http://127.0.0.1:8080 (upload an image, get the report).

Supported input: raw floppy or hard-disk images (FAT12/16/32, MBR), fixed VHD. Dynamic VHD, VMDK, QCOW2 and NTFS are detected but not read (the tool says so).
The source image is never modified (the emulator runs in snapshot mode, with no network).

## What the runbook contains

Evidence table (MEASURED / EMULATOR / NOT PROVEN), risks (dongle drivers, motion-card drivers, serial/DNC use, memory managers, CD-ROM drivers, CNC files), and ordered recovery steps (copy and hash the disk, photograph hardware, restore to a spare drive, supervised dry run).

## Tests

    python -m unittest
    python -m tools_demo.corpus 8     # builds a corpus and checks boot results against how each image was made

The corpus uses the free FreeDOS 1.3 images (GPL) and images derived from them, including a clearly labelled synthetic CNC-style disk and negative controls (blank, wiped boot sector, random bytes). No real shop data is used.

License: MIT.
