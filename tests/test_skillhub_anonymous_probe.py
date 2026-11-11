import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "deploy/enterprise/inspect-portal-skillhub-anonymous-20261105-r1.py"
spec = importlib.util.spec_from_file_location("skillhub_anonymous_probe", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SkillHubAnonymousProbeTests(unittest.TestCase):
    def test_page_redirect_does_not_establish_api_auth(self):
        self.assertEqual(probe.summarize(307, b"", True)["result"], "REDIRECT_OBSERVED")
        self.assertEqual(probe.summarize(307, b"")["result"], "REVIEW_REQUIRED")

    def test_anonymous_empty_and_nonempty_business_results_are_exposed(self):
        for data in ([], {"items": [{"name": "private-example"}], "total": 1}):
            result = probe.summarize(200, json.dumps({"code": 0, "data": data}).encode())
            self.assertEqual(result["result"], "UNAUTHENTICATED_BUSINESS_RESPONSE")
            self.assertNotIn("private-example", json.dumps(result))

    def test_transport_success_is_not_business_success(self):
        self.assertEqual(probe.summarize(200, b'{"code":401}')["result"], "REVIEW_REQUIRED")
        self.assertEqual(probe.summarize(200, b'<html>login</html>')["result"], "REVIEW_REQUIRED")

    def test_only_auth_denial_counts_as_rejection(self):
        for status in (401, 403):
            self.assertEqual(probe.summarize(status, b"")["result"], "AUTH_REJECTED")
        for status in (404, 500, 503):
            self.assertEqual(probe.summarize(status, b"")["result"], "REVIEW_REQUIRED")

    def test_output_bounds_and_does_not_export_server_body(self):
        raw = b'x' * (probe.MAX_BYTES + 1)
        result = probe.summarize(200, raw)
        self.assertTrue(result["body_bound_exceeded"])
        self.assertLess(len(json.dumps(result)), 300)

    def test_redirects_are_never_followed(self):
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))


if __name__ == "__main__":
    unittest.main()
