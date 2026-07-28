import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.app import app
from app.patient_passport import initialize_patient_passport_store


class PatientPassportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "passport-api.db")
        self.previous = {
            name: os.environ.get(name)
            for name in (
                "PATIENT_PASSPORT_DB_PATH",
                "PASSPORT_ENCRYPTION_KEY",
                "ALLOW_REAL_PHI",
                "HOSTING_BAA_CONFIRMED",
                "GOOGLE_BAA_CONFIRMED",
            )
        }
        os.environ.update({
            "PATIENT_PASSPORT_DB_PATH": self.db_path,
            "PASSPORT_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "ALLOW_REAL_PHI": "true",
            "HOSTING_BAA_CONFIRMED": "true",
            "GOOGLE_BAA_CONFIRMED": "true",
        })
        initialize_patient_passport_store()
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.patient_claim = {
            "uid": "patient-passport-001",
            "email": "patient@example.invalid",
            "email_verified": True,
        }
        self.clinician_claim = {
            "uid": "nurse-passport-001",
            "email": "nurse@example.invalid",
            "email_verified": True,
            "name": "Nurse Avery",
            "clinical_role": "nurse",
            "organization_id": "nmc-grant-city",
            "organization_name": "Northwest Medical Center",
        }

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp_dir.cleanup()

    @staticmethod
    def headers(token):
        return {"Authorization": f"Bearer {token}"}

    def _patient_profile(self):
        return {
            "preferred_name": "Jordan Lee",
            "allergies": ["Penicillin (rash)", "Latex (itching)"],
            "medications": ["Lisinopril 10 mg daily"],
            "conditions": ["Hypertension"],
            "care_team": ["Grant City Clinic"],
            "emergency_notes": "Patient reports no emergency notes.",
        }

    def _create_patient_and_code(self):
        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.patient_claim,
        ):
            saved = self.client.post(
                "/passport/profile",
                json=self._patient_profile(),
                headers=self.headers("patient-token"),
            )
            self.assertEqual(saved.status_code, 200)
            created = self.client.post(
                "/passport/access-codes",
                json={"grant_duration_hours": 4},
                headers=self.headers("patient-token"),
            )
            self.assertEqual(created.status_code, 201)
            return created.get_json()["access_code"]

    def _redeem(self, code, claim=None, token="clinician-token"):
        with patch(
            "app.security.auth.verify_id_token",
            return_value=claim or self.clinician_claim,
        ):
            return self.client.post(
                "/passport/clinician/redeem",
                json={"code": code},
                headers=self.headers(token),
            )

    def test_complete_consent_grant_append_audit_and_revoke_workflow(self):
        access_code = self._create_patient_and_code()
        self.assertRegex(access_code["code"], r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertTrue(access_code["qr_data_uri"].startswith("data:image/svg+xml"))

        redeemed = self._redeem(access_code["code"])
        self.assertEqual(redeemed.status_code, 201)
        grant_id = redeemed.get_json()["grant"]["grant_id"]

        reused = self._redeem(access_code["code"])
        self.assertEqual(reused.status_code, 403)

        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.clinician_claim,
        ):
            opened = self.client.get(
                f"/passport/clinician/{grant_id}",
                headers=self.headers("clinician-token"),
            )
            self.assertEqual(opened.status_code, 200)
            passport = opened.get_json()["passport"]
            self.assertEqual(passport["profile"]["preferred_name"], "Jordan Lee")
            self.assertTrue(passport["append_only_history"])

            entry = self.client.post(
                f"/passport/clinician/{grant_id}/entries",
                json={
                    "entry_type": "clinician_encounter",
                    "encounter_datetime": datetime.now(timezone.utc).isoformat(),
                    "summary": "Office visit",
                    "clinical_note": "Patient-reported symptoms reviewed during visit.",
                    "source_of_information": "Patient interview",
                    "append_only_confirmed": True,
                },
                headers=self.headers("clinician-token"),
            )
            self.assertEqual(entry.status_code, 201)
            self.assertEqual(
                entry.get_json()["entry"]["source_type"], "clinician_confirmed"
            )

        other_clinician = dict(
            self.clinician_claim,
            uid="nurse-passport-other",
            email="other-nurse@example.invalid",
        )
        with patch(
            "app.security.auth.verify_id_token",
            return_value=other_clinician,
        ):
            denied = self.client.get(
                f"/passport/clinician/{grant_id}",
                headers=self.headers("other-clinician-token"),
            )
        self.assertEqual(denied.status_code, 404)

        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.patient_claim,
        ):
            patient_view = self.client.get(
                "/passport",
                headers=self.headers("patient-token"),
            )
            self.assertEqual(patient_view.status_code, 200)
            patient_passport = patient_view.get_json()["passport"]
            self.assertTrue(
                any(
                    item["summary"] == "Office visit"
                    for item in patient_passport["entries"]
                )
            )
            event_types = {item["event_type"] for item in patient_passport["audit"]}
            self.assertIn("passport_viewed", event_types)
            self.assertIn("entry_added", event_types)
            granted_event = next(
                item
                for item in patient_passport["audit"]
                if item["event_type"] == "access_granted"
            )
            self.assertEqual(granted_event["actor_display"], "Nurse Avery")

            revoked = self.client.post(
                f"/passport/grants/{grant_id}/revoke",
                headers=self.headers("patient-token"),
            )
            self.assertEqual(revoked.status_code, 200)

        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.clinician_claim,
        ):
            closed = self.client.get(
                f"/passport/clinician/{grant_id}",
                headers=self.headers("clinician-token"),
            )
        self.assertEqual(closed.status_code, 403)

    def test_phi_and_access_code_are_not_stored_in_plaintext_and_history_is_immutable(self):
        access_code = self._create_patient_and_code()
        redeemed = self._redeem(access_code["code"])
        grant_id = redeemed.get_json()["grant"]["grant_id"]

        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.clinician_claim,
        ):
            entry = self.client.post(
                f"/passport/clinician/{grant_id}/entries",
                json={
                    "entry_type": "allergy_update",
                    "encounter_datetime": datetime.now(timezone.utc).isoformat(),
                    "summary": "Confirmed penicillin allergy",
                    "clinical_note": "Penicillin allergy confirmed with patient.",
                    "source_of_information": "Patient interview",
                    "append_only_confirmed": True,
                },
                headers=self.headers("clinician-token"),
            )
        entry_id = entry.get_json()["entry"]["entry_id"]

        with sqlite3.connect(self.db_path) as connection:
            profile_blob = connection.execute(
                "SELECT encrypted_profile FROM passport_profiles"
            ).fetchone()[0]
            entry_blob = connection.execute(
                "SELECT encrypted_payload FROM passport_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()[0]
            code_row = connection.execute(
                "SELECT code_digest FROM passport_access_codes"
            ).fetchone()[0]
            self.assertNotIn("Penicillin", profile_blob)
            self.assertNotIn("Penicillin", entry_blob)
            self.assertNotEqual(code_row, access_code["code"])
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE passport_entries SET entry_type = 'correction' WHERE entry_id = ?",
                    (entry_id,),
                )

    def test_verified_role_and_organization_claims_are_both_required(self):
        access_code = self._create_patient_and_code()
        missing_organization = dict(self.clinician_claim)
        missing_organization.pop("organization_id")
        missing_organization.pop("organization_name")
        response = self._redeem(access_code["code"], claim=missing_organization)
        self.assertEqual(response.status_code, 403)
        self.assertIn("organization", response.get_json()["message"].lower())

        ordinary_user = {
            "uid": "ordinary-user",
            "email_verified": True,
        }
        response = self._redeem(access_code["code"], claim=ordinary_user)
        self.assertEqual(response.status_code, 403)

    def test_grant_cannot_follow_clinician_to_a_different_organization(self):
        access_code = self._create_patient_and_code()
        redeemed = self._redeem(access_code["code"])
        grant_id = redeemed.get_json()["grant"]["grant_id"]
        moved_clinician = dict(
            self.clinician_claim,
            organization_id="different-hospital",
            organization_name="Different Hospital",
        )
        with patch(
            "app.security.auth.verify_id_token",
            return_value=moved_clinician,
        ):
            response = self.client.get(
                f"/passport/clinician/{grant_id}",
                headers=self.headers("moved-clinician-token"),
            )
        self.assertEqual(response.status_code, 403)
        self.assertIn("different", response.get_json()["message"].lower())

    def test_integrity_chain_detects_database_tampering(self):
        access_code = self._create_patient_and_code()
        redeemed = self._redeem(access_code["code"])
        grant_id = redeemed.get_json()["grant"]["grant_id"]

        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DROP TRIGGER passport_entries_no_update")
            connection.execute(
                "UPDATE passport_entries SET entry_type = 'tampered_entry'"
            )

        with patch(
            "app.security.auth.verify_id_token",
            return_value=self.clinician_claim,
        ):
            response = self.client.get(
                f"/passport/clinician/{grant_id}",
                headers=self.headers("clinician-token"),
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("integrity", response.get_json()["message"].lower())

    def test_invalid_code_attempts_are_rate_limited(self):
        for index in range(5):
            denied = self._redeem(
                f"AAAA-AA{index + 2}A",
                token=f"clinician-token-{index}",
            )
            self.assertEqual(denied.status_code, 403)
        limited = self._redeem("BBBB-BBBB", token="clinician-token-limited")
        self.assertEqual(limited.status_code, 429)

    def test_unverified_patient_cannot_open_passport(self):
        with patch(
            "app.security.auth.verify_id_token",
            return_value={"uid": "unverified", "email_verified": False},
        ):
            response = self.client.get(
                "/passport",
                headers=self.headers("unverified-token"),
            )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
