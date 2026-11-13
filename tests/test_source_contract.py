import hashlib
import json
import unittest

from water_quality.client import Client
from water_quality.decoding import parse_page, records as decode_records
from water_quality.origin import load_scenario, serve


class SourceContractTests(unittest.TestCase):
    def test_domain_observations_preserve_source_distinctions(self):
        scenario = load_scenario()
        records = scenario["source_records"]
        with serve(scenario) as origin:
            client = Client(origin.base_url)
            self.assertEqual(records[0]["sample"], records[2]["sample"])
            self.assertNotEqual(records[0]["analyte"], records[2]["analyte"])
            self.assertNotEqual(records[0]["sampled_at"], records[1]["sampled_at"])
            self.assertEqual(records[2]["qualifier"], "<")
            self.assertEqual(records[2]["unit"], "unknown")

    def test_every_record_has_the_declared_source_identity(self):
        scenario = load_scenario()
        for record in scenario["source_records"]:
            for field in scenario["identity_fields"]:
                self.assertIn(field, record)
                self.assertNotEqual(str(record[field]).strip(), "")


if __name__ == "__main__":
    unittest.main()
