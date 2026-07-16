"""Tests for the local Gateway launcher."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_launcher_module():
    launcher_path = Path(__file__).parents[1] / "scripts" / "run_gateway.py"
    spec = importlib.util.spec_from_file_location("run_gateway_test", launcher_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_launcher_selects_selector_policy(monkeypatch):
    module = _load_launcher_module()

    class ExistingPolicy:
        pass

    class SelectorPolicy:
        pass

    configured = []
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(
        module.asyncio,
        "WindowsSelectorEventLoopPolicy",
        SelectorPolicy,
        raising=False,
    )
    monkeypatch.setattr(module.asyncio, "get_event_loop_policy", ExistingPolicy)
    monkeypatch.setattr(
        module.asyncio,
        "set_event_loop_policy",
        lambda policy: configured.append(policy),
    )

    module._configure_windows_event_loop_policy()

    assert len(configured) == 1
    assert isinstance(configured[0], SelectorPolicy)
