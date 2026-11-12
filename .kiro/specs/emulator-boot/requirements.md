# Requirements: emulator boot test

1. WHEN asked to boot an image, THE SYSTEM SHALL start QEMU headless with TCG, no network, snapshot mode, and a fixed time limit.
2. THE SYSTEM SHALL capture timed screenshots through QMP and read the guest text screen (VGA text memory).
3. THE SYSTEM SHALL decide "booted" only from evidence: OS text on screen, and no BIOS boot-failure text.
4. WHEN the screen stays blank or shows a boot-failure message, THE SYSTEM SHALL report "did not boot" with the evidence.
5. THE SYSTEM SHALL label the result EMULATOR and state it is not hardware proof.
6. NEGATIVE CONTROLS: a blank disk, a disk with a wiped boot sector, and random bytes SHALL all report "did not boot".
