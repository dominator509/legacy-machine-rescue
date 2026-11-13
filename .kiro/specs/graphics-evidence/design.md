# Design

Capture the BIOS Data Area video-mode byte at 0x449 through QMP pmemsave. Combine a known legacy graphics mode with nonuniform P6 screendump pixels. Pixel analysis samples at most 65536 pixels and requires at least 1 percent and 64 nonbackground samples; it is a heuristic, not visual OS recognition. Uniform colors, unknown modes and missing samples do not pass. Explicit text boot errors retain precedence. BIOS Data Area metadata can be stale or guest-controlled and is not proof of active VGA state or OS boot. Modern VBE/direct-register modes may be missed.

Return graphics_output=true with booted=false,boot_status=unverified. The HTML badge and runbook distinguish this from both confirmed text boot and did-not-boot. No Win9x likely claim is made.

Source reference for the BDA field: https://www.stanislavs.org/helppc/video_information.html (40:49 current video mode, equivalent to physical0x449).
