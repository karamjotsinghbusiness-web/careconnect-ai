import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.app import app


class LiveLocationApiTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    @staticmethod
    def payload():
        return {
            "age": 42,
            "gender": "Female",
            "city": "Live location",
            "insurance": "Fictional Health Plan",
            "insurance_plan_type": "Unknown",
            "condition": "Ongoing fictional knee pain",
            "priority": "closest",
            "barriers": [],
            "demo_only_confirmed": True,
        }

    def post_recommendation(self, payload):
        empty = pd.DataFrame()
        with (
            patch.dict(
                os.environ,
                {
                    "ALLOW_REAL_PHI": "true",
                    "HOSTING_BAA_CONFIRMED": "true",
                    "GOOGLE_BAA_CONFIRMED": "true",
                },
                clear=False,
            ),
            patch(
                "app.security.auth.verify_id_token",
                return_value={"uid": "location-test-user"},
            ),
            patch(
                "app.app.recommend",
                return_value=(
                    "Primary care",
                    empty,
                    empty,
                    empty,
                    empty,
                    empty,
                    empty,
                ),
            ),
            patch(
                "app.app.discover_supplemental_resources",
                return_value=(empty, empty, empty),
            ),
            patch(
                "app.app.explain_recommendation",
                return_value="Fictional explanation.",
            ),
        ):
            return self.client.post(
                "/recommend",
                json=payload,
                headers={"Authorization": "Bearer valid-location-test-token"},
            )

    def test_explicit_consent_uses_live_location_without_returning_coordinates(self):
        response = self.post_recommendation(
            self.payload()
            | {
                "location_consent": True,
                "latitude": 40.48,
                "longitude": -94.42,
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["location_context"]["used"])
        self.assertEqual(
            body["location_context"]["label"],
            "Near Grant City, Missouri",
        )
        self.assertFalse(body["location_context"]["saved"])
        self.assertNotIn("latitude", body)
        self.assertNotIn("longitude", body)

    def test_coordinates_are_ignored_without_explicit_consent(self):
        response = self.post_recommendation(
            self.payload()
            | {
                "city": "Rolla",
                "location_consent": False,
                "latitude": 40.48,
                "longitude": -94.42,
            }
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["location_context"]["used"])
        self.assertEqual(body["location_context"]["label"], "Rolla")

    def test_invalid_approved_coordinates_are_rejected(self):
        response = self.post_recommendation(
            self.payload()
            | {
                "location_consent": True,
                "latitude": 200,
                "longitude": -94.42,
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid", response.get_json()["message"].lower())


if __name__ == "__main__":
    unittest.main()
