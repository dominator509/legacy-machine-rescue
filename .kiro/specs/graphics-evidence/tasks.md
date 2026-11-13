# Tasks

Authorship: outside Kiro.

- [x] Capture BDA mode and bounded pixel metadata with screenshots.
- [x] Add conservative graphics-output/boot-unverified result.
- [x] Preserve blank/no-boot failures.
- [x] Add generated pixel, failure, report and seeded negative tests.
- [x] Real QEMU8.2.2 TCG integration in a task-owned network namespace: synthetic mode13h blue/green boot sector, uniform-color negative, unsigned no-boot negative. All screenshots inspected. These fixtures are not operating systems. Reproduce with `python -m tools_demo.verify_graphics new-output-dir` on a local QEMU host.
- [ ] Independent OS identity/boot evidence before any Win9x or boot-success claim.
- [ ] Parent review and publication.
