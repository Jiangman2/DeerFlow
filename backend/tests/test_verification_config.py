"""Tests for the verification config section."""

from pathlib import Path

import yaml

from deerflow.config.verification_config import VerificationConfig


def test_defaults_receipts_on_judge_off():
    config = VerificationConfig()
    assert config.receipts_enabled is True
    assert config.receipts_render_mode == "delegation_only"
    assert config.judge_enabled is False
    assert config.judge_model_name is None


def test_app_config_carries_verification_section():
    from deerflow.config.app_config import AppConfig

    app_config = AppConfig.model_validate({"sandbox": {"use": "test"}})
    assert app_config.verification.receipts_enabled is True
    assert app_config.verification.judge_enabled is False


def test_versioned_example_publishes_verification_section():
    example_path = Path(__file__).resolve().parents[2] / "config.example.yaml"
    example = yaml.safe_load(example_path.read_text(encoding="utf-8"))

    assert example["config_version"] >= 34
    assert example["verification"] == {
        "receipts_enabled": True,
        "receipts_render_mode": "delegation_only",
        "judge_enabled": False,
        "judge_model_name": None,
    }


def test_helm_and_example_receipts_defaults_agree():
    root = Path(__file__).resolve().parents[2]
    example = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))
    values = yaml.safe_load((root / "deploy/helm/deer-flow/values.yaml").read_text(encoding="utf-8"))
    helm_config = yaml.safe_load(values["config"])
    assert helm_config["config_version"] == example["config_version"]
    assert helm_config["verification"] == example["verification"]


def test_lead_mode_is_configured_but_children_always_see_the_ledger():
    from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares, build_subagent_runtime_middlewares
    from deerflow.agents.middlewares.tool_receipt_middleware import ToolReceiptMiddleware
    from deerflow.config.app_config import AppConfig

    config = AppConfig.model_validate({"sandbox": {"use": "test"}})
    lead = build_lead_runtime_middlewares(app_config=config)
    child = build_subagent_runtime_middlewares(app_config=config, lazy_init=True)
    assert [m._render_mode for m in lead if isinstance(m, ToolReceiptMiddleware)] == ["delegation_only"]
    assert [m._render_mode for m in child if isinstance(m, ToolReceiptMiddleware)] == ["always"]
    config.verification.receipts_render_mode = "always"
    lead = build_lead_runtime_middlewares(app_config=config)
    assert [m._render_mode for m in lead if isinstance(m, ToolReceiptMiddleware)] == ["always"]
    config.verification.receipts_enabled = False
    assert not any(isinstance(m, ToolReceiptMiddleware) for m in build_subagent_runtime_middlewares(app_config=config, lazy_init=True))
