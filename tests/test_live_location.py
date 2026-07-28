import unittest

import pandas as pd

from models.hospital_matcher import add_hospital_distance
from models.location_resolver import nearest_missouri_place
from models.long_term import add_hospital_distance as add_long_term_distance


class LiveLocationTests(unittest.TestCase):
    def setUp(self):
        self.facilities = pd.DataFrame([
            {"facility_name": "Kansas City option", "city_town": "Kansas City"},
            {"facility_name": "West Plains option", "city_town": "West Plains"},
        ])

    def test_worth_county_coordinates_resolve_to_grant_city(self):
        location = nearest_missouri_place(40.48, -94.42)

        self.assertIsNotNone(location)
        self.assertEqual(location.name, "Grant City town")

    def test_coordinates_outside_service_area_are_rejected(self):
        self.assertIsNone(nearest_missouri_place(34.0522, -118.2437))

    def test_live_coordinates_override_an_incorrect_city_for_hospitals(self):
        ranked = add_hospital_distance(
            self.facilities,
            patient_city="West Plains",
            patient_latitude=39.10,
            patient_longitude=-94.58,
        )

        self.assertEqual(ranked.iloc[0]["facility_name"], "Kansas City option")
        self.assertLess(
            float(ranked.iloc[0]["distance_miles"]),
            float(ranked.iloc[1]["distance_miles"]),
        )

    def test_live_coordinates_override_an_incorrect_city_for_long_term_care(self):
        ranked = add_long_term_distance(
            self.facilities,
            patient_city="West Plains",
            patient_latitude=39.10,
            patient_longitude=-94.58,
        )

        self.assertEqual(ranked.iloc[0]["facility_name"], "Kansas City option")
        self.assertLess(
            float(ranked.iloc[0]["distance_miles"]),
            float(ranked.iloc[1]["distance_miles"]),
        )


if __name__ == "__main__":
    unittest.main()
