import unittest

import pandas as pd

from models.hospice import normalize_columns
from scripts.sync_public_facility_data import validate_dataset


class PublicFacilityDataTests(unittest.TestCase):
    def config(self, minimum_rows=1, minimum_facilities=1):
        return {
            "facility_id": "facility_id",
            "measure_id": "measure_id",
            "required": {
                "facility_id",
                "facility_name",
                "state",
                "measure_id",
                "score",
            },
            "minimum_rows": minimum_rows,
            "minimum_facilities": minimum_facilities,
        }

    def test_valid_missouri_quality_row_passes(self):
        report = validate_dataset(
            [
                {
                    "facility_id": "260001",
                    "facility_name": "Example Hospital",
                    "state": "MO",
                    "measure_id": "READM_30_HF",
                    "score": "12.3",
                }
            ],
            self.config(),
        )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["facilities"], 1)
        self.assertEqual(report["measures"], 1)

    def test_duplicate_facility_measure_is_rejected(self):
        row = {
            "facility_id": "260001",
            "facility_name": "Example Hospital",
            "state": "MO",
            "measure_id": "READM_30_HF",
            "score": "12.3",
        }
        report = validate_dataset([row, row], self.config())

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["duplicate_facility_measure_rows"], 1)

    def test_wrong_state_and_missing_schema_are_rejected(self):
        report = validate_dataset(
            [
                {
                    "facility_id": "010001",
                    "facility_name": "Out of State",
                    "state": "AL",
                    "measure_id": "READM_30_HF",
                }
            ],
            self.config(),
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["wrong_state_rows"], 1)
        self.assertIn("score", report["errors"][0])

    def test_hospice_api_columns_match_recommendation_contract(self):
        normalized = normalize_columns(
            pd.DataFrame(
                [
                    {
                        "cms_certification_number_ccn": "261500",
                        "citytown": "SPRINGFIELD",
                        "countyparish": "Greene",
                        "measure_code": "H_011_01_OBSERVED",
                    }
                ]
            )
        )

        self.assertIn("facility_id", normalized.columns)
        self.assertIn("city_town", normalized.columns)
        self.assertIn("county_parish", normalized.columns)
        self.assertIn("measure_id", normalized.columns)


if __name__ == "__main__":
    unittest.main()
