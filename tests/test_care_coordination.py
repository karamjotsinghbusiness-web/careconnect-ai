import os
import tempfile
import unittest
from pathlib import Path

from app.care_coordination import (
    CareCoordinationError,
    create_care_request,
    initialize_care_coordination_store,
    list_patient_requests,
    revoke_patient_request,
    update_request_status,
)


class CareCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("CARE_COORDINATION_DB_PATH")
        os.environ["CARE_COORDINATION_DB_PATH"] = str(
            Path(self.temp_dir.name) / "coordination.db"
        )
        initialize_care_coordination_store()

    def tearDown(self):
        if self.previous_db_path is None:
            os.environ.pop("CARE_COORDINATION_DB_PATH", None)
        else:
            os.environ["CARE_COORDINATION_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def appointment_payload(**overrides):
        payload = {
            "client_request_id": "appointment-request-001",
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
            "provider_name": "Fictional Mercy Clinic",
            "provider_phone": "573-555-0100",
            "provider_city": "Rolla",
            "service_needed": "Fictional physical therapy evaluation",
            "appointment_timing": "first_available",
            "referral_status": "not_sure",
            "notes": "Fictional demonstration request.",
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def authorization_payload(**overrides):
        payload = {
            "client_request_id": "authorization-request-001",
            "request_type": "prior_authorization",
            "demo_only_confirmed": True,
            "patient_authorization_confirmed": True,
            "data_review_confirmed": True,
            "contact_preference": "in_app",
            "consent_permissions": [
                "contact_insurer",
                "contact_ordering_provider",
                "share_authorization_packet",
            ],
            "payer": "Fictional Health Plan",
            "plan_type": "PPO",
            "service_or_item": "Fictional MRI",
            "known_requirement": "unknown",
            "ordering_provider": "",
            "servicing_provider": "Fictional Imaging Center",
            "member_id_available": True,
            "date_of_birth_available": True,
            "provider_order_available": False,
            "clinical_notes_available": False,
        }
        payload.update(overrides)
        return payload

    def test_rejects_real_patient_request_until_phi_gate_is_enabled(self):
        payload = self.appointment_payload(demo_only_confirmed=False)
        with self.assertRaisesRegex(CareCoordinationError, "Real patient information"):
            create_care_request("patient-1", payload, allow_real_phi=False)

    def test_requires_explicit_patient_authorization_and_review(self):
        payload = self.appointment_payload(patient_authorization_confirmed=False)
        with self.assertRaisesRegex(CareCoordinationError, "Patient authorization"):
            create_care_request("patient-1", payload, allow_real_phi=False)

        payload = self.appointment_payload(data_review_confirmed=False)
        with self.assertRaisesRegex(CareCoordinationError, "displayed information"):
            create_care_request("patient-1", payload, allow_real_phi=False)

    def test_appointment_request_is_idempotent_and_not_marked_sent(self):
        first, created = create_care_request(
            "patient-1", self.appointment_payload(), allow_real_phi=False
        )
        duplicate, duplicate_created = create_care_request(
            "patient-1", self.appointment_payload(), allow_real_phi=False
        )

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first["request_id"], duplicate["request_id"])
        self.assertEqual(first["status"], "received")
        self.assertEqual(first["external_delivery_status"], "not_sent")
        self.assertIn("not sent", first["assessment"]["readiness_label"].lower())
        self.assertEqual(len(list_patient_requests("patient-1")), 1)

    def test_prior_authorization_assistant_reports_missing_items_without_claiming_verification(self):
        care_request, created = create_care_request(
            "patient-2", self.authorization_payload(), allow_real_phi=False
        )

        self.assertTrue(created)
        self.assertEqual(care_request["status"], "assessment_ready")
        assessment = care_request["assessment"]
        self.assertEqual(assessment["verification_status"], "not_verified")
        self.assertEqual(assessment["readiness"], "needs_information")
        self.assertGreaterEqual(len(assessment["missing_items"]), 3)
        self.assertIn("has not contacted", assessment["disclaimer"])

    def test_patient_only_lists_and_revokes_their_own_request(self):
        care_request, _ = create_care_request(
            "patient-owner", self.appointment_payload(), allow_real_phi=False
        )
        self.assertEqual(list_patient_requests("different-patient"), [])

        with self.assertRaisesRegex(CareCoordinationError, "not found"):
            revoke_patient_request("different-patient", care_request["request_id"])

        revoked, changed = revoke_patient_request(
            "patient-owner", care_request["request_id"]
        )
        self.assertTrue(changed)
        self.assertEqual(revoked["status"], "revoked")
        self.assertIsNotNone(revoked["consent_revoked_at"])

        repeated, changed_again = revoke_patient_request(
            "patient-owner", care_request["request_id"]
        )
        self.assertFalse(changed_again)
        self.assertEqual(repeated["status"], "revoked")

    def test_only_allowed_workflow_transitions_can_create_verified_delivery_status(self):
        care_request, _ = create_care_request(
            "patient-3",
            self.appointment_payload(client_request_id="appointment-request-003"),
            allow_real_phi=False,
        )

        with self.assertRaisesRegex(CareCoordinationError, "cannot move"):
            update_request_status(
                "clinician-1", care_request["request_id"], "scheduled"
            )

        reviewed = update_request_status(
            "clinician-1", care_request["request_id"], "ready_for_review"
        )
        self.assertEqual(reviewed["external_delivery_status"], "not_sent")

        sent = update_request_status(
            "clinician-1", care_request["request_id"], "sent_to_destination"
        )
        self.assertEqual(sent["external_delivery_status"], "verified_sent")
        self.assertIn("verified delivery", sent["last_verified_event"].lower())


if __name__ == "__main__":
    unittest.main()
