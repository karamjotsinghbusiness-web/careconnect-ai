import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.app import app


class CoordinationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("CARE_COORDINATION_DB_PATH")
        os.environ["CARE_COORDINATION_DB_PATH"] = str(
            Path(self.temp_dir.name) / "coordination-api.db"
        )
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("CARE_COORDINATION_DB_PATH", None)
        else:
            os.environ["CARE_COORDINATION_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def payload():
        return {
            "client_request_id": "api-appointment-request-001",
            "request_type": "appointment_referral",
            "demo_only_confirmed": True,
            "patient_authorization_confirmed": True,
            "data_review_confirmed": True,
            "contact_preference": "in_app",
            "consent_permissions": [
                "contact_selected_providers",
                "request_appointment",
                "share_referral_summary",
            ],
            "destination_mode": "specific_provider",
            "provider_name": "Fictional Clinic",
            "provider_city": "Rolla",
            "service_needed": "Fictional appointment",
            "appointment_timing": "first_available",
            "referral_status": "not_sure",
        }

    def test_patient_api_creates_lists_and_revokes_without_duplicate(self):
        with patch(
            "app.security.auth.verify_id_token",
            return_value={"uid": "patient-api", "email_verified": True},
        ):
            response = self.client.post(
                "/coordination/requests",
                json=self.payload(),
                headers={"Authorization": "Bearer valid-patient-token"},
            )
            self.assertEqual(response.status_code, 201)
            created = response.get_json()
            self.assertTrue(created["created"])
            request_id = created["request"]["request_id"]
            self.assertEqual(
                created["request"]["external_delivery_status"], "not_sent"
            )

            duplicate = self.client.post(
                "/coordination/requests",
                json=self.payload(),
                headers={"Authorization": "Bearer valid-patient-token"},
            )
            self.assertEqual(duplicate.status_code, 200)
            self.assertFalse(duplicate.get_json()["created"])

            listed = self.client.get(
                "/coordination/requests",
                headers={"Authorization": "Bearer valid-patient-token"},
            )
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.get_json()["requests"]), 1)

            revoked = self.client.post(
                f"/coordination/requests/{request_id}/revoke",
                headers={"Authorization": "Bearer valid-patient-token"},
            )
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.get_json()["request"]["status"], "revoked")

    def test_api_requires_authentication(self):
        response = self.client.get("/coordination/requests")
        self.assertEqual(response.status_code, 401)

    def test_verified_clinician_claim_is_required_for_status_changes(self):
        with patch(
            "app.security.auth.verify_id_token",
            return_value={"uid": "patient-api", "email_verified": True},
        ):
            created = self.client.post(
                "/coordination/requests",
                json=self.payload() | {
                    "client_request_id": "api-appointment-request-002"
                },
                headers={"Authorization": "Bearer valid-patient-token"},
            ).get_json()
        request_id = created["request"]["request_id"]

        with patch(
            "app.security.auth.verify_id_token",
            return_value={"uid": "unverified-user"},
        ):
            denied = self.client.post(
                f"/coordination/requests/{request_id}/status",
                json={"status": "ready_for_review"},
                headers={"Authorization": "Bearer ordinary-token"},
            )
            self.assertEqual(denied.status_code, 403)

        with patch(
            "app.security.auth.verify_id_token",
            return_value={"uid": "nurse-api", "clinical_role": "nurse"},
        ):
            reviewed = self.client.post(
                f"/coordination/requests/{request_id}/status",
                json={"status": "ready_for_review"},
                headers={"Authorization": "Bearer clinician-token"},
            )
            self.assertEqual(reviewed.status_code, 200)
            self.assertEqual(
                reviewed.get_json()["request"]["external_delivery_status"],
                "not_sent",
            )

            sent = self.client.post(
                f"/coordination/requests/{request_id}/status",
                json={"status": "sent_to_destination"},
                headers={"Authorization": "Bearer clinician-token"},
            )
            self.assertEqual(sent.status_code, 200)
            self.assertEqual(
                sent.get_json()["request"]["external_delivery_status"],
                "verified_sent",
            )


if __name__ == "__main__":
    unittest.main()
