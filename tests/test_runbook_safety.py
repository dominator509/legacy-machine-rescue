import unittest
from legacyrescue import runbook


def report():
    return {"image": {"path": "synthetic.img", "size": 512, "container": "raw"},
            "volumes": [], "warnings": [], "hardware_flags": [], "cnc_files": [],
            "cnc_findings": [], "unreadable_volumes": []}


class RunbookSafetyTests(unittest.TestCase):
    def test_preflight_is_before_evidence_and_covers_preservation(self):
        text = runbook.build(report())
        self.assertLess(text.index("Before you touch the machine"), text.index("Evidence summary"))
        for word in ["original drive", "sector-level copy", "SHA-256", "controller settings",
                     "BIOS settings", "Photograph", "cable", "card", "safe state"]:
            self.assertIn(word, text)
        self.assertIn("NOT RUN", text)

    def test_not_proven_is_tied_to_actual_findings(self):
        rep = report()
        rep["cnc_findings"] = [{"path": "/CONF/FANUC.CFG", "where": "synthetic volume",
            "category": "Controller configuration", "reason": "filename suggests controller settings",
            "note": "Needs real hardware check; not proof the software works."}]
        rep["hardware_flags"] = [{"category": "Serial/DNC transfer", "evidence": ["CONFIG.SYS: COM1"]}]
        text = runbook.build(rep)
        section = text.split("## Not proven by emulator", 1)[1].split("## 3. Steps", 1)[0]
        self.assertIn("/CONF/FANUC.CFG", section)
        self.assertIn("synthetic volume", section)
        self.assertIn("CONFIG.SYS: COM1", section)
        self.assertIn("NOT PROVEN", section)

    def test_no_match_never_claims_absence_or_hardware_success(self):
        text = runbook.build(report(), {"booted": True, "emulator": "synthetic test emulator",
                                        "evidence": "synthetic test result, not a real boot"})
        self.assertIn("incomplete hints, not an absence check", text)
        self.assertIn("remain NOT PROVEN", text)
        self.assertNotIn("No hardware risks", text)
        self.assertIn("not permission to energize", text)
