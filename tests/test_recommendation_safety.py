import unittest

import pandas as pd

from app.app import confidence_score
from models.recommendation_engine import manual_specialty_match


class RecommendationSafetyTests(unittest.TestCase):
    def test_navigation_confidence_is_deterministic(self):
        providers = pd.DataFrame([{"provider_name": "Demo provider"}])
        empty = pd.DataFrame()

        values = {
            confidence_score(
                providers,
                empty,
                empty,
                empty,
                empty,
                empty,
                "Family Practice",
            )
            for _ in range(20)
        }

        self.assertEqual(values, {85})

    def test_common_primary_care_phrases_are_handled(self):
        self.assertEqual(
            manual_specialty_match("I need a routine primary care checkup"),
            "Family Practice",
        )
        self.assertEqual(
            manual_specialty_match("Heartburn after meals"),
            "Family Practice",
        )

    def test_short_keywords_do_not_match_inside_unrelated_words(self):
        self.assertIsNone(manual_specialty_match("I am researching earnest money"))

    def test_respiratory_phrase_uses_intended_specialty(self):
        self.assertEqual(
            manual_specialty_match("ongoing shortness of breath"),
            "Pulmonology",
        )


if __name__ == "__main__":
    unittest.main()
