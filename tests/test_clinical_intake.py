import unittest

from app.clinical_intake import structure_clinical_intake
from app.insurance import assess_insurance


class ClinicalIntakeTests(unittest.TestCase):
    def build_result(self, **overrides):
        payload = {
            "patient_reference": "DEMO-10042",
            "encounter_datetime": "2026-07-22T10:15",
            "visit_type": "Office visit",
            "reason_for_visit": "Cough and sore throat",
            "clinical_note": (
                "HPI: Fictional patient reports cough for three days.\n"
                "Allergies: Penicillin\n"
                "Medications: Acetaminophen as needed\n"
                "Assessment: Clinician-entered demo assessment\n"
                "Plan: Clinician-entered demo plan\n"
                "Vitals: BP 124/78 HR 78 RR 18 SpO2 97% Temp 98.9 F"
            ),
            "assessment": "Explicit clinician assessment",
            "plan": "Explicit clinician plan",
        }
        payload.update(overrides)
        insurance = assess_insurance({"payer": "Aetna", "plan_type": "PPO"})
        return structure_clinical_intake(payload, insurance, allow_openai=False)

    def test_structures_labeled_note_and_vitals(self):
        result = self.build_result()

        self.assertEqual(result["draft_status"], "clinician_review_required")
        self.assertFalse(result["saved"])
        self.assertFalse(result["ehr_write_attempted"])
        self.assertEqual(
            result["sections"]["objective"]["vitals"]["blood_pressure"],
            "124/78",
        )
        self.assertEqual(
            result["sections"]["medication_reconciliation"]["allergies"],
            ["Penicillin"],
        )

    def test_explicit_clinician_fields_win_and_require_review(self):
        result = self.build_result()

        self.assertEqual(
            result["sections"]["clinical_review"]["assessment"],
            "Explicit clinician assessment",
        )
        assessment_mapping = next(
            item for item in result["destination_mappings"]
            if item["field"] == "Assessment"
        )
        self.assertEqual(assessment_mapping["resource"], "Condition")
        self.assertEqual(assessment_mapping["status"], "clinician_review_required")

    def test_missing_medication_reconciliation_is_flagged(self):
        result = self.build_result(
            clinical_note="HPI: Fictional patient reports cough.",
            medications="",
            allergies="",
        )
        review = " ".join(result["review_items"]).lower()

        self.assertIn("allergy status", review)
        self.assertIn("medication reconciliation", review)

    def test_insurance_maps_to_coverage_but_requires_verification(self):
        result = self.build_result()
        coverage_mapping = next(
            item for item in result["destination_mappings"]
            if item["resource"] == "Coverage"
        )

        self.assertEqual(coverage_mapping["status"], "verification_required")
        self.assertIn("not verified", " ".join(result["review_items"]).lower())


if __name__ == "__main__":
    unittest.main()
