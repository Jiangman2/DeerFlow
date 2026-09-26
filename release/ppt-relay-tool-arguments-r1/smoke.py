"""Run in the exact Gateway base, offline, without pytest or application startup."""

import asyncio
import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch


def load_adapter(path):
    spec = importlib.util.spec_from_file_location("ppt_relay_smoke_target", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = None


def payload():
    return {
        "model": "DeepSeek-V4-Flash",
        "stream": True,
        "input": "Compute 1+1.",
        "tools": [
            {
                "type": "namespace",
                "name": "functions",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "parameters": {
                            "type": "object",
                            "properties": {"cmd": {"type": "string"}},
                            "required": ["cmd"],
                            "additionalProperties": False,
                        },
                    }
                ],
            }
        ],
    }


class RelaySmoke(unittest.TestCase):
    def run_case(self, arguments):
        sent = []

        class FakeClient:
            status_code = 200

            def __init__(self, **kwargs):
                if kwargs.get("trust_env") is not False:
                    raise AssertionError("proxy boundary changed")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def stream(self, *args, **kwargs):
                sent.append(kwargs["json"])
                return self

            async def aiter_lines(self):
                value = arguments[min(len(sent) - 1, len(arguments) - 1)]
                yield "data: " + json.dumps(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_{}".format(len(sent)),
                                            "function": {
                                                "name": "functions__exec_command",
                                                "arguments": value,
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    }
                )
                yield "data: [DONE]"

        async def collect():
            result = []
            async for frame in ADAPTER.stream_deepseek_responses(
                ADAPTER.convert_responses_request(payload())
            ):
                result.extend(
                    json.loads(line[6:])
                    for line in frame.splitlines()
                    if line.startswith("data: ")
                )
            return result

        with (
            patch.object(ADAPTER.httpx, "AsyncClient", FakeClient),
            patch.object(ADAPTER, "_upstream_key", return_value="SMOKE-NOT-A-KEY"),
        ):
            with patch.dict(
                os.environ,
                {
                    "PPT_MASTER_DEEPSEEK_MODEL": "DeepSeek-V4-Flash",
                    "PPT_MASTER_DEEPSEEK_ATTEMPTS": "2",
                },
            ):
                result = asyncio.run(collect())
        return sent, result

    def test_required_type_and_syntax_recovery(self):
        for invalid in (
            "{}",
            '{"command":"do-not-guess"}',
            '{"cmd":5}',
            '{"cmd":',
            "[]",
            '{"cmd":"A","cmd":"B"}',
        ):
            with self.subTest(invalid=invalid):
                sent, events = self.run_case([invalid, '{"cmd":"printf 2"}'])
                self.assertEqual(len(sent), 2)
                self.assertEqual(events[-1]["type"], "response.completed")
                self.assertEqual(events[-1]["response"]["usage"]["total_tokens"], 30)
                items = [
                    e["item"]
                    for e in events
                    if e["type"] == "response.output_item.done"
                ]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["call_id"], "call_2")
                self.assertEqual(items[0]["namespace"], "functions")
                self.assertEqual(json.loads(items[0]["arguments"]), {"cmd": "printf 2"})

    def test_exhaustion_does_not_dispatch(self):
        sent, events = self.run_case(["{}"])
        self.assertEqual(len(sent), 3)
        self.assertEqual(
            [e["type"] for e in events],
            ["response.created", "response.in_progress", "response.failed"],
        )
        self.assertEqual(events[-1]["response"]["usage"]["total_tokens"], 45)

    def test_good_turn_once_and_protocol_complete(self):
        sent, events = self.run_case(['{"cmd":"printf 2"}'])
        self.assertEqual(len(sent), 1)
        added = next(e for e in events if e["type"] == "response.output_item.added")
        done = next(
            e for e in events if e["type"] == "response.function_call_arguments.done"
        )
        self.assertEqual(added["item"]["arguments"], "")
        self.assertEqual(done["arguments"], '{"cmd":"printf 2"}')


if __name__ == "__main__":
    ADAPTER = load_adapter(sys.argv.pop(1))
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RelaySmoke)
    )
    if not result.wasSuccessful():
        sys.exit(1)
    print(
        "ppt_relay_offline_smoke=PASS real_model=NOT_TESTED production_workloads_mutated=no"
    )
