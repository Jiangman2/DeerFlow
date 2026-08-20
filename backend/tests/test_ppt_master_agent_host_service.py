import importlib.util
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _ROOT / "docker" / "sandbox-ppt-master" / "agent_host.py"
_SPEC = importlib.util.spec_from_file_location("ppt_master_agent_host_service", _MODULE_PATH)
assert _SPEC and _SPEC.loader
host = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(host)


def test_codex_command_uses_real_cli_and_resumes_the_persisted_session():
    initial = host.build_codex_command(session_id=None)
    resumed = host.build_codex_command(session_id="session-123")

    assert initial[:2] == ["codex", "exec"]
    assert "--json" in initial
    assert "--dangerously-bypass-approvals-and-sandbox" in initial
    assert "$ppt-master" not in " ".join(initial)
    assert resumed[:4] == ["codex", "exec", "resume", "session-123"]
    assert "--json" in resumed
    # `--color` belongs to `codex exec`, but Codex CLI does not expose it on
    # the `exec resume` subcommand. Passing it makes every continuation exit 2
    # before the persisted Agent session can start.
    assert "--color" not in resumed


def test_agent_host_fails_closed_without_a_real_codex_credential(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    assert not host.has_codex_credentials({}, codex_home)
    (codex_home / "auth.json").write_text('{"tokens": {"access_token": "test"}}', encoding="utf-8")
    assert host.has_codex_credentials({}, codex_home)


def test_agent_host_requires_the_selected_custom_provider_key(tmp_path: Path):
    config = """
model = "qwen3.7-max"
model_provider = "model_studio"
[model_providers.model_studio]
name = "Alibaba Cloud Model Studio"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
env_key = "DASHSCOPE_API_KEY"
wire_api = "responses"
"""

    assert not host.has_codex_credentials({"CODEX_CONFIG_TOML": config}, tmp_path / "missing")
    assert host.has_codex_credentials(
        {"CODEX_CONFIG_TOML": config, "DASHSCOPE_API_KEY": "test-dashscope-key"},
        tmp_path / "missing",
    )


def test_agent_host_restores_missing_writable_user_data_roots(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "user-data" / "workspace"
    uploads = tmp_path / "user-data" / "uploads"
    outputs = tmp_path / "user-data" / "outputs"
    temp_root = workspace / ".ppt-master-agent" / "tmp"
    uploads.mkdir(parents=True)
    monkeypatch.setattr(host, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(host, "UPLOADS_ROOT", uploads)
    monkeypatch.setattr(host, "OUTPUTS_ROOT", outputs)
    monkeypatch.setattr(host, "TEMP_ROOT", temp_root)

    restored = host.ensure_user_data_directories()

    assert restored == [str(workspace), str(outputs)]
    assert workspace.is_dir()
    assert uploads.is_dir()
    assert outputs.is_dir()
    assert temp_root.is_dir()


def test_agent_subprocess_temp_is_pinned_to_persistent_workspace(tmp_path: Path, monkeypatch):
    temp_root = tmp_path / "workspace" / ".ppt-master-agent" / "tmp"
    monkeypatch.setattr(host, "TEMP_ROOT", temp_root)

    env = host._agent_subprocess_env({"PATH": "test-path", "TMPDIR": "/tmp"})

    assert env["PATH"] == "test-path"
    assert env["TMPDIR"] == str(temp_root)
    assert env["TMP"] == str(temp_root)
    assert env["TEMP"] == str(temp_root)
    assert temp_root.is_dir()


def test_agent_host_writes_valid_operator_model_catalog(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(host, "CODEX_HOME", tmp_path / "codex-home")
    monkeypatch.setattr(host, "RUNTIME_ROOT", tmp_path / "runtime")
    monkeypatch.setattr(host, "TEMP_ROOT", tmp_path / "temp")
    monkeypatch.setattr(Path, "symlink_to", lambda self, target, target_is_directory=False: None)
    host.RUNTIME_ROOT.mkdir()
    catalog = {
        "models": [
            {
                "slug": "qwen3.7-max",
                "display_name": "qwen3.7-max",
                "context_window": 1_000_000,
            }
        ]
    }
    config = 'model = "qwen3.7-max"\nmodel_provider = "model_studio"\n'

    host._prepare_codex_home(
        {
            "CODEX_CONFIG_TOML": config,
            "CODEX_MODEL_CATALOG_JSON": __import__("json").dumps(catalog),
        }
    )

    assert (host.CODEX_HOME / "config.toml").read_text(encoding="utf-8") == config
    assert __import__("json").loads((host.CODEX_HOME / "model-catalog.local.json").read_text(encoding="utf-8")) == catalog


def test_writable_skill_snapshot_is_an_exact_refresh_not_a_custom_template_merge(tmp_path: Path):
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    (source / "scripts").mkdir(parents=True)
    (source / "workflows").mkdir()
    (source / "SKILL.md").write_text('version: "4.5.0"\n', encoding="utf-8")
    (source / "scripts" / "attribution_guard.py").write_text("print('ok')\n", encoding="utf-8")
    runtime.mkdir()
    (runtime / "SKILL.md").write_text('version: "4.3.0"\n', encoding="utf-8")
    (runtime / "custom-template.txt").write_text("must disappear", encoding="utf-8")

    host.prepare_writable_skill_snapshot(source, runtime)

    assert (runtime / "SKILL.md").read_text(encoding="utf-8") == 'version: "4.5.0"\n'
    assert not (runtime / "custom-template.txt").exists()
    assert (runtime / ".upstream-source.sha256").is_file()


def test_task_and_thread_ids_cannot_escape_the_persistent_workspace():
    assert host.validate_id("a12_B-c") == "a12_B-c"
    for invalid in ("../escape", "a/b", "", "x" * 129):
        try:
            host.validate_id(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid id was accepted: {invalid!r}")


def test_delivery_contract_rejects_claimed_native_capabilities_missing_from_package(tmp_path: Path, monkeypatch):
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
    report = {
        "schema": "ppt-master.pptx-delivery-check.v1",
        "status": "passed",
        "package": {"parts": {"slides": 1, "notes": 1, "masters": 1, "layouts": 1}},
        "slides": {"count": 1},
        "media": {"embedded": []},
        "motion": {
            "transitions": {"visual_effect_slide_count": 1, "timed_advance_slide_count": 0},
            "object_animations": {"object_animation_slide_count": 0},
        },
    }
    monkeypatch.setattr(host, "_official_delivery_report", lambda _path: report)

    issues = host.validate_delivery_contract(
        pptx,
        {
            "expected_slides": 1,
            "required_capabilities": {"speaker_notes": True, "transitions": True, "object_animations": True, "native_charts": True},
        },
    )

    assert issues == ["object animations are missing", "editable native chart parts are missing"]


def test_delivery_contract_accepts_real_chart_and_animation_facts(tmp_path: Path, monkeypatch):
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
        archive.writestr("ppt/charts/chart1.xml", "<c:chartSpace/>")
    report = {
        "schema": "ppt-master.pptx-delivery-check.v1",
        "status": "passed",
        "package": {"parts": {"slides": 1, "notes": 1, "masters": 1, "layouts": 1}},
        "slides": {"count": 1},
        "media": {"embedded": []},
        "motion": {
            "transitions": {"visual_effect_slide_count": 1, "timed_advance_slide_count": 0},
            "object_animations": {"object_animation_slide_count": 1},
        },
    }
    monkeypatch.setattr(host, "_official_delivery_report", lambda _path: report)

    assert (
        host.validate_delivery_contract(
            pptx,
            {
                "expected_slides": 1,
                "required_capabilities": {"speaker_notes": True, "transitions": True, "object_animations": True, "native_charts": True},
            },
        )
        == []
    )
