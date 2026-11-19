# Real CNC sample smoke tests (local only)

Checked 2026-11-18. All samples were downloaded or copied to temporary FAT12 images outside the repository. No sample bytes, extracted program text, real names, or PII are committed. No G-code was executed.

## Inputs and provenance

| Sample | Source | Size | SHA-256 | Rights notes |
| --- | --- | ---: | --- | --- |
| LinuxCNC `tool-length-probe.ngc` | https://github.com/LinuxCNC/linuxcnc/blob/master/nc_files/tool-length-probe.ngc | 1,228 bytes | `4e22133ff0f9911f4138d5a26ac916a45553450fd28d0ed35d877e7c5c9062e6` | LinuxCNC repository `COPYING` is GNU GPL v2. No file-specific header was identified. Kept local; sample not redistributed here. |
| Mazak Quick Turn 100-MSY sample | https://cam.autodesk.com/posts/view.php?name=mazak+quick+turn+100-msy | 2,209 bytes | `f300151edbdd7a5e0ff1bebef0d4f8fcd89f3cfae528b792986ba23c33432b9c` | No sample license or redistribution grant identified. Kept local. Autodesk marks omitted blocks and review-only use. The `.nc` filename was a local alias, not a source-provided filename. |
| Clough42 `GenericKeyway.tap` | https://github.com/clough42/cnc-shaping/blob/master/GenericKeyway.tap | 1,862 bytes | `350773cf81b1d1bf038f97626224ef02bde91138db4ebc32c8ec0b79a8007fe7` | Source repository has an MIT license; no separate license for this program was identified. Kept local. |
| Heidenhain `drill_7_20.h` | https://github.com/polymech-info/heidenhain-simulator/blob/main/samples/common/drill_7_20.h | 852 bytes | `c53008f28ba177decf3bc0dd825dc997820a34cd0841329a03621c8104d48d66` | Source page describes the programs as from real manufacturing jobs. No `LICENSE` or `COPYING` file was found in the source checkout. Kept in a temporary local copy. |
| EMCO Fanuc 0/21 sample collection attachment | https://forums.autodesk.com/autodesk/attachments/autodesk/fusion-manufacture-forum-en/150526/3/Sample%20NC%20File%20for%20FANUC%2021.txt | 3,030 bytes | `b0860dd6e417364a3ed0eb4ca69c34ff11cd2b34e3bb250744a5555b781bbc4d` | No sample license or redistribution grant identified. The attachment is training material with multiple program examples, not one controller-native file. Kept local. |
| Fanuc O0007 test fragment, derived locally from the attachment above | Same Autodesk attachment URL | 198 bytes | `9a00bf086c37666be9968c2c8c789cb0fdb0aa2b9cda1f72feb1f0fe5b69bc90` | Temporary extracted fragment used only to test an extensionless controller filename. It is not committed or redistributed. |
| Haas Mill Tool Breakage Detection `.NC` | https://github.com/zodiaceng/haas-mill-programs/blob/main/Haas%20Mill%20Tool%20Breakage%20Detection.NC | 1,573 bytes | `d13f77cc5d14b47b7b2f5b8e38543b9aad47868a745a5e7cb89489fb057282d6` | No repository license file or sample-specific redistribution grant identified on the source page. The README cautions that machine compatibility is not guaranteed. Kept local. |
| Mach3 `z200.tap` | https://github.com/tungbuivn/mach3-autolevel/blob/master/z200.tap | 2,933 bytes | `04811beb42a469ef98cbc791c9e00693379314f7f31e7cb872c60f9f47a5ad17` | No repository license file or sample-specific redistribution grant identified on the source page. Kept local. |
| Apsu `sample.gcode` (3D-printer-style G-code, not CNC milling) | https://github.com/Apsu/gcode/blob/master/sample.gcode | 4,841 bytes | `ffa8a09d98621fc00ddf107003f03c46c7dad856364dd94ba564b019cf7a2b06` | The repository page has no declared license, and no sample-specific redistribution grant was identified. Kept local. Used only to test the `.gcode` extension hint. |

## Method and results

The first four files were copied to a temporary FAT12 image using short aliases `LINUXCNC.NGC`, `MAZAK.NC`, `GENKEY.TAP`, and `DRILL.H`. The `.ngc`, `.nc`, and `.tap` files were detected by extension. The Heidenhain `.H` file was initially missed, then detected after a narrow signature check requiring `BEGIN PGM` plus `TOOL CALL` or `CYCL DEF` in the first 8 KiB.

The follow-up image was a separate 1.44 MB FAT12 copy containing `FANUC21.TXT`, `HAASBRK.NC`, `Z200.TAP`, and extensionless `O0007`. The Haas and Mach3 files were detected by `.NC` and `.TAP` extensions. `FANUC21.TXT` was included in the CNC-related file list due to `FANUC` in the basename, but did not produce a structured program finding. The attachment says that its Fanuc examples use an O-number filename without an extension. A 198-byte O0007 fragment extracted to a temporary extensionless file was initially missed. A narrow Fanuc signature then recognized it: an O-number header, at least three numbered G/M blocks, and an M30 or M99 end block, all within an 8 KiB prefix. This is a hint, not a parser.

A GRBL simulator `test.nc` (249 bytes, SHA-256 `9a0000e8383995d5a537c33f7928e3a1bd08ff7d83f823b1131428dbbd33b571`) was copied to a separate FAT12 image as `GRBL.NC` and detected by extension. The GRBL simulator repository declares GPL-3.0. A separate 4,841-byte Apsu `sample.gcode` (SHA-256 `ffa8a09d98621fc00ddf107003f03c46c7dad856364dd94ba564b019cf7a2b06`) is 3D-printer-style G-code, not CNC milling. Before the `.gcode` extension was added, the name appeared in the CNC-related filename list but produced no structured finding. The extension check now reports it as a possible G-code program rather than a CNC program, avoiding a false claim about additive-machine samples. The sample was used locally only, with no execution or compatibility claim.

The suite after adding the Fanuc signature and positive/negative regression tests: 166 tests run, 2 skipped because the optional FreeDOS image is absent, all remaining tests passed. Existing image-file ResourceWarnings remain.

## Caveats

These checks do not validate G-code or Klartext syntax, controller compatibility, execution, machine hardware, dongles, motion cards, DNC, or any real shop disk. The Autodesk Mazak example is incomplete and review-only, so it is not an execution test. The temporary test images contained copies only; no program was run or simulated. The extension and signature matches do not establish that any program is safe or compatible with a machine.

## Code changes

The inventory now uses bounded prefix reads only for `.H` files and filenames that are exactly an O-number with four digits. The Heidenhain signature is narrow; the Fanuc signature requires the O-number header, at least three numbered G/M blocks, and an M30 or M99 end block. The program extension list also includes `.gcode`, which is reported as a possible G-code program rather than a CNC program, and was tested with a local-only 3D-printer-style sample. Negative tests cover unrelated C-header text, generic `.H` motion text, `.H` without content, `.HPP`, extensionless O-number names without the Fanuc signature, and an `O####.TXT` filename. Regression fixtures use synthetic text, not third-party sample contents. No sample bytes or PII are committed. No G-code was executed.
