import os
import tempfile
import unittest
from legacyrescue import boot, report, runbook
from tests.test_runbook_safety import report as inventory_report


def ppm(path, width=160, height=100, pattern=True):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            color = (30, 60, 120) if not pattern or x < width // 2 else (180, 190, 210)
            pixels.extend(color)
    with open(path, "wb") as f:
        f.write(("P6\n%d %d\n255\n" % (width, height)).encode() + pixels)


class BootGraphicsTests(unittest.TestCase):
    def test_generated_nonblank_graphics_is_evidence_not_os_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "generated.ppm")
            ppm(path)
            evidence = boot.pixel_evidence(path)
            png = os.path.join(tmp, "generated.png")
            self.assertEqual(boot._png(path, png), (160, 100))
            self.assertTrue(evidence["nonblank"])
            self.assertEqual(evidence["sampled_colors"], 2)
            frame = {"t": 25, "text": "", "bios_video_mode": 19, "pixels": evidence, "png": png}
            result = boot.judge([frame])
            self.assertTrue(result["graphics_output"])
            self.assertFalse(result["booted"])
            self.assertEqual(result["boot_status"], "unverified")
            self.assertIn("not proven", result["evidence"])

    def test_uniform_color_is_blank_for_graphics_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "blank.ppm")
            ppm(path, pattern=False)
            evidence = boot.pixel_evidence(path)
        self.assertFalse(evidence["nonblank"])
        result = boot.judge([{"t": 25, "text": "", "bios_video_mode": 19, "pixels": evidence}])
        self.assertFalse(result["booted"])
        self.assertFalse(result["graphics_output"])

    def test_nonblank_alone_does_not_establish_graphics_mode(self):
        for mode in [None, 3, 7, 255]:
            result = boot.judge([{"t": 25, "text": "", "bios_video_mode": mode,
                                 "pixels": {"nonblank": True}}])
            self.assertFalse(result["booted"])
            self.assertFalse(result["graphics_output"])

    def test_boot_errors_and_bios_keep_failure_precedence(self):
        for text in ["No bootable device", "Boot failed", "Invalid system disk"]:
            result = boot.judge([{"t": 25, "text": text, "bios_video_mode": 19,
                                 "pixels": {"nonblank": True}}])
            self.assertFalse(result["booted"])
            self.assertFalse(result.get("graphics_output", False))
            self.assertIn("screen shows", result["evidence"])
        self.assertFalse(boot.judge([{"t": 25, "text": "SeaBIOS version test"}])["booted"])

    def test_text_boot_and_empty_input_preserve_existing_behavior(self):
        self.assertTrue(boot.judge([{"t": 25, "text": "FreeDOS\nC:\\>"}])["booted"])
        self.assertFalse(boot.judge([])["booted"])

    def test_malformed_pixel_inputs_are_clear_errors(self):
        cases = [b"", b"P6\n0 1\n255\n", b"P6\n999999 999999\n255\n",
                 b"P6\n1 1\n255\n\x00\x00", b"P3\n1 1\n255\n0 0 0",
                 b"P6\n1 1\n255\n\x00\x00\x00extra"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.ppm")
            for data in cases:
                with open(path, "wb") as f:
                    f.write(data)
                with self.assertRaises(ValueError):
                    boot.pixel_evidence(path)

    def test_graphics_report_and_runbook_never_use_boot_success_badge(self):
        inv = inventory_report()
        result = {"booted": False, "graphics_output": True, "emulator": "synthetic test",
                  "evidence": "generated graphics test; OS not proven", "screenshots": []}
        md = runbook.build(inv, result)
        self.assertIn("OS boot and Win9x identity | NOT PROVEN", md)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.html")
            report.build(inv, result, md, path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("GRAPHICS OUTPUT - BOOT UNVERIFIED", text)
        self.assertNotIn("BOOTED IN EMULATOR", text)
        self.assertNotIn("DID NOT BOOT", text)

    def test_seeded_uniform_images_never_prove_graphics(self):
        import random
        rng = random.Random(41)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "uniform.ppm")
            for _ in range(30):
                width, height = rng.randint(1, 90), rng.randint(1, 90)
                color = bytes(rng.randrange(256) for _ in range(3))
                with open(path, "wb") as f:
                    f.write(("P6\n%d %d\n255\n" % (width, height)).encode() + color * width * height)
                self.assertFalse(boot.pixel_evidence(path)["nonblank"])
