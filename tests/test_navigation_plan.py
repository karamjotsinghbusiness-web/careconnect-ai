import unittest

import pandas as pd

from app.insurance import assess_insurance
from app.navigation_plan import build_navigation_plan


class NavigationPlanTests(unittest.TestCase):
    def build_plan(self, **patient_overrides):
        patient = {
            "city": "Rolla",
            "condition": "ongoing knee pain",
            "priority": "closest",
            "barriers": ["transportation", "cost"],
        }
        patient.update(patient_overrides)
        providers = pd.DataFrame([
            {
                "provider_name": "Farther Demo Clinic",
                "city": "Rolla",
                "distance_miles": 8.2,
                "phone": "555-0102",
            },
            {
                "provider_name": "Closer Demo Clinic",
                "city": "Rolla",
                "distance_miles": 1.4,
                "phone": "555-0101",
            },
        ])
        insurance = assess_insurance({
            "payer": "Aetna",
            "plan_type": "PPO",
        })
        return build_navigation_plan(
            patient=patient,
            specialty="Physical Therapist In Private Practice",
            emergency={"is_emergency": False},
            access_score={"level": "Limited Access"},
            care_gap={"detected": True},
            insurance_assessment=insurance,
            providers=providers,
            nearest_clinics=pd.DataFrame(),
            fallback_hospitals=pd.DataFrame(),
            recommended_hospitals=pd.DataFrame(),
            advocates=pd.DataFrame(),
        )

    def test_closest_priority_sorts_known_distances(self):
        plan = self.build_plan()

        self.assertEqual(plan["priority"]["id"], "closest")
        self.assertEqual(plan["care_options"][0]["name"], "Closer Demo Clinic")
        self.assertEqual(plan["care_options"][0]["distance_miles"], 1.4)

    def test_plan_is_actionable_without_claiming_verification(self):
        plan = self.build_plan()

        self.assertEqual(len(plan["tasks"]), 4)
        self.assertFalse(plan["safety"]["coverage_verified"])
        self.assertFalse(plan["safety"]["availability_verified"])
        self.assertTrue(all(
            option["network_status"] == "Not verified"
            for option in plan["care_options"]
        ))

    def test_barriers_create_specific_actions(self):
        plan = self.build_plan()
        barrier_names = {item["barrier"] for item in plan["barrier_plan"]}
        barrier_text = " ".join(item["action"] for item in plan["barrier_plan"]).lower()

        self.assertEqual(barrier_names, {"Transportation", "Cost"})
        self.assertIn("telehealth", barrier_text)
        self.assertIn("written estimate", barrier_text)

    def test_call_kits_include_provider_and_plan_questions(self):
        plan = self.build_plan()

        self.assertIn("ongoing knee pain", plan["call_kits"]["provider"]["script"])
        self.assertIn("in network", plan["call_kits"]["insurance"]["script"])
        self.assertIn(
            "Call reference number",
            plan["call_kits"]["insurance"]["record_fields"],
        )

    def test_unknown_preferences_are_safely_normalized(self):
        plan = self.build_plan(priority="invented", barriers=["cost", "invented"])

        self.assertEqual(plan["priority"]["id"], "fastest")
        self.assertEqual(plan["barriers"], ["Cost"])

    def test_cost_priority_puts_clinic_options_first(self):
        insurance = assess_insurance({"payer": "Aetna", "plan_type": "PPO"})
        plan = build_navigation_plan(
            patient={"city": "Rolla", "condition": "demo", "priority": "cost"},
            specialty="Family Practice",
            emergency={"is_emergency": False},
            access_score={"level": "Moderate Access"},
            care_gap={"detected": False},
            insurance_assessment=insurance,
            providers=pd.DataFrame([{"provider_name": "Demo Specialist"}]),
            nearest_clinics=pd.DataFrame([{"clinic_name": "Demo Community Clinic"}]),
        )

        self.assertEqual(plan["care_options"][0]["name"], "Demo Community Clinic")
        self.assertIn("sliding-fee", plan["care_options"][0]["type"])


if __name__ == "__main__":
    unittest.main()
