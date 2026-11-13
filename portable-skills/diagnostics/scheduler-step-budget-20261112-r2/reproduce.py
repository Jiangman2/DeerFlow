"""Run the frozen topology fixture with the exact reported production versions."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path


VERSIONS = {"langchain": "1.2.15", "langchain-core": "1.3.3", "langgraph": "1.1.9"}


def main() -> None:
    versions = {name: importlib.metadata.version(name) for name in VERSIONS}
    if versions != VERSIONS:
        raise RuntimeError(f"The exact-version fixture changed: {versions}")
    previous = Path(__file__).resolve().parent.parent / "scheduler-step-budget-20261112-r1/reproduce.py"
    spec = importlib.util.spec_from_file_location("offline_budget_fixture", previous)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hooks = module.load_fixture_hooks()
    cases = [module.trace_case(limit, hooks) for limit in (300, 1000)]
    assert cases[0]["status"] == "RECURSION_CAPPED"
    assert cases[0]["tasks"] == 300
    assert cases[0]["counts"]["model"] == 16
    assert cases[0]["counts"]["tools"] == 15
    assert cases[1]["status"] == "SUCCESS"
    assert cases[1]["tasks"] == 326
    assert cases[1]["counts"]["model"] == 17
    assert cases[1]["counts"]["tools"] == 16
    print(json.dumps({
        "schema": "scheduler-step-budget-exact-version-local-fixture.v1",
        "versions": versions,
        "configuration_is_synthetic": True,
        "live_compiled_graph_proven": False,
        "real_model_requests": 0,
        "shell_commands_executed": 0,
        "hook_counts": module.EXPECTED_HOOK_COUNTS,
        "cases": cases,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
