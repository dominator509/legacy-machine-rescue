# Real CNC sample smoke test (local-only)

Checked 2026-11-18 against main commit `5f37f4e36880a1c75fe1727e44dd42300e7f6e8a`. This records actual public sample inputs used for a file-discovery smoke check. Source sample bytes are not included in this repository or PR.

## Inputs and provenance

| Sample | Source | Size | SHA-256 | Rights notes |
| --- | --- | ---: | --- | --- |
| LinuxCNC `tool-length-probe.ngc` | https://github.com/LinuxCNC/linuxcnc/blob/master/nc_files/tool-length-probe.ngc | 1228 bytes | `4e22133ff0f9911f4138d5a26ac916a45553450fd28d0ed35d877e7c5c9062e6` | LinuxCNC repository `COPYING` is GNU GPL v2. No file-specific header was identified. Kept local; sample not redistributed here. |
| Mazak Quick Turn 100-MSY sample | https://cam.autodesk.com/posts/view.php?name=mazak+quick+turn+100-msy | 2209 bytes | `f300151edbdd7a5e0ff1bebef0d4f8fcd89f3cfae528b792986ba23c33432b9c` | Autodesk page presents a sample, but no sample license/redistribution grant was identified. Kept local; sample not redistributed here. Page says blocks are skipped and the program is for review only. Its `.nc` filename was a local alias, not a source-provided filename. |

## Method and result

The sample bytes were copied into a temporary FAT12 image under short names `LINUXCNC.NGC` and `MAZAK.NC` because the simple FAT12 fixture helper uses 8.3 names. The temporary image and original downloaded sample files were outside the repository. The CLI was run with `python -m legacyrescue <temporary-image>`.

**Pass, limited scope:** both `.ngc` and `.nc` were found and reported as possible CNC programs. The runbook preserved the existing warning that matching is based on filename/extension only and does not prove the program or machine works. The ordinary unit suite also passed: 162 tests, 2 skipped (optional FreeDOS image missing).

**Not tested:** program syntax, controller dialect compatibility, execution/simulation, physical machine, dongle, motion card, DNC, or a real shop's disk image. The Autodesk sample is incomplete by design, so it is unsuitable as an execution test. LinuxCNC's sample is likewise used here only as data for filename-based inventory; no G-code was run.

## Code changes

Adds a small regression assertion for the two sample-shaped filenames. No sample code or content is committed. No production code fix was needed for these suffixes. This is not a claim that Legacy Machine Rescue has been proven to work on real shop software or hardware.
