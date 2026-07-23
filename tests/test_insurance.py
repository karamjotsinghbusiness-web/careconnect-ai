import unittest

import pandas as pd

from app.insurance import assess_insurance, add_network_verification_status


class InsuranceAssessmentTests(unittest.TestCase):
    def test_normalizes_payer_and_keeps_coverage_unverified(self):
        result = assess_insurance({
            "insurance": "bcbs",
            "insurance_plan_type": "ppo",
            "member_id_present": True,
            "date_of_birth_present": True,
        })

        self.assertEqual(result["payer"], "Anthem / Blue Cross Blue Shield")
        self.assertEqual(result["plan_type"], "PPO")
        self.assertEqual(result["readiness"], "ready_for_gateway")
        self.assertEqual(result["coverage_status"], "not_verified")
        self.assertEqual(result["network_status"], "not_verified")

    def test_reports_missing_verification_inputs(self):
        result = assess_insurance({"payer": "Aetna", "plan_type": "HMO"})

        self.assertIn("Subscriber or member ID", result["missing_for_verification"])
        self.assertIn("Patient date of birth", result["missing_for_verification"])
        self.assertEqual(result["readiness"], "needs_member_details")

    def test_self_pay_has_estimate_steps(self):
        result = assess_insurance({"payer": "Self-pay", "plan_type": "Self-pay"})

        self.assertEqual(result["readiness"], "self_pay")
        self.assertIn("written estimate", " ".join(result["next_steps"]).lower())

    def test_provider_rows_are_never_marked_in_network(self):
        frame = pd.DataFrame([{"provider_name": "Demo Clinic"}])
        result = add_network_verification_status(
            frame,
            assess_insurance({"payer": "Aetna", "plan_type": "PPO"}),
        )

        self.assertEqual(result.iloc[0]["insurance_network_status"], "Not verified")
        self.assertNotIn("in-network", result.iloc[0]["insurance_follow_up"].lower())


if __name__ == "__main__":
    unittest.main()
