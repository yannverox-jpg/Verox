import unittest

from api.analyze_observation import parse_analysis_json


class StructuredAnalysisContractTests(unittest.TestCase):
    def test_accepts_exact_schema(self):
        result = parse_analysis_json(
            '{"title":"Signal public","summary":"Observation vérifiable.","confidence":0.82,"is_actionable":true}'
        )
        self.assertEqual(result["title"], "Signal public")
        self.assertEqual(result["confidence"], 0.82)
        self.assertIs(result["is_actionable"], True)

    def test_rejects_non_json(self):
        with self.assertRaisesRegex(ValueError, "model_output_not_json"):
            parse_analysis_json("Voici le résultat.")

    def test_rejects_extra_fields(self):
        with self.assertRaisesRegex(ValueError, "model_output_schema_invalid"):
            parse_analysis_json(
                '{"title":"T","summary":"S","confidence":0.8,"is_actionable":true,"extra":"no"}'
            )

    def test_rejects_boolean_confidence(self):
        with self.assertRaisesRegex(ValueError, "model_confidence_invalid"):
            parse_analysis_json(
                '{"title":"T","summary":"S","confidence":true,"is_actionable":true}'
            )

    def test_rejects_confidence_outside_range(self):
        with self.assertRaisesRegex(ValueError, "model_confidence_invalid"):
            parse_analysis_json(
                '{"title":"T","summary":"S","confidence":1.1,"is_actionable":true}'
            )

    def test_rejects_non_boolean_actionable(self):
        with self.assertRaisesRegex(ValueError, "model_actionable_invalid"):
            parse_analysis_json(
                '{"title":"T","summary":"S","confidence":0.8,"is_actionable":"true"}'
            )


if __name__ == "__main__":
    unittest.main()
