"""Offline checks for the standalone direct-model SSE probe."""

import argparse
import importlib.util
import json
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "direct_model_stream_loadtest.py"
SPEC = importlib.util.spec_from_file_location("direct_model_stream_loadtest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class DirectModelStreamProbeTests(unittest.TestCase):
    def test_actual_request_matches_thinking_config_but_calibration_does_not(self) -> None:
        args = argparse.Namespace(model="DeepSeek-V4-Flash", output_target=55000, enable_thinking=True, thinking_budget=32768)
        actual = probe.request_body(args, 3, 1, 65536)
        self.assertTrue(actual["enable_thinking"])
        self.assertEqual(actual["thinking_budget"], 32768)
        self.assertEqual(actual["max_tokens"], 65536)
        self.assertTrue(actual["stream"])
        calibration = probe.request_body(args, 3, -1, 16)
        self.assertFalse(calibration["enable_thinking"])
        self.assertNotIn("thinking_budget", calibration)

    def test_prompt_repeats_are_deterministic(self) -> None:
        prompt = probe.make_prompt(3, 55000, 2)
        self.assertEqual(prompt.count(probe.CONTEXT_LINE), 3)
        self.assertIn("55000 output tokens", prompt)
        self.assertIn("approximately 3438 lines", prompt)
        self.assertIn("Request 2", prompt)

    def test_smaller_target_does_not_request_eight_thousand_lines(self) -> None:
        prompt = probe.make_prompt(3, 5000, 2)
        self.assertIn("approximately 313 lines", prompt)
        self.assertNotIn("8000 lines", prompt)

    def test_sse_parser_counts_without_retaining_content(self) -> None:
        result = {
            "done_marker": False,
            "invalid_json_events": 0,
            "events": 0,
            "input_tokens": None,
            "output_tokens": None,
            "finish_reason": None,
            "answer_chars": 0,
            "reasoning_chars": 0,
        }
        probe.observe_sse_data(
            json.dumps({"choices": [{"delta": {"content": "hello", "reasoning_content": "why"}, "finish_reason": None}]}),
            result,
        )
        probe.observe_sse_data(
            json.dumps({"choices": [{"delta": {}, "finish_reason": "length"}], "usage": {"prompt_tokens": 15001, "completion_tokens": 55000}}),
            result,
        )
        probe.observe_sse_data("[DONE]", result)
        self.assertEqual(result["events"], 2)
        self.assertEqual(result["answer_chars"], 5)
        self.assertEqual(result["reasoning_chars"], 3)
        self.assertEqual(result["input_tokens"], 15001)
        self.assertEqual(result["output_tokens"], 55000)
        self.assertEqual(result["finish_reason"], "length")
        self.assertTrue(result["done_marker"])
        self.assertNotIn("hello", result.values())


if __name__ == "__main__":
    unittest.main()
