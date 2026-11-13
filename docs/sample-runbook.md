# Recovery runbook

Image: `synthetic-demo.img`
Size: 368640 bytes (raw)
SHA-256: `8a476deb0c0f0b5f4156230f55aacc5e2c16947820a1731cc4f6e47a7f5909fc`

## Before you touch the machine

- Do not write to the original drive. Make a sector-level copy first, keep the original labelled, and verify the copy against the recorded SHA-256 before recovery work.
- Record the controller settings, BIOS settings and serial/DNC settings before changing anything. Keep an offline copy of the settings and recovery image.
- Photograph every cable, port, card, jumper and hardware key in place. Label connections before disconnecting them.
- Have a qualified technician put the machine in a safe state before physical work. An emulator result is not permission to energize or move the machine.

## 1. Evidence summary

| Claim | Status |
| --- | --- |
| Filesystem FAT12, 4 files readable | MEASURED |
| OS guess: Unknown | MEASURED (file names only) |
| Image boots in emulator | NOT RUN |
| Works with the real motion card, dongle, serial/DNC link and CNC controller | **NOT PROVEN** |

An emulator boot proves the disk contents start. It does not prove real hardware works.

## 2. Risks found

- WARNING: No recognised DOS/Windows 9x boot file set was found.
- **Hardware key / dongle driver** (check on real machine). Evidence: CONFIG.SYS: HASP
- **Real-mode memory manager** (check on real machine). Evidence: CONFIG.SYS: HIMEM
- **Serial/DNC transfer (COM port)** (check on real machine). Evidence: AUTOEXEC.BAT: MODE COM
- **Possible CNC program**: `/PART1.NC` (whole image). Reason: file extension .nc may contain a CNC program. Needs real hardware check; filename/extension match only, not proof the software works.
- Possible CNC-related files (filename/extension hints only): /PART1.NC

## Not proven by emulator

- Possible CNC program: `/PART1.NC` (whole image). NOT PROVEN: Needs real hardware check; filename/extension match only, not proof the software works.
- Hardware key / dongle driver: CONFIG.SYS: HASP. NOT PROVEN: requires a check on the real machine.
- Real-mode memory manager: CONFIG.SYS: HIMEM. NOT PROVEN: requires a check on the real machine.
- Serial/DNC transfer (COM port): AUTOEXEC.BAT: MODE COM. NOT PROVEN: requires a check on the real machine.
- Real motion/IO, hardware key/licensing, serial/DNC transfer and controller operation remain NOT PROVEN even when no matching filename was found. The inventory rules are incomplete hints, not an absence check.

## 3. Steps

1. Do NOT write to the original disk. Make a sector-level copy of it first (for example `dd` or a hardware imager), then verify the SHA-256 above matches the copy.
2. Keep the original disk or machine powered off and labelled. Store one copy off-site.
3. Photograph the back of the PC: every card, cable and dongle, with the port it uses. Record BIOS settings (CMOS) too, since a dead battery can lose them.
4. Locate the hardware key and keep a spare plan: contact the software vendor about a replacement or license transfer. Test the key on the replacement PC before moving production.
5. Note COM port numbers, baud rate, parity and handshake used for DNC. Use a USB-serial adapter only after testing a full program transfer.
6. Run the emulator boot test. If it fails, the image may be damaged, need a different BIOS/CPU setting, or rely on hardware.
7. Restore the verified copy to a spare drive (an IDE-to-CompactFlash adapter is common) and test it on the real machine with the machine's axes disabled or in a safe state.
8. Only after a supervised dry run, return the machine to production.

## 4. Disk inventory

- whole image: FAT12, label `TESTDISK`, 4 files, OS: Unknown
  - Boot files present: CONFIG.SYS, AUTOEXEC.BAT
  - Has config file: CONFIG.SYS
  - Has config file: AUTOEXEC.BAT
