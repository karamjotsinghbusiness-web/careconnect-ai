import unittest

import pandas as pd

from models.hospital_matcher import get_hospital_city_coordinates
from models.hospice import get_hospice_coordinates
from models.location_resolver import (
    missouri_counties,
    missouri_places,
    resolve_missouri_location,
)
from models.long_term import get_hospital_coordinates
from models.provider_matcher import (
    filter_by_radius,
    find_nearest_clinics,
    get_city_coordinates,
)
from models.recommendation_engine import find_advocates


class MissouriLocationRankingTests(unittest.TestCase):
    def test_census_gazetteers_cover_missouri_places_and_counties(self):
        self.assertGreaterEqual(len(missouri_places()), 1000)
        self.assertEqual(
            len({item.geoid for item in missouri_counties().values()}),
            115,
        )

    def test_worth_county_uses_census_internal_point(self):
        resolved = resolve_missouri_location("Worth County, Missouri")

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, "Worth County")
        self.assertEqual(resolved.geoid, "29227")
        self.assertAlmostEqual(resolved.latitude, 40.480499, places=6)
        self.assertAlmostEqual(resolved.longitude, -94.419198, places=6)
        self.assertEqual(
            get_city_coordinates("Worth County"),
            resolved.coordinates,
        )
        self.assertEqual(
            get_hospital_city_coordinates("Worth County"),
            resolved.coordinates,
        )
        self.assertEqual(
            get_hospice_coordinates("Worth County"),
            resolved.coordinates,
        )
        self.assertEqual(
            get_hospital_coordinates("Worth County"),
            resolved.coordinates,
        )

    def test_worth_county_clinics_are_ranked_locally(self):
        clinics = find_nearest_clinics("Worth County", top_n=5)

        self.assertFalse(clinics.empty)
        self.assertEqual(
            clinics.iloc[0]["clinic_name"],
            "NMC Grant City Clinic",
        )
        self.assertEqual(clinics.iloc[0]["city"], "Grant City")
        self.assertLess(float(clinics.iloc[0]["distance_miles"]), 2)
        self.assertNotIn(
            "OMC/Zizzer Clinic",
            set(clinics["clinic_name"]),
        )
        self.assertTrue(
            (pd.to_numeric(clinics["distance_miles"]) <= 30).all()
        )

    def test_unknown_county_never_returns_arbitrary_unknown_distance_rows(self):
        unresolved = find_nearest_clinics(
            "Imaginary County",
            top_n=5,
        )
        self.assertTrue(unresolved.empty)

        unknown_rows = pd.DataFrame(
            [{"provider_name": "Far Away", "distance_miles": "Unknown"}]
        )
        self.assertTrue(filter_by_radius(unknown_rows).empty)

    def test_radius_filter_never_substitutes_a_far_away_provider(self):
        far_rows = pd.DataFrame([
            {"provider_name": "Five Hours Away", "distance_miles": 250.0},
        ])

        self.assertTrue(filter_by_radius(far_rows, radius_miles=30).empty)

    def test_advocate_fallback_is_distance_ranked_instead_of_file_order(self):
        advocates = find_advocates("Worth County", top_n=5)

        if not advocates.empty:
            self.assertNotIn("Unknown", set(advocates["distance_miles"]))
            self.assertTrue(
                (pd.to_numeric(advocates["distance_miles"]) <= 80).all()
            )


if __name__ == "__main__":
    unittest.main()
