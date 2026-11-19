# Real CNC sample smoke test (local-only)

Checked 2026-11-18. The first two sample runs used the public `main` commit `5f37f4e36880a1c75fe1727e44dd42300e7f6e8a`. The follow-up `.tap` and Heidenhain `.H` runs used the open branch `test/real-cnc-sample-smoke`. No sample bytes are included in this repository or PR.

## Inputs and provenance

| Sample | Source | Size | SHA-256 | Rights notes |
| --- | --- | ---: | --- | --- |
| LinuxCNC `tool-length-probe.ngc` | https://github.com/LinuxCNC/linuxcnc/blob/master/nc_files/tool-length-probe.ngc | 1,228 bytes | `4e22133ff0f9911f4138d5a26ac916a45553450fd28d0ed35d877e7c5c9062e6` | LinuxCNC repository `COPYING` is GNU GPL v2. No file-specific header was identified. Kept local; sample not redistributed here. |
| Mazak Quick Turn 100-MSY sample | https://cam.autodesk.com/posts/view.php?name=mazak+quick+turn+100-msy | 2,209 bytes | `f300151edbdd7a5e0ff1bebef0d4f8fcd89f3cfae528b792986ba23c33432b9c` | Autodesk page presents a sample, but no sample license/redistribution grant was identified. Kept local; sample not redistributed here. Page marks omitted blocks and review-only use. The `.nc` filename was a local alias, not a source-provided filename. |
| Clough42 `GenericKeyway.tap` | https://github.com/clough42/cnc-shaping/blob/master/GenericKeyway.tap | 1,862 bytes | `350773cf81b1d1bf038f97626224ef02bde91138db4ebc32c8ec0b79a8007fe7` | The source repository has an MIT license; no separate license for this program was identified. Kept local; sample not redistributed here. |
| Heidenhain `drill_7_20.h` | https://github.com/polymech-info/heidenhain-simulator/blob/main/samples/common/drill_7_20.h | 852 bytes | `c53008f28ba177decf3bc0dd825dc997820a34cd0841329a03621c8104d48d66` | The linked source page describes the sample programs as from real manufacturing jobs. No `LICENSE` or `COPYING` file was found in the source checkout. Used only in a temporary local copy; no sample bytes or program text are redistributed here. |

## Method and result

All four source files were copied to a temporary FAT12 image outside the repository, using short aliases `LINUXCNC.NGC`, `MAZAK.NC`, `GENKEY.TAP`, and `DRILL.H` because the simple FAT12 fixture helper uses 8.3 names. The CLI was run with `python -m legacyrescue <temporary-image>`. A combined run found all four files as possible CNC programs.

**Pass, limited scope:** `.ngc`, `.nc`, and `.tap` were detected by extension. The Heidenhain `.H` file was initially missed by filename/extension-only rules. A follow-up code change recognizes only `.H` files whose first 8 KiB contain both a `BEGIN PGM` header and a `TOOL CALL` or `CYCL DEF` command. The test suite also checks that a plain C header, an `.H` file with only a generic motion line, an `.H` filename without supplied content, and `.HPP` do not match this signature. The runbook still marks all findings as not proven to work with any controller or hardware.

The ordinary unit suite passed after the change: 162 tests passed and 2 were skipped because the optional FreeDOS image is absent.

**Not tested:** G-code/Klartext syntax correctness, controller dialect compatibility, execution or simulation, physical machine, dongle, motion card, DNC, or a real shop disk image. The Autodesk sample is incomplete by design and is unsuitable for an execution test. LinuxCNC, Clough42, and Heidenhain samples were used only as inputs to the file inventory; no program was executed. The content signature is a narrow detection heuristic, not a parser or compatibility check.

## Code changes

Adds a bounded FAT-file prefix read used only for `.H` signature checks and a narrow Heidenhain content signature. It deliberately does not flag every `.h` file, since that suffix is also used for C/C++ headers. The regression test uses generic synthetic text, not any third-party sample content. No sample bytes or real names/PII are committed. No G-code was executed.
