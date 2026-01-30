import unittest
from unittest.mock import MagicMock, patch
import json
import google.api_core.exceptions
from app.generators.ai_service import GeminiService
from app.generators.ads.ads_generator import AdsGenerator

class TestGeminiFixes(unittest.TestCase):

    @patch('google.generativeai.GenerativeModel')
    def test_retry_logic(self, mock_model_cls):
        # Setup mock
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model

        # Scenario: Fail 2 times with ResourceExhausted, then succeed
        error = google.api_core.exceptions.ResourceExhausted("Quota exceeded")
        success_response = MagicMock()
        success_response.text = "Success"

        mock_model.generate_content.side_effect = [error, error, success_response]

        service = GeminiService(api_key="fake")
        # Replace the model instance with our mock (constructor creates a new one)
        service.model = mock_model

        # Override sleep to speed up test
        with patch('time.sleep', return_value=None) as mock_sleep:
            result = service.complete("test prompt")

            self.assertEqual(result, "Success")
            self.assertEqual(mock_model.generate_content.call_count, 3)
            self.assertEqual(mock_sleep.call_count, 2)
            print("\n[PASS] Retry logic verified: Retried 2 times on ResourceExhausted.")

    @patch('google.generativeai.GenerativeModel')
    def test_json_cleaning(self, mock_model_cls):
        mock_model = MagicMock()
        mock_model_cls.return_value = mock_model

        # Scenario: Return markdown wrapped JSON
        dirty_json = "```json\n{\"key\": \"value\"}\n```"
        response = MagicMock()
        response.text = dirty_json
        mock_model.generate_content.return_value = response

        service = GeminiService(api_key="fake")
        service.model = mock_model

        cleaned = service.complete_json("test")
        self.assertEqual(cleaned, '{"key": "value"}')
        print("\n[PASS] JSON cleaning verified: Stripped markdown.")

    @patch('app.generators.ai_service.GeminiService')
    def test_ads_generator_fallback(self, mock_service_cls):
        mock_service = MagicMock()
        # Scenario: Service raises generic exception (e.g. 500 error or max retries exceeded)
        mock_service.complete_json.side_effect = Exception("API Failure")

        generator = AdsGenerator(mock_service)

        result = generator.generate_responsive_search_ad("test keyword")

        self.assertIn("error", result)
        self.assertEqual(result["error"], "API Failure")
        self.assertIn("headlines", result) # Fallback data
        print("\n[PASS] Generator fallback verified: Returned safe structure on crash.")

if __name__ == '__main__':
    unittest.main()
