import unittest
from scripts.classify_report import classify


class ClassificationTests(unittest.TestCase):
    def run_case(self, scenario, checks, outcome="error"):
        return classify({"scenario": scenario, "checks": [{"check": c, "level": "FAIL"} for c in checks], "outcome": outcome})

    def test_known(self):
        self.assertEqual(self.run_case("truncate_json", ["transport_error_kills_client"]), "XFAIL")

    def test_new_failure_not_hidden(self):
        self.assertEqual(self.run_case("truncate_json", ["transport_error_kills_client", "unexpected_outcome"]), "FAIL")

    def test_harness_failure(self):
        self.assertEqual(self.run_case("truncate_json", ["fault_not_applied"]), "ERROR")

    def test_fixed(self):
        self.assertEqual(self.run_case("truncate_json", []), "XPASS")
