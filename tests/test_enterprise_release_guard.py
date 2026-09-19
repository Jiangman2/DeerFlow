from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_enterprise_release.py"
SPEC = spec_from_file_location("validate_enterprise_release", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _valid_config() -> dict:
    return {
        "models": [
            {
                "name": "deepseek-v4-flash",
                "max_tokens": 65536,
                "profile": {"max_input_tokens": 1048576},
            }
        ],
        "sandbox": {
            "shared_thread_data_mounts": True,
            "environment": {
                **MODULE.SANDBOX_PROXY_REFERENCES,
                **MODULE.SANDBOX_CA_REFERENCES,
                "IMAGE_BACKEND": "qwen",
                "IMAGE_GENERATION_PROVIDER": "dashscope",
                "QWEN_BASE_URL": "https://dashscope.aliyuncs.com",
                "QWEN_MODEL": "qwen-image-2.0-pro",
            },
        },
        "scheduler": {
            "enabled": True,
            "recursion_limit": 300,
            "max_concurrent_runs": 3,
        },
        "title": {"model_name": None},
        "summarization": {"model_name": "deepseek-v4-flash"},
        "input_polish": {"model_name": "deepseek-v4-flash"},
        "memory": {
            "backend_config": {
                "model": {"model": "deepseek-v4-flash"},
            }
        },
    }


def test_enterprise_runtime_patch_satisfies_release_guard() -> None:
    checks = MODULE.validate_manifest(ROOT / "k8s" / "enterprise" / "runtime-patches.yaml")
    assert "Provisioner Sandbox proxy/CA injection" in checks
    assert "audited direct/proxy routing policy" in checks


def test_enterprise_control_inventory_is_complete() -> None:
    checks = MODULE.validate_baseline_inventory(
        ROOT / "k8s" / "enterprise" / "release-baseline.yaml",
        ROOT,
    )
    assert checks == ["complete enterprise control inventory"]


def test_enterprise_repository_satisfies_full_release_guard() -> None:
    checks = MODULE.validate_repository(ROOT)
    assert "immutable ARM64 image policy" in checks
    assert "three-node MinerU availability policy" in checks
    assert "Sandbox and platform security source boundaries" in checks


def test_release_config_accepts_runtime_proxy_references(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(_valid_config(), sort_keys=False),
        encoding="utf-8",
    )
    assert "sandbox command proxy and CA references" in MODULE.validate_config(path)


@pytest.mark.parametrize("bad_value", [False, None])
def test_release_config_rejects_disabled_shared_thread_data_mounts(
    tmp_path: Path,
    bad_value: bool | None,
) -> None:
    config = _valid_config()
    if bad_value is None:
        config["sandbox"].pop("shared_thread_data_mounts")
    else:
        config["sandbox"]["shared_thread_data_mounts"] = bad_value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match="shared_thread_data_mounts"):
        MODULE.validate_config(path)


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("HTTP_PROXY", ""),
        ("HTTPS_PROXY", None),
        ("NO_PROXY", "127.0.0.1,localhost"),
        ("http_proxy", "$http_proxy"),
        ("https_proxy", ""),
        ("no_proxy", "$no_proxy"),
        ("SSL_CERT_FILE", ""),
        ("REQUESTS_CA_BUNDLE", None),
        ("CURL_CA_BUNDLE", "$SSL_CERT_FILE"),
        ("NODE_EXTRA_CA_CERTS", ""),
        ("OPENSSL_CONF", None),
        ("DASHSCOPE_TLS_COMPAT", "true"),
    ],
)
def test_release_config_rejects_sandbox_environment_regressions(
    tmp_path: Path,
    key: str,
    bad_value: str | None,
) -> None:
    config = _valid_config()
    environment = config["sandbox"]["environment"]
    if bad_value is None:
        environment.pop(key)
    else:
        environment[key] = bad_value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match=key):
        MODULE.validate_config(path)


def test_release_config_rejects_gemini_injection(tmp_path: Path) -> None:
    config = _valid_config()
    config["sandbox"]["environment"]["GEMINI_API_KEY"] = "$GEMINI_API_KEY"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match="GEMINI_API_KEY"):
        MODULE.validate_config(path)


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("IMAGE_BACKEND", "dashscope"),
        ("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com"),
        ("QWEN_MODEL", ""),
    ],
)
def test_release_config_rejects_ppt_qwen_contract_regressions(
    tmp_path: Path,
    key: str,
    bad_value: str,
) -> None:
    config = _valid_config()
    config["sandbox"]["environment"][key] = bad_value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match=key):
        MODULE.validate_config(path)


def test_release_config_rejects_extra_chat_model(tmp_path: Path) -> None:
    config = _valid_config()
    config["models"].append({"name": "qwen-plus"})
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match="deepseek-v4-flash"):
        MODULE.validate_config(path)


@pytest.mark.parametrize(
    ("path_parts", "bad_value", "error_field"),
    [
        (("max_tokens",), 32768, "max_tokens"),
        (("profile", "max_input_tokens"), 1000000, "max_input_tokens"),
    ],
)
def test_release_config_rejects_deepseek_token_limit_regressions(
    tmp_path: Path,
    path_parts: tuple[str, ...],
    bad_value: int,
    error_field: str,
) -> None:
    config = _valid_config()
    target = config["models"][0]
    for part in path_parts[:-1]:
        target = target[part]
    target[path_parts[-1]] = bad_value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match=error_field):
        MODULE.validate_config(path)


def test_release_config_rejects_unbounded_scheduler_change(tmp_path: Path) -> None:
    config = _valid_config()
    config["scheduler"]["recursion_limit"] = 1000
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(MODULE.ValidationError, match="recursion_limit"):
        MODULE.validate_config(path)
