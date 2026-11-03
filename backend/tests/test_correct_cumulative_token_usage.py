"""Pure-function tests for the cumulative stream usage repair command."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "correct_cumulative_token_usage.py"
SPEC = importlib.util.spec_from_file_location("correct_cumulative_token_usage", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _run(**overrides):
    base = {
        "run_id": "run-1",
        "model_name": "deepseek-v4-flash",
        "total_input_tokens": 1_001_100,
        "total_output_tokens": 2_000,
        "total_tokens": 1_003_100,
        "lead_agent_tokens": 1_003_100,
        "subagent_tokens": 0,
        "middleware_tokens": 0,
        "token_usage_by_model": {
            "deepseek-v4-flash": {
                "input_tokens": 1_001_100,
                "output_tokens": 2_000,
                "total_tokens": 1_003_100,
                "cache_read_tokens": 900_000,
            }
        },
        "metadata_json": {"preserved": True},
    }
    return {**base, **overrides}


def _event(*, seq, call_index, input_tokens, output_tokens, caller="lead_agent"):
    return {
        "run_id": "run-1",
        "model_name": "deepseek-v4-flash",
        "seq": seq,
        "content": {"response_metadata": {"model_name": "deepseek-v4-flash"}},
        "event_metadata": {
            "caller": caller,
            "llm_call_index": call_index,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_token_details": {"cache_read": 900_000},
            },
        },
    }


def test_quarantines_only_impossible_calls_and_keeps_normal_usage():
    events = [
        _event(seq=1, call_index=1, input_tokens=1_000_000, output_tokens=1_500),
        _event(seq=2, call_index=2, input_tokens=1_100, output_tokens=500),
    ]

    correction = MODULE.build_run_correction(
        _run(),
        events,
        suspicious_call_threshold=1_000_000,
    )

    assert correction is not None
    assert len(correction.suspicious_calls) == 1
    assert correction.corrected["total_input_tokens"] == 1_100
    assert correction.corrected["total_output_tokens"] == 500
    assert correction.corrected["total_tokens"] == 1_600
    assert correction.corrected["lead_agent_tokens"] == 1_600
    assert correction.corrected["token_usage_by_model"] == {
        "deepseek-v4-flash": {
            "input_tokens": 1_100,
            "output_tokens": 500,
            "total_tokens": 1_600,
        }
    }
    assert correction.original["metadata_json"] == {"preserved": True}


def test_preserves_external_subagent_usage_not_present_in_response_events():
    run = _run(
        total_input_tokens=1_000_200,
        total_output_tokens=1_100,
        total_tokens=1_001_300,
        lead_agent_tokens=1_001_000,
        subagent_tokens=300,
        token_usage_by_model={
            "deepseek-v4-flash": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000,
                "total_tokens": 1_001_000,
            },
            "subagent-model": {
                "input_tokens": 200,
                "output_tokens": 100,
                "total_tokens": 300,
            },
        },
    )
    correction = MODULE.build_run_correction(
        run,
        [_event(seq=1, call_index=1, input_tokens=1_000_000, output_tokens=1_000)],
        suspicious_call_threshold=1_000_000,
    )

    assert correction is not None
    assert correction.corrected["total_tokens"] == 300
    assert correction.corrected["lead_agent_tokens"] == 0
    assert correction.corrected["subagent_tokens"] == 300
    assert correction.corrected["token_usage_by_model"] == {
        "subagent-model": {
            "input_tokens": 200,
            "output_tokens": 100,
            "total_tokens": 300,
        }
    }


def test_deduplicates_repeated_callback_events_by_call_index():
    duplicated = _event(
        seq=1,
        call_index=7,
        input_tokens=1_000_000,
        output_tokens=1_500,
    )
    correction = MODULE.build_run_correction(
        _run(),
        [duplicated, {**duplicated, "seq": 2}],
        suspicious_call_threshold=1_000_000,
    )

    assert correction is not None
    assert len(correction.suspicious_calls) == 1
    assert correction.corrected["total_tokens"] == 1_600


def test_returns_none_when_no_call_crosses_the_threshold():
    correction = MODULE.build_run_correction(
        _run(
            total_input_tokens=2_000,
            total_output_tokens=500,
            total_tokens=2_500,
            lead_agent_tokens=2_500,
        ),
        [_event(seq=1, call_index=1, input_tokens=2_000, output_tokens=500)],
        suspicious_call_threshold=1_000_000,
    )

    assert correction is None
