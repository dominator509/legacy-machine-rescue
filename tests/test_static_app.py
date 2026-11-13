import os, subprocess, sys, unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class StaticApp(unittest.TestCase):
    def test_build_creates_browser_files(self):
        subprocess.check_call([sys.executable, "-m", "tools_demo.build_static_app"], cwd=ROOT)
        for p in ("docs/app/sample.img", "docs/app/py/manifest.json", "docs/app/py/legacyrescue/inventory.py"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, p)), p)
        # the browser build must not ship the emulator or web modules
        self.assertFalse(os.path.exists(os.path.join(ROOT, "docs/app/py/legacyrescue/boot.py")))

    def test_sample_reads_as_fat12_with_dongle_flag(self):
        from legacyrescue import inventory
        subprocess.check_call([sys.executable, "-m", "tools_demo.build_static_app"], cwd=ROOT)
        r = inventory.inspect(os.path.join(ROOT, "docs/app/sample.img"))
        self.assertEqual(r["volumes"][0]["fs"], "FAT12")
        self.assertIn("Hardware key / dongle driver", [h["category"] for h in r["hardware_flags"]])


if __name__ == "__main__":
    unittest.main()
