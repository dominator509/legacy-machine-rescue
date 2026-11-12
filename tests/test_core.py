import os, unittest
from legacyrescue import inventory, runbook
IMG = os.path.join(os.path.dirname(__file__), "..", "images", "144m", "x86BOOT.img")

class T(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(IMG), "FreeDOS image not downloaded")
    def test_freedos_boot_floppy(self):
        r = inventory.inspect(IMG)
        v = r["volumes"][0]
        self.assertEqual(v["fs"], "FAT12")
        self.assertEqual(v["os"], "FreeDOS")
        self.assertIn("KERNEL.SYS", v["boot_files_present"])
        self.assertIn("NOT PROVEN", runbook.build(r))
