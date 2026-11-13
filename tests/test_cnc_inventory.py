import unittest
import tempfile
import os
from unittest.mock import patch
from legacyrescue import inventory, runbook
from tests import fixtures


class CncInventoryTests(unittest.TestCase):
    def test_filename_rules_have_path_reason_and_hardware_warning(self):
        cases = [("/BIN/DNCPLUS.EXE", "Serial/DNC transfer"),
                 ("/SETUP/FANUC.CFG", "Controller configuration"),
                 ("/DRIVERS/HASPVDD.DLL", "Hardware key / dongle driver"),
                 ("/DRIVERS/PMAC.SYS", "Motion/IO control card driver")]
        for path, category in cases:
            with self.subTest(path=path):
                item = next(f for f in inventory.cnc_file_findings(path, "partition 1")
                            if f["category"] == category)
                self.assertEqual(item["path"], path)
                self.assertEqual(item["where"], "partition 1")
                self.assertTrue(item["reason"])
                self.assertEqual(item["matched_by"], "filename")
                self.assertIn("Needs real hardware check", item["note"])
                self.assertIn("not proof the software works", item["note"])

    def test_program_extensions_are_case_insensitive(self):
        for extension in ["NC", "tap", "NgC", "MPF"]:
            result = inventory.cnc_file_findings("/PARTS/123." + extension)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["matched_by"], "extension")

    def test_directory_names_do_not_match_files(self):
        for path in ["/DNC/HASP/PMAC/FANUC/readme.txt", "DNC\\HASPVDD\\readme.txt",
                     "/DOCS/HASP.TXT", "/SERIAL/readme.exe", "/PARTS/part.nc.bak"]:
            self.assertEqual(inventory.cnc_file_findings(path), [])

    def test_no_content_or_directory_lookup(self):
        # Deliberately pass only a filename; the helper has no content input.
        with patch("builtins.open", side_effect=AssertionError("must not read content")):
            self.assertEqual(inventory.cnc_file_findings("ordinary.exe"), [])
            self.assertTrue(inventory.cnc_file_findings("dnc.exe"))

    def test_inventory_includes_structured_findings_and_keeps_compatibility(self):
        class Volume:
            kind, label, oem, total, bps = "FAT12", "SYNTHETIC", "TEST", 1, 512
            def walk(self):
                for path in ["/TOOLS/DNC.EXE", "/CONF/FANUC.CFG", "/PMAC/readme.exe", "/PARTS/a.nc"]:
                    yield path, {"name": path.rsplit("/", 1)[-1], "dir": False, "size": 0}
        with patch.object(inventory.img, "load", return_value=(b"synthetic", {})), \
             patch.object(inventory.img, "partitions", return_value=[]), \
             patch.object(inventory.img, "volumes", return_value=[("synthetic volume", Volume())]), \
             patch.object(inventory.img, "unreadable", return_value=[]), \
             patch.object(inventory.img, "boot_code_hint", return_value="synthetic"):
            result = inventory.inspect("synthetic-only.img")
        self.assertEqual(len(result["cnc_findings"]), 3)
        self.assertIn("/TOOLS/DNC.EXE", result["cnc_files"])
        self.assertIn("/CONF/FANUC.CFG", result["cnc_files"])
        self.assertNotIn("/PMAC/readme.exe", result["cnc_files"])
        self.assertTrue(all(f["where"] == "synthetic volume" for f in result["cnc_findings"]))



    def test_seeded_directory_names_never_change_filename_matches(self):
        import random
        rng = random.Random(731)
        for _ in range(150):
            prefix = "/".join(rng.choice(["DNC", "HASP", "PMAC", "FANUC", "plain"])
                              for _ in range(rng.randint(1, 6)))
            name = rng.choice(["ordinary.exe", "dnc.exe", "FANUC.CFG", "x.nc", "HASPVDD.DLL"])
            plain = inventory.cnc_file_findings(name)
            nested = inventory.cnc_file_findings(prefix + "/" + name)
            self.assertEqual([(f["category"], f["reason"], f["matched_by"]) for f in plain],
                             [(f["category"], f["reason"], f["matched_by"]) for f in nested])

    def test_real_synthetic_fat_image_and_runbook_output(self):
        data = fixtures.fat12_image({"DNC.EXE": b"synthetic only", "FANUC.CFG": b"synthetic",
                                     "HASPDOS.SYS": b"synthetic", "PMAC.SYS": b"synthetic",
                                     "PART.NC": b"synthetic", "README.TXT": b"DNC HASP PMAC"})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "synthetic.img")
            with open(path, "wb") as f:
                f.write(data)
            result = inventory.inspect(path)
            result["image"]["path"] = "synthetic.img"
            md = runbook.build(result)
        self.assertEqual(len(result["cnc_findings"]), 5)
        self.assertFalse(any(f["path"].endswith("README.TXT") for f in result["cnc_findings"]))
        for finding in result["cnc_findings"]:
            self.assertIn(finding["path"], md)
            self.assertIn(finding["reason"], md)
            self.assertIn(finding["note"], md)
        self.assertIn("NOT PROVEN", md)
        self.assertIn("NOT RUN", md)


if __name__ == "__main__":
    unittest.main()
