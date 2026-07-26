import unittest

from models.public_provider_data import (
    PROVIDER_COLUMNS,
    is_valid_npi,
    normalize_cms_provider,
    normalize_hrsa_site,
    validate_providers,
)
from models.provider_matcher import add_specialty_relevance


class PublicProviderDataTests(unittest.TestCase):
    def test_npi_check_digit_validation(self):
        self.assertTrue(is_valid_npi("1235888272"))
        self.assertFalse(is_valid_npi("1235888273"))
        self.assertFalse(is_valid_npi("not-an-npi"))

    def test_cms_row_is_normalized_without_availability_claim(self):
        row = {
            "npi": "1235888272",
            "ind_enrl_id": "I-1",
            "org_pac_id": "G-1",
            "adrs_id": "MO63101",
            "provider_first_name": "JANE",
            "provider_middle_name": "Q",
            "provider_last_name": "DOE",
            "gndr": "F",
            "cred": "MD",
            "pri_spec": "FAMILY PRACTICE",
            "sec_spec_1": "INTERNAL MEDICINE",
            "facility_name": "DEMO MEDICAL GROUP",
            "citytown": "ST LOUIS",
            "state": "MO",
            "zip_code": "63101",
            "telephone_number": "3145550100",
        }

        provider = normalize_cms_provider(row, "2026-06-26")

        self.assertEqual(set(provider), set(PROVIDER_COLUMNS))
        self.assertEqual(provider["provider_name"], "Jane Q Doe")
        self.assertEqual(provider["primary_specialty"], "Family Practice")
        self.assertEqual(provider["accepting_new_patients"], "Unknown")
        self.assertIn("2026-06-26", provider["source"])

    def test_cms_rejects_invalid_npi(self):
        provider = normalize_cms_provider(
            {
                "npi": "1235888273",
                "provider_first_name": "JANE",
                "provider_last_name": "DOE",
                "state": "MO",
            },
            "2026-06-26",
        )
        self.assertIsNone(provider)

    def test_hrsa_active_site_includes_coordinates(self):
        row = {
            "Health Center Type": "Federally Qualified Health Center (FQHC)",
            "Health Center Number": "H80CS00001",
            "BPHC Assigned Number": "BPS-H80-1",
            "Site Name": "Example Health Center",
            "Site Address": "100 Main St",
            "Site City": "Rolla",
            "Site State Abbreviation": "MO",
            "Site Postal Code": "65401",
            "Site Telephone Number": "5735550100",
            "Site Status Description": "Active",
            "FQHC Site NPI Number": "1235888272",
            "Health Center Location Identification Number": "5",
            "Health Center Name": "Example Organization",
            "Geocoding Artifact Address Primary X Coordinate": "-91.7713",
            "Geocoding Artifact Address Primary Y Coordinate": "37.9514",
        }

        provider = normalize_hrsa_site(row, "07/24/2026")

        self.assertEqual(provider["latitude"], 37.9514)
        self.assertEqual(provider["longitude"], -91.7713)
        self.assertEqual(provider["accepting_new_patients"], "Unknown")

    def test_hrsa_administrative_only_site_is_excluded(self):
        provider = normalize_hrsa_site(
            {
                "Site Name": "Administrative Office",
                "Site City": "Rolla",
                "Site State Abbreviation": "MO",
                "Site Status Description": "Active",
                "Health Center Type Description": "Administrative",
            },
            "07/24/2026",
        )

        self.assertIsNone(provider)

    def test_quality_report_blocks_unsupported_claims(self):
        provider = {column: "" for column in PROVIDER_COLUMNS}
        provider.update(
            {
                "provider_id": "cms-one",
                "npi": "1235888272",
                "provider_name": "Jane Doe",
                "state": "MO",
                "accepting_new_patients": "Yes",
                "source": "CMS example",
            }
        )

        report = validate_providers(
            [provider],
            minimum_cms=1,
            minimum_hrsa=0,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["unsupported_availability_claims"], 1)

    def test_exact_primary_specialty_ranks_above_fallback(self):
        import pandas as pd

        providers = pd.DataFrame(
            [
                {
                    "provider_name": "Secondary Match",
                    "primary_specialty": "Nephrology",
                    "secondary_specialty": "Internal Medicine",
                    "specialty": "Nephrology",
                },
                {
                    "provider_name": "Exact Match",
                    "primary_specialty": "Family Practice",
                    "secondary_specialty": "",
                    "specialty": "Family Practice",
                },
            ]
        )

        ranked = add_specialty_relevance(
            providers,
            "Family Practice",
            ["family practice", "internal medicine"],
        ).sort_values("specialty_relevance", ascending=False)

        self.assertEqual(ranked.iloc[0]["provider_name"], "Exact Match")
        self.assertGreater(
            ranked.iloc[0]["specialty_relevance"],
            ranked.iloc[1]["specialty_relevance"],
        )


if __name__ == "__main__":
    unittest.main()
