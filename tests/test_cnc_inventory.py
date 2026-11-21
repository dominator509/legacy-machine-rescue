import unittest
import tempfile
import os
from unittest.mock import patch
from legacyrescue import inventory, runbook
from tests import fixtures


class CncInventoryTests(unittest.TestCase):
    def test_heidenhain_h_program_signature_is_detected_without_broad_h_suffix_matching(self):
        program = (b"0 BEGIN PGM DRILL MM\n"
                   b"10 TOOL CALL 10 Z S1247\n"
                   b"18 CYCL DEF 200 DRILLING\n"
                   b"24 M30\n"
                   b"25 END PGM DRILL MM\n")
        result = inventory.cnc_file_findings("/PARTS/DRILL.H", content=program)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "Possible CNC program")
        self.assertEqual(result[0]["matched_by"], "content")
        self.assertIn("Heidenhain", result[0]["reason"])
        self.assertIn("not proof the software works", result[0]["note"])

        for path, content in [
            ("/INCLUDE/MOTOR.H", b"#ifndef MOTOR_H\n#define MOTOR_H\n"),
            ("/INCLUDE/DRILL.H", b"BEGIN PGM DRILL MM\nL X+0 Y+0 FMAX\nEND PGM DRILL MM\n"),
            ("/PARTS/DRILL.H", None),
            ("/PARTS/DRILL.HPP", program),
        ]:
            with self.subTest(path=path):
                self.assertEqual(inventory.cnc_file_findings(path, content=content), [])

    def test_inspect_reports_heidenhain_signature_in_fat_image(self):
        program = (b"0 BEGIN PGM DRILL MM\n"
                   b"10 TOOL CALL 10 Z S1247\n"
                   b"18 CYCL DEF 200 DRILLING\n"
                   b"24 M30\n"
                   b"25 END PGM DRILL MM\n")
        data = fixtures.fat12_image({"DRILL.H": program})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "heidenhain.img")
            with open(path, "wb") as f:
                f.write(data)
            result = inventory.inspect(path)
        self.assertEqual(len(result["cnc_findings"]), 1)
        self.assertEqual(result["cnc_findings"][0]["path"], "/DRILL.H")
        self.assertEqual(result["cnc_findings"][0]["matched_by"], "content")

    def test_extensionless_fanuc_o_program_signature_is_narrow(self):
        program = b"O4321 (demo)\nN10 G00 X0\nN20 G01 X1 F2\nN30 M30\n"
        result = inventory.cnc_file_findings("/O4321", content=program)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "Possible CNC program")
        self.assertEqual(result[0]["matched_by"], "content")
        self.assertIn("Fanuc", result[0]["reason"])
        self.assertIn("not proof the software works", result[0]["note"])

        for path, content in [
            ("/O4321", b"#define O4321 1\nN10 G00 X0\nN20 G01 X1\nN30 M30\n"),
            ("/O4321", b"O4321\nN10 X0\nN20 X1\nN30 M30\n"),
            ("/O4321", None),
            ("/O4321.TXT", program),
        ]:
            with self.subTest(path=path):
                self.assertEqual(inventory.cnc_file_findings(path, content=content), [])

    def test_extensionless_fanuc_accepts_unumbered_end_after_two_numbered_blocks(self):
        program = b"%\nO1234 (fixture)\nN10 G00 X0 Y0\nN20 G01 X1 F100\nM30\n%\n"
        result = inventory.cnc_file_findings("/O1234", content=program)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["matched_by"], "content")
        self.assertIn("Fanuc", result[0]["reason"])

        for path, content in [
            ("/O1234", b"O1234\nN10 G00 X0\nM30\n"),
            ("/O1234", b"O1234\nN10 G00 X0\nN20 G01 X1\n"),
            ("/O1234.TXT", program),
        ]:
            with self.subTest(path=path, content=content):
                self.assertEqual(inventory.cnc_file_findings(path, content=content), [])

    def test_inspect_reports_extensionless_fanuc_signature_in_fat_image(self):
        program = b"O4321\nN10 G00 X0\nN20 G01 X1 F2\nN30 M30\n"
        data = fixtures.fat12_image({"O4321": program})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fanuc.img")
            with open(path, "wb") as f:
                f.write(data)
            result = inventory.inspect(path)
        self.assertEqual(len(result["cnc_findings"]), 1)
        self.assertEqual(result["cnc_findings"][0]["path"], "/O4321")
        self.assertEqual(result["cnc_findings"][0]["matched_by"], "content")

    def test_filename_rules_have_path_reason_and_hardware_warning(self):
        cases = [("/BIN/DNCPLUS.EXE", "Serial/DNC transfer"),
                 ("/SETUP/FANUC.CFG", "Controller configuration"),
                 ("/TCNC/TURBOCNC.INI", "Controller configuration"),
                 ("/TCNC/MAXNC10.INI", "Controller configuration"),
                 ("/TCNC/XYLOTEX.INI", "Controller configuration"),
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
        for extension in ["NC", "tap", "NgC", "MPF", "GCODE", "ncf", "SSB", "I", "EIA", "Min", "spf", "cnc"]:
            result = inventory.cnc_file_findings("/PARTS/123." + extension)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["matched_by"], "extension")
            if extension.upper() == "GCODE":
                self.assertEqual(result[0]["category"], "Possible G-code program")
                self.assertIn("G-code", result[0]["reason"])
            else:
                self.assertEqual(result[0]["category"], "Possible CNC program")

    def test_public_sample_filenames_remain_detectable(self):
        # Names from the local-only samples recorded in docs/real-sample-validation.md.
        # The Mazak page does not give the sample a filename, so this is a local alias.
        cases = ["/TOOLS/tool-length-probe.ngc", "/PROGRAMS/mazak-quick-turn-100-msy.nc",
                 "/MACROS/TOOLGET.CNC"]
        for path in cases:
            with self.subTest(path=path):
                result = inventory.cnc_file_findings(path)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["category"], "Possible CNC program")
                self.assertEqual(result[0]["matched_by"], "extension")

    def test_directory_names_do_not_match_files(self):
        for path in ["/DNC/HASP/PMAC/FANUC/readme.txt", "DNC\\HASPVDD\\readme.txt",
                     "/DOCS/HASP.TXT", "/SERIAL/readme.exe", "/PARTS/part.nc.bak"]:
            self.assertEqual(inventory.cnc_file_findings(path), [])

    def test_no_content_or_directory_lookup(self):
        # The helper does not open files when no content is explicitly supplied.
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

    def test_real_software_install_names_are_listed(self):
        # Basenames observed in real Estlcam, PlanetCNC CNC USB Controller,
        # DANCAD3D, and MicroSystems World CNC Controller / EZCnc distributions;
        # contents are synthetic placeholders, not real files.
        data = fixtures.fat12_image({"ESTLCAM.EXE": b"synthetic", "ESTLCAM.DAT": b"synthetic",
                                     "CNCUSB.EXE": b"synthetic", "SETTINGS.SET": b"synthetic",
                                     "DANCAD3D.COM": b"synthetic", "DANCAD3D.000": b"synthetic",
                                     "SHOW-20X.MAC": b"synthetic", "MANUAL.DOC": b"synthetic",
                                     "CNC2307L.EXE": b"synthetic", "CNC126.EXE": b"synthetic",
                                     "CNC2.EXE": b"synthetic", "CNC2.INI": b"synthetic",
                                     "EZCNC127.EXE": b"synthetic",
                                     "README.TXT": b"synthetic"})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "shopsw.img")
            with open(path, "wb") as f:
                f.write(data)
            result = inventory.inspect(path)
        self.assertIn("/ESTLCAM.EXE", result["cnc_files"])
        self.assertIn("/CNCUSB.EXE", result["cnc_files"])
        self.assertIn("/DANCAD3D.COM", result["cnc_files"])
        self.assertIn("/CNC2307L.EXE", result["cnc_files"])
        self.assertIn("/CNC126.EXE", result["cnc_files"])
        self.assertIn("/CNC2.EXE", result["cnc_files"])
        self.assertIn("/EZCNC127.EXE", result["cnc_files"])
        self.assertNotIn("/README.TXT", result["cnc_files"])
        self.assertNotIn("/MANUAL.DOC", result["cnc_files"])

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
