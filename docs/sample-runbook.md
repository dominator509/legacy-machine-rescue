# Recovery runbook

Image: `synthetic-cnc-shop.img`  
Size: 33554432 bytes (raw)  
SHA-256: `e8a0047eb525b25ee37fd9763c71eca1463efeccf9780a8e5086b70089482316`

## 1. Evidence summary

| Claim | Status |
| --- | --- |
| Filesystem FAT16, 680 files readable | MEASURED |
| OS guess: FreeDOS | MEASURED (file names only) |
| Image boots in QEMU 6.2 | EMULATOR: OS output on screen after 15s: 'FreeDOS 1.3 - Install' |
| Works with the real motion card, dongle, serial/DNC link and CNC controller | **NOT PROVEN** |

An emulator boot proves the disk contents start. It does not prove real hardware works.

## 2. Risks found

- **Real-mode memory manager** (check on real machine). Evidence: FDCONFIG.SYS: UMB
- **CD-ROM / ASPI driver** (check on real machine). Evidence: FDAUTO.BAT: CDROM
- **Hardware key / dongle driver** (check on real machine). Evidence: file /CNC/HASPDOS.SYS
- **Motion/IO control card driver** (check on real machine). Evidence: file /CNC/PMACDRV.SYS
- Possible CNC-related files: /CNC/PART1.NC, /DNC/SEND.BAT

## 3. Steps

1. Do NOT write to the original disk. Make a sector-level copy of it first (for example `dd` or a hardware imager), then verify the SHA-256 above matches the copy.
2. Keep the original disk or machine powered off and labelled. Store one copy off-site.
3. Photograph the back of the PC: every card, cable and dongle, with the port it uses. Record BIOS settings (CMOS) too, since a dead battery can lose them.
4. Locate the hardware key and keep a spare plan: contact the software vendor about a replacement or license transfer. Test the key on the replacement PC before moving production.
5. Identify the motion/IO card model and IRQ/IO settings. These cannot be emulated; source a spare card or an equivalent board.
6. Boot a copy of the image in the emulator again before any real-hardware change, to confirm the image is intact.
7. Restore the verified copy to a spare drive (an IDE-to-CompactFlash adapter is common) and test it on the real machine with the machine's axes disabled or in a safe state.
8. Only after a supervised dry run, return the machine to production.

## 4. Disk inventory

- partition 1 (FAT16<32M): FAT16, label `FD13-LITE`, 680 files, OS: FreeDOS
  - Boot files present: COMMAND.COM, KERNEL.SYS
  - Has config file: FDCONFIG.SYS
  - Has config file: FDAUTO.BAT
