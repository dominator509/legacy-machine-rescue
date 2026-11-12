import os, tempfile, unittest
from legacyrescue import inventory, image
from tests import fixtures


def _inspect(data):
    fd, p = tempfile.mkstemp(suffix=".img")
    os.write(fd, data)
    os.close(fd)
    try:
        return inventory.inspect(p)
    finally:
        os.remove(p)


class FixtureFat(unittest.TestCase):
    """These pass on the current code and guard the fixture builder itself."""

    def test_valid_fat12_fixture_is_readable(self):
        r = _inspect(fixtures.fat12_image())
        v = r["volumes"][0]
        self.assertEqual(v["fs"], "FAT12")
        self.assertEqual(v["files"], 2)
        self.assertIn("CONFIG.SYS", v["config_files"])
        self.assertIn("HASPDOS", v["config_files"]["CONFIG.SYS"])

    def test_fixture_flags_dongle_driver_from_config(self):
        r = _inspect(fixtures.fat12_image())
        cats = [h["category"] for h in r["hardware_flags"]]
        self.assertIn("Hardware key / dongle driver", cats)

    def test_unknown_data_gives_warning_not_crash(self):
        r = _inspect(fixtures.unknown_sector())
        self.assertEqual(r["volumes"], [])
        self.assertTrue(r["warnings"])


class FixtureSpec(unittest.TestCase):
    """Specification for the NTFS/exFAT/unknown inventory work (spec task 1).
    Skipped until Kiro implements it. Each case states the required behavior."""

    @unittest.skipUnless(hasattr(image, "unreadable"), "task 1 not implemented yet")
    def test_ntfs_is_detected_not_readable(self):
        r = _inspect(fixtures.ntfs_boot_sector())
        self.assertEqual(r["volumes"], [])
        self.assertEqual([u["fs"] for u in r["unreadable_volumes"]], ["NTFS"])
        self.assertEqual(r["unreadable_volumes"][0]["status"], "detected, not readable")

    @unittest.skipUnless(hasattr(image, "unreadable"), "task 1 not implemented yet")
    def test_exfat_is_detected_not_readable(self):
        r = _inspect(fixtures.exfat_boot_sector())
        self.assertEqual([u["fs"] for u in r["unreadable_volumes"]], ["exFAT"])

    @unittest.skipUnless(hasattr(image, "unreadable"), "task 1 not implemented yet")
    def test_ntfs_partition_in_mbr_disk(self):
        r = _inspect(fixtures.mbr_disk(0x07, fixtures.ntfs_boot_sector()))
        self.assertEqual(r["unreadable_volumes"][0]["fs"], "NTFS")
        self.assertIn("partition 1", r["unreadable_volumes"][0]["where"])


if __name__ == "__main__":
    unittest.main()
