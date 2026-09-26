#!/usr/bin/env python3
"""Thread-scoped HTTP host for the real Codex ``ppt-master`` Agent.

This service runs as a second container in each provisioned Kubernetes Sandbox
Pod. It owns only process/session lifecycle and artifact handoff. The vendored
Skill remains authoritative for presentation routing, stages, confirmation,
validation, and recovery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 production Sandbox compatibility.
    import tomli as tomllib

AGENT_HOST_PORT = int(os.environ.get("PPT_MASTER_AGENT_HOST_PORT", "8788"))
SOURCE_SKILL_ROOT = Path(
    os.environ.get("PPT_MASTER_SOURCE_ROOT", "/opt/ppt-master-release-skill")
)
WORKSPACE_ROOT = Path(os.environ.get("PPT_MASTER_WORKSPACE_ROOT", "/mnt/user-data/workspace"))
UPLOADS_ROOT = Path(os.environ.get("PPT_MASTER_UPLOADS_ROOT", "/mnt/user-data/uploads"))
OUTPUTS_ROOT = Path(os.environ.get("PPT_MASTER_OUTPUTS_ROOT", "/mnt/user-data/outputs"))
RUNTIME_ROOT = Path(os.environ.get("PPT_MASTER_RUNTIME_ROOT", str(WORKSPACE_ROOT / ".ppt-master-runtime")))
HOST_STATE_ROOT = Path(os.environ.get("PPT_MASTER_AGENT_STATE_ROOT", str(WORKSPACE_ROOT / ".ppt-master-agent")))
CODEX_HOME = Path(os.environ.get("PPT_MASTER_CODEX_HOME", str(HOST_STATE_ROOT / "codex-home")))
TEMP_ROOT = Path(os.environ.get("PPT_MASTER_TEMP_ROOT", str(HOST_STATE_ROOT / "tmp")))
MAX_REQUEST_BYTES = 1024 * 1024
MAX_EVENT_TEXT = 256 * 1024
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_SECRET_NAME_RE = re.compile(r"(?:KEY|SECRET|TOKEN|PASS|CREDENTIAL)", re.IGNORECASE)
_MODEL_PROVIDER_RE = re.compile(r'^\s*model_provider\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
_ENV_KEY_RE = re.compile(r'^\s*env_key\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
_ENTERPRISE_RELAY_REQUIRED_VALUES = frozenset({"1", "true", "yes", "on"})
_ENTERPRISE_RELAY_PROVIDER = "cnooc_deepseek"
_ENTERPRISE_RELAY_BASE_URL = "http://gateway:8001/api/codex/deepseek/v1"
_ENTERPRISE_RELAY_ENV_KEY = "PPT_MASTER_RESPONSES_RELAY_KEY"
_EXPECTED_PPT_MASTER_VERSION = "6.6.0"
_V66_REQUIRED_PATHS = (
    "workflows/edit-native-pptx.md",
    "workflows/profiles/image-to-pptx.md",
    "scripts/authoring_roundtrip.py",
    "scripts/pptx_workspace.py",
    "templates/decks/cnooc/templates/design_spec.md",
)
_RETIRED_V45_PATHS = (
    "workflows/native-enhance-pptx.md",
    "workflows/template-fill-pptx.md",
)
_DELIVERY_CAPABILITIES = frozenset(
    {
        "speaker_notes",
        "transitions",
        "timed_advance",
        "object_animations",
        "native_charts",
        "ai_images",
        "narration_audio",
        "template",
    }
)
MAX_DELIVERY_REPAIR_ATTEMPTS = 3


def ensure_user_data_directories() -> list[str]:
    """Restore the writable per-thread directory contract when it is missing.

    The Sandbox main container and the Agent host share the same user-data
    volume.  A resumed or delivery-repair turn must therefore not assume that
    the three writable roots still exist merely because the Pod is healthy.
    Return the paths that had to be recreated so the host can emit a useful
    diagnostic receipt without treating this recoverable condition as a task
    failure.
    """

    restored: list[str] = []
    for path in (WORKSPACE_ROOT, UPLOADS_ROOT, OUTPUTS_ROOT):
        existed = path.is_dir()
        path.mkdir(mode=0o777, parents=True, exist_ok=True)
        if not path.is_dir():
            raise RuntimeError(f"PPT Agent writable directory is unavailable: {path}")
        os.chmod(path, 0o777)
        if not existed:
            restored.append(str(path))
    TEMP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(TEMP_ROOT, 0o700)
    return restored


def _agent_subprocess_env(env: dict[str, str]) -> dict[str, str]:
    """Pin PPT-only subprocess temporary files to the per-thread PVC."""

    TEMP_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(TEMP_ROOT, 0o700)
    return {
        **env,
        "TMPDIR": str(TEMP_ROOT),
        "TMP": str(TEMP_ROOT),
        "TEMP": str(TEMP_ROOT),
    }


def validate_id(value: object) -> str:
    candidate = value if isinstance(value, str) else ""
    if not _SAFE_ID_RE.fullmatch(candidate):
        raise ValueError("task_id and thread_id must match ^[A-Za-z0-9_-]{1,128}$")
    return candidate


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((candidate for candidate in root.rglob("*") if candidate.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def validate_ppt_master_source_contract(source: Path) -> dict[str, str]:
    """Fail closed unless the immutable source is the adapted v6.6.0 release."""

    skill_path = source / "SKILL.md"
    if not skill_path.is_file():
        raise RuntimeError(f"ppt-master source package is incomplete: {source}")
    skill_text = skill_path.read_text(encoding="utf-8")
    frontmatter_end = skill_text.find("\n---\n", 4)
    frontmatter = skill_text[4:frontmatter_end] if skill_text.startswith("---\n") and frontmatter_end >= 0 else ""
    version_match = re.search(r'^\s{2}version:\s*["\']([^"\']+)["\']\s*$', frontmatter, re.MULTILINE)
    version = version_match.group(1) if version_match else ""
    if version != _EXPECTED_PPT_MASTER_VERSION:
        raise RuntimeError(
            "ppt-master mounted Skill version mismatch: "
            f"version={version!r} expected={_EXPECTED_PPT_MASTER_VERSION!r}"
        )
    missing = [relative for relative in _V66_REQUIRED_PATHS if not (source / relative).is_file()]
    if missing:
        raise RuntimeError("ppt-master v6.6 mounted Skill is missing required paths: " + ", ".join(missing))
    retired = [relative for relative in _RETIRED_V45_PATHS if (source / relative).exists()]
    if retired:
        raise RuntimeError("ppt-master mounted Skill still contains retired v4.5 routes: " + ", ".join(retired))
    try:
        deck_index = json.loads((source / "templates" / "decks" / "decks_index.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("ppt-master Deck registry is unreadable") from exc
    if not isinstance(deck_index, dict) or "cnooc" not in deck_index:
        raise RuntimeError("ppt-master enterprise CNOOC Deck registration is missing")
    return {"ppt_master_version": version}


def prepare_writable_skill_snapshot(source: Path, target: Path) -> str:
    """Atomically refresh an exact writable copy of the mounted upstream Skill."""

    if not (source / "SKILL.md").is_file() or not (source / "scripts").is_dir() or not (source / "workflows").is_dir():
        raise RuntimeError(f"ppt-master source package is incomplete: {source}")
    source_hash = _tree_sha256(source)
    marker = target / ".upstream-source.sha256"
    if target.is_dir() and marker.is_file() and marker.read_text(encoding="utf-8").strip() == source_hash:
        return source_hash

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f"{target.name}.installing-{os.getpid()}-{threading.get_ident()}")
    backup = target.with_name(f"{target.name}.previous-{os.getpid()}-{threading.get_ident()}")
    if staging.exists():
        shutil.rmtree(staging)
    if backup.exists():
        shutil.rmtree(backup)
    shutil.copytree(source, staging, symlinks=True)
    (staging / ".upstream-source.sha256").write_text(source_hash + "\n", encoding="utf-8")
    if target.exists():
        target.replace(backup)
    try:
        staging.replace(target)
    except BaseException:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    return source_hash


def _configured_provider_env_key(config_text: str) -> str | None:
    provider_match = _MODEL_PROVIDER_RE.search(config_text)
    if provider_match is None:
        return None
    provider_name = provider_match.group(1)
    section_re = re.compile(
        rf'^\s*\[model_providers\.(?:"{re.escape(provider_name)}"|\'{re.escape(provider_name)}\'|{re.escape(provider_name)})\]\s*$',
        re.MULTILINE,
    )
    section_match = section_re.search(config_text)
    if section_match is None:
        return None
    section_start = section_match.end()
    next_section = re.search(r"^\s*\[", config_text[section_start:], re.MULTILINE)
    section_end = section_start + next_section.start() if next_section else len(config_text)
    env_key_match = _ENV_KEY_RE.search(config_text[section_start:section_end])
    return env_key_match.group(1) if env_key_match else None


def _enterprise_relay_required(env: dict[str, str]) -> bool:
    return env.get("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "").strip().lower() in _ENTERPRISE_RELAY_REQUIRED_VALUES


def _codex_provider_metadata(config_text: str) -> dict[str, str]:
    """Return the selected provider contract without exposing credentials."""

    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError("CODEX_CONFIG_TOML is not valid TOML") from exc
    provider_name = config.get("model_provider")
    model_name = config.get("model")
    providers = config.get("model_providers")
    provider = providers.get(provider_name) if isinstance(providers, dict) and isinstance(provider_name, str) else None
    if not isinstance(provider_name, str) or not provider_name or not isinstance(provider, dict):
        raise RuntimeError("CODEX_CONFIG_TOML does not define its selected model provider")
    metadata = {
        "model": model_name if isinstance(model_name, str) else "",
        "provider": provider_name,
        "base_url": str(provider.get("base_url") or "").rstrip("/"),
        "env_key": str(provider.get("env_key") or ""),
        "wire_api": str(provider.get("wire_api") or ""),
    }
    return metadata


def validate_codex_provider_contract(env: dict[str, str], codex_home: Path) -> dict[str, str]:
    """Fail closed when enterprise PPT traffic could bypass the Gateway relay."""

    config_text = env.get("CODEX_CONFIG_TOML")
    if _enterprise_relay_required(env) and not config_text:
        raise RuntimeError("enterprise PPT Agent requires operator-supplied CODEX_CONFIG_TOML")
    config_path = codex_home / "config.toml"
    if not config_text and config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
    if not config_text:
        return {}
    metadata = _codex_provider_metadata(config_text)
    if not _enterprise_relay_required(env):
        return metadata
    expected = {
        "provider": _ENTERPRISE_RELAY_PROVIDER,
        "base_url": _ENTERPRISE_RELAY_BASE_URL,
        "env_key": _ENTERPRISE_RELAY_ENV_KEY,
        "wire_api": "responses",
    }
    mismatches = [
        f"{name}={metadata.get(name)!r} expected={value!r}"
        for name, value in expected.items()
        if metadata.get(name) != value
    ]
    if mismatches:
        raise RuntimeError("enterprise PPT Agent provider contract mismatch: " + "; ".join(mismatches))
    if not env.get(_ENTERPRISE_RELAY_ENV_KEY):
        raise RuntimeError("enterprise PPT Agent relay credential is missing")
    return metadata


def has_codex_credentials(env: dict[str, str], codex_home: Path) -> bool:
    if (codex_home / "auth.json").is_file():
        return True
    config_text = env.get("CODEX_CONFIG_TOML")
    config_path = codex_home / "config.toml"
    if not config_text and config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
    if not config_text:
        return False
    env_key = _configured_provider_env_key(config_text)
    if env_key:
        return bool(env.get(env_key))
    return any(env.get(name) for name in ("CODEX_API_KEY", "ARK_API_KEY", "OPENAI_API_KEY"))


def _agent_command() -> list[str]:
    configured = os.environ.get("PPT_MASTER_AGENT_COMMAND", "codex").strip()
    command = shlex.split(configured)
    if not command:
        raise RuntimeError("PPT_MASTER_AGENT_COMMAND is empty")
    return command


def build_codex_command(*, session_id: str | None) -> list[str]:
    command = _agent_command()
    if session_id:
        return [
            *command,
            "exec",
            "resume",
            session_id,
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-",
        ]
    return [
        *command,
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--color",
        "never",
        "-C",
        str(WORKSPACE_ROOT),
        "--add-dir",
        str(UPLOADS_ROOT),
        "--add-dir",
        str(OUTPUTS_ROOT),
        "-",
    ]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _secret_values(env: dict[str, str]) -> list[str]:
    return sorted(
        {value for name, value in env.items() if _SECRET_NAME_RE.search(name) and isinstance(value, str) and len(value) >= 8},
        key=len,
        reverse=True,
    )


def _redact_text(text: str, secret_values: list[str]) -> str:
    result = text
    for secret in secret_values:
        result = result.replace(secret, "[REDACTED]")
    if len(result) > MAX_EVENT_TEXT:
        return result[:MAX_EVENT_TEXT] + "\n... (truncated by Agent host)"
    return result


def _sanitize_payload(value: object, secret_values: list[str]) -> object:
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, list):
        return [_sanitize_payload(item, secret_values) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_payload(item, secret_values) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def codex_turn_usage_record(
    event: dict[str, Any],
    *,
    task_id: str,
    turn_index: int,
    model_name: str,
) -> dict[str, int | str] | None:
    """Normalize one official ``codex exec --json`` turn usage event.

    Codex reports cached input as a subset of ``input_tokens``.  It is retained
    as a separate diagnostic dimension and must not be added to the total a
    second time.
    """

    if event.get("type") != "turn.completed":
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None

    def nonnegative_int(name: str) -> int:
        value = usage.get(name, 0)
        if isinstance(value, bool):
            return 0
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    input_tokens = nonnegative_int("input_tokens")
    output_tokens = nonnegative_int("output_tokens")
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None
    return {
        "source_run_id": f"ppt-master:{task_id}:codex-turn:{turn_index}",
        "caller": "lead_agent:ppt-master",
        "model_name": model_name or "ppt-master-agent-host",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_tokens": nonnegative_int("cached_input_tokens"),
        "reasoning_output_tokens": nonnegative_int("reasoning_output_tokens"),
        "llm_call_count": 1,
    }


def codex_failure_detail(event: dict[str, Any]) -> str:
    """Extract a bounded operator-safe diagnostic from a Codex error event."""

    if event.get("type") not in {"error", "turn.failed"}:
        return ""

    def collect(value: object) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, dict):
            parts: list[str] = []
            for key in ("code", "type", "message", "detail"):
                if key in value:
                    parts.extend(collect(value[key]))
            return parts
        if isinstance(value, list):
            parts: list[str] = []
            for item in value[:8]:
                parts.extend(collect(item))
            return parts
        return []

    parts: list[str] = []
    for key in ("error", "message", "detail"):
        if key in event:
            parts.extend(collect(event[key]))
    unique: list[str] = []
    for part in parts:
        if part and part not in unique:
            unique.append(part)
    return " | ".join(unique)[:8_000]


def _prepare_codex_home(env: dict[str, str]) -> None:
    env = _agent_subprocess_env(env)
    validate_codex_provider_contract(env, CODEX_HOME)
    CODEX_HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(CODEX_HOME, 0o700)
    skills_dir = CODEX_HOME / "skills"
    skills_dir.mkdir(exist_ok=True)
    skill_link = skills_dir / "ppt-master"
    if skill_link.is_symlink() or skill_link.is_file():
        skill_link.unlink()
    elif skill_link.exists():
        shutil.rmtree(skill_link)
    skill_link.symlink_to(RUNTIME_ROOT, target_is_directory=True)

    auth_source = Path(env.get("CODEX_AUTH_PATH", "/root/.codex/auth.json"))
    auth_target = CODEX_HOME / "auth.json"
    if auth_source.is_file() and auth_source.resolve() != auth_target.resolve():
        shutil.copy2(auth_source, auth_target)
        os.chmod(auth_target, 0o600)

    config_target = CODEX_HOME / "config.toml"
    config_toml = env.get("CODEX_CONFIG_TOML")
    if config_toml:
        if "\x00" in config_toml or len(config_toml.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise RuntimeError("CODEX_CONFIG_TOML is invalid or too large")
        config_target.write_text(config_toml, encoding="utf-8")
        os.chmod(config_target, 0o600)
    else:
        renderer = Path("/opt/gem/render-codex-config.sh")
        if renderer.is_file():
            completed = subprocess.run(
                [str(renderer)],
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            rendered = completed.stdout.strip()
            if completed.returncode != 0:
                raise RuntimeError("Codex configuration renderer failed")
            if rendered:
                rendered_path = Path(rendered)
                if not rendered_path.is_file():
                    raise RuntimeError("Codex configuration renderer returned a missing file")
                shutil.copy2(rendered_path, config_target)
                os.chmod(config_target, 0o600)

    catalog_json = env.get("CODEX_MODEL_CATALOG_JSON")
    if catalog_json:
        try:
            catalog = json.loads(catalog_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CODEX_MODEL_CATALOG_JSON is invalid") from exc
        if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
            raise RuntimeError("CODEX_MODEL_CATALOG_JSON must contain a models array")
        catalog_target = CODEX_HOME / "model-catalog.local.json"
        catalog_target.write_text(
            json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.chmod(catalog_target, 0o600)


def _virtual_output_path(path: Path) -> str:
    relative = path.relative_to(OUTPUTS_ROOT)
    return str(PurePosixPath("/mnt/user-data/outputs") / PurePosixPath(relative.as_posix()))


def _normalize_delivery_contract(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    raw_capabilities = raw.get("required_capabilities")
    capabilities = {name: True for name, enabled in (raw_capabilities.items() if isinstance(raw_capabilities, dict) else []) if name in _DELIVERY_CAPABILITIES and enabled is True}
    expected_slides = raw.get("expected_slides")
    if not isinstance(expected_slides, int) or isinstance(expected_slides, bool) or not 1 <= expected_slides <= 100:
        expected_slides = None
    return {"required_capabilities": capabilities, "expected_slides": expected_slides}


def _output_fingerprints() -> dict[str, tuple[int, int]]:
    if not OUTPUTS_ROOT.is_dir():
        return {}
    return {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in OUTPUTS_ROOT.rglob("*.pptx") if path.is_file()}


def _candidate_pptx_files(baseline: dict[str, tuple[int, int]]) -> list[Path]:
    if not OUTPUTS_ROOT.is_dir():
        return []
    candidates = []
    for path in OUTPUTS_ROOT.rglob("*.pptx"):
        if not path.is_file():
            continue
        fingerprint = (path.stat().st_size, path.stat().st_mtime_ns)
        if baseline.get(str(path)) != fingerprint:
            candidates.append(path)
    return sorted(candidates, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def _official_delivery_report(pptx: Path) -> dict[str, Any]:
    checker = RUNTIME_ROOT / "scripts" / "pptx_delivery_check.py"
    completed = subprocess.run(
        ["python3", str(checker), str(pptx)],
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise RuntimeError(f"official pptx_delivery_check.py failed: {detail}")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("official pptx_delivery_check.py returned invalid JSON") from exc
    if not isinstance(report, dict):
        raise RuntimeError("official pptx_delivery_check.py returned a non-object report")
    return report


def _native_chart_count(pptx: Path) -> int:
    with zipfile.ZipFile(pptx, "r") as archive:
        return sum(1 for name in archive.namelist() if re.fullmatch(r"ppt/charts/chart[1-9]\d*\.xml", name))


def validate_delivery_contract(pptx: Path, contract: dict[str, Any]) -> list[str]:
    """Compare explicit requirements with facts from the upstream read-only audit."""

    report = _official_delivery_report(pptx)
    issues: list[str] = []
    if report.get("status") == "failed":
        issues.append("official pptx_delivery_check.py reported failure")
    package = report.get("package") if isinstance(report.get("package"), dict) else {}
    parts = package.get("parts") if isinstance(package.get("parts"), dict) else {}
    slides = report.get("slides") if isinstance(report.get("slides"), dict) else {}
    motion = report.get("motion") if isinstance(report.get("motion"), dict) else {}
    transitions = motion.get("transitions") if isinstance(motion.get("transitions"), dict) else {}
    animations = motion.get("object_animations") if isinstance(motion.get("object_animations"), dict) else {}
    media = report.get("media") if isinstance(report.get("media"), dict) else {}
    embedded = media.get("embedded") if isinstance(media.get("embedded"), list) else []
    image_count = sum(1 for item in embedded if isinstance(item, dict) and item.get("kind") == "image")
    audio_count = sum(1 for item in embedded if isinstance(item, dict) and item.get("kind") == "audio")
    slide_count = int(slides.get("count") or parts.get("slides") or 0)
    expected_slides = contract.get("expected_slides")
    if expected_slides and slide_count != expected_slides:
        issues.append(f"slide count requires {expected_slides}, actual {slide_count}")
    required = contract.get("required_capabilities") or {}
    if required.get("speaker_notes") and int(parts.get("notes") or 0) < slide_count:
        issues.append(f"speaker notes require {slide_count} slides, actual {int(parts.get('notes') or 0)}")
    if required.get("transitions") and int(transitions.get("visual_effect_slide_count") or 0) == 0:
        issues.append("page transitions are missing")
    if required.get("timed_advance") and int(transitions.get("timed_advance_slide_count") or 0) < slide_count:
        issues.append("timed advance does not cover every slide")
    if required.get("object_animations") and int(animations.get("object_animation_slide_count") or 0) == 0:
        issues.append("object animations are missing")
    if required.get("native_charts") and _native_chart_count(pptx) == 0:
        issues.append("editable native chart parts are missing")
    if required.get("ai_images") and image_count == 0:
        issues.append("embedded AI image output is missing")
    if required.get("narration_audio") and audio_count == 0:
        issues.append("embedded narration audio is missing")
    if required.get("template") and (int(parts.get("masters") or 0) == 0 or int(parts.get("layouts") or 0) == 0):
        issues.append("template master/layout parts are missing")
    return issues


def _ensure_pptx_preview(pptx: Path) -> Path:
    preview = pptx.with_name(f"{pptx.stem}.preview.pdf")
    if preview.is_file() and preview.stat().st_size > 0 and preview.stat().st_mtime_ns >= pptx.stat().st_mtime_ns:
        return preview
    with tempfile.TemporaryDirectory(prefix="ppt-preview-") as temporary_dir:
        completed = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", temporary_dir, str(pptx)],
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        converted = Path(temporary_dir) / f"{pptx.stem}.pdf"
        if completed.returncode != 0 or not converted.is_file():
            raise RuntimeError(f"PowerPoint preview generation failed for {pptx.name}")
        shutil.move(str(converted), preview)
    return preview


def _discover_artifacts(pptx_files: list[Path]) -> list[str]:
    artifacts: list[Path] = []
    for pptx in pptx_files:
        artifacts.extend((pptx, _ensure_pptx_preview(pptx)))
    return [_virtual_output_path(path) for path in sorted(set(artifacts))]


class AgentTask:
    def __init__(
        self,
        task_id: str,
        thread_id: str,
        prompt: str,
        resume: bool,
        delivery_contract: dict[str, Any],
        task_dir: Path,
        env: dict[str, str],
    ):
        self.task_id = task_id
        self.thread_id = thread_id
        self.prompt = prompt
        self.resume = resume
        self.delivery_contract = _normalize_delivery_contract(delivery_contract)
        self.output_baseline = _output_fingerprints()
        self.task_dir = task_dir
        self.state_path = task_dir / "state.json"
        self.events_path = task_dir / "events.jsonl"
        self.thread_state_path = HOST_STATE_ROOT / "thread.json"
        self.env = env
        self.secret_values = _secret_values(env)
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.cancel_requested = False
        self.recent_stderr: list[str] = []
        self.recent_codex_errors: list[str] = []
        self.state: dict[str, Any] = {
            "task_id": task_id,
            "thread_id": thread_id,
            "status": "pending",
            "session_id": None,
            "final_message": "",
            "artifacts": [],
            "error": "",
            "usage": {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
                "total_tokens": 0,
                "llm_call_count": 0,
            },
            "usage_records": [],
            "last_seq": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    def persist(self) -> None:
        self.state["updated_at"] = time.time()
        _atomic_json(self.state_path, self.state)

    def set_state(self, **updates: Any) -> None:
        with self.lock:
            self.state.update(updates)
            self.persist()

    def append_event(self, kind: str, payload: object) -> None:
        with self.lock:
            self.state["last_seq"] = int(self.state.get("last_seq") or 0) + 1
            envelope = {
                "seq": self.state["last_seq"],
                "kind": kind,
                "payload": _sanitize_payload(payload, self.secret_values),
                "created_at": time.time(),
            }
            self.task_dir.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self.persist()

    def record_codex_usage(self, event: dict[str, Any], *, model_name: str) -> None:
        with self.lock:
            records = self.state.get("usage_records")
            if not isinstance(records, list):
                records = []
                self.state["usage_records"] = records
            record = codex_turn_usage_record(
                event,
                task_id=self.task_id,
                turn_index=len(records) + 1,
                model_name=model_name,
            )
            if record is None:
                return
            records.append(record)
            summary = self.state["usage"]
            summary["input_tokens"] += int(record["input_tokens"])
            summary["cached_input_tokens"] += int(record["cache_read_tokens"])
            summary["output_tokens"] += int(record["output_tokens"])
            summary["reasoning_output_tokens"] += int(record["reasoning_output_tokens"])
            summary["total_tokens"] += int(record["total_tokens"])
            summary["llm_call_count"] += 1
            self.persist()

    def record_codex_failure(self, event: dict[str, Any]) -> None:
        detail = codex_failure_detail(event)
        if not detail:
            return
        detail = _redact_text(detail, self.secret_values)
        with self.lock:
            self.recent_codex_errors.append(detail)
            del self.recent_codex_errors[:-20]

    def snapshot(self, after_seq: int = 0) -> dict[str, Any]:
        with self.lock:
            payload = dict(self.state)
            events: list[dict[str, Any]] = []
            if self.events_path.is_file():
                for line in self.events_path.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and isinstance(event.get("seq"), int) and event["seq"] > after_seq:
                        events.append(event)
            payload["events"] = events
            return payload


class CodexTaskManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tasks: dict[str, AgentTask] = {}
        self.active_task_id: str | None = None
        ensure_user_data_directories()
        HOST_STATE_ROOT.mkdir(parents=True, exist_ok=True)

    def start(
        self,
        *,
        task_id: str,
        thread_id: str,
        prompt: str,
        resume: bool,
        delivery_contract: dict[str, Any] | None = None,
    ) -> AgentTask:
        task_id = validate_id(task_id)
        thread_id = validate_id(thread_id)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if len(prompt.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("prompt is too large")
        with self.lock:
            existing = self.tasks.get(task_id)
            if existing is not None:
                return existing
            if self.active_task_id:
                active = self.tasks.get(self.active_task_id)
                if active and active.state.get("status") not in _TERMINAL_STATUSES:
                    raise RuntimeError(f"another Agent turn is already running: {self.active_task_id}")
            task = AgentTask(
                task_id,
                thread_id,
                prompt,
                bool(resume),
                _normalize_delivery_contract(delivery_contract),
                HOST_STATE_ROOT / "tasks" / task_id,
                dict(os.environ),
            )
            task.task_dir.mkdir(parents=True, exist_ok=True)
            task.persist()
            self.tasks[task_id] = task
            self.active_task_id = task_id
            threading.Thread(target=self._run, args=(task,), name=f"ppt-agent-{task_id}", daemon=True).start()
            return task

    def get(self, task_id: str) -> AgentTask | None:
        return self.tasks.get(validate_id(task_id))

    def cancel(self, task_id: str) -> AgentTask:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)
        with task.lock:
            task.cancel_requested = True
            process = task.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    process.terminate()
        return task

    def _session_for(self, task: AgentTask) -> str | None:
        if not task.resume:
            return None
        if not task.thread_state_path.is_file():
            raise RuntimeError("no persisted Agent session exists for this continuation")
        payload = json.loads(task.thread_state_path.read_text(encoding="utf-8"))
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("the persisted Agent session pointer is missing")
        return session_id

    def _persist_session(self, task: AgentTask, session_id: str) -> None:
        task.state["session_id"] = session_id
        _atomic_json(
            task.thread_state_path,
            {"thread_id": task.thread_id, "session_id": session_id, "updated_at": time.time()},
        )
        task.persist()

    def _consume_stderr(self, task: AgentTask, stream) -> None:
        for line in stream:
            text = _redact_text(line.rstrip(), task.secret_values)
            if text:
                with task.lock:
                    task.recent_stderr.append(text)
                    del task.recent_stderr[:-20]
                task.append_event("stderr", {"text": text})

    def _run(self, task: AgentTask) -> None:
        try:
            task.set_state(status="preparing")
            ensure_user_data_directories()
            validate_ppt_master_source_contract(SOURCE_SKILL_ROOT)
            prepare_writable_skill_snapshot(SOURCE_SKILL_ROOT, RUNTIME_ROOT)
            _prepare_codex_home(task.env)
            provider_metadata = validate_codex_provider_contract(task.env, CODEX_HOME)
            if not has_codex_credentials(task.env, CODEX_HOME):
                raise RuntimeError("Codex Agent credentials/configuration are missing; configure a Responses-capable provider or mount Codex auth")
            guard = subprocess.run(
                ["python3", str(RUNTIME_ROOT / "scripts" / "attribution_guard.py")],
                cwd=RUNTIME_ROOT,
                env={**_agent_subprocess_env(task.env), "CODEX_HOME": str(CODEX_HOME)},
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
            if guard.returncode != 0:
                raise RuntimeError("upstream ppt-master attribution guard failed")

            session_id = self._session_for(task)
            prompt = task.prompt
            final_message = ""
            for repair_attempt in range(MAX_DELIVERY_REPAIR_ATTEMPTS + 1):
                restored = ensure_user_data_directories()
                if restored:
                    # Recreate runtime material as well: the workspace may have
                    # disappeared between Agent turns, taking the writable Skill
                    # snapshot and Codex home with it.
                    prepare_writable_skill_snapshot(SOURCE_SKILL_ROOT, RUNTIME_ROOT)
                    _prepare_codex_home(task.env)
                    task.append_event(
                        "host",
                        {
                            "message": "Restored missing persistent Sandbox directories before Agent launch",
                            "paths": restored,
                        },
                    )
                command = build_codex_command(session_id=session_id)
                task.append_event(
                    "host",
                    {
                        "message": "Agent host ready",
                        "resumed": bool(session_id),
                        "delivery_repair_attempt": repair_attempt,
                        "model": provider_metadata.get("model", ""),
                        "provider": provider_metadata.get("provider", ""),
                        "base_url": provider_metadata.get("base_url", ""),
                        "wire_api": provider_metadata.get("wire_api", ""),
                    },
                )
                task.set_state(status="running", session_id=session_id)
                process = subprocess.Popen(
                    command,
                    cwd=WORKSPACE_ROOT,
                    env={**_agent_subprocess_env(task.env), "CODEX_HOME": str(CODEX_HOME)},
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
                task.process = process
                assert process.stdin and process.stdout and process.stderr
                stderr_thread = threading.Thread(
                    target=self._consume_stderr,
                    args=(task, process.stderr),
                    name=f"ppt-agent-stderr-{task.task_id}",
                    daemon=True,
                )
                stderr_thread.start()
                process.stdin.write(prompt)
                process.stdin.close()

                for line in process.stdout:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        task.append_event("stdout", {"text": _redact_text(raw, task.secret_values)})
                        continue
                    if not isinstance(event, dict):
                        continue
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") == "reasoning":
                        continue
                    if event.get("type") == "thread.started":
                        new_session_id = event.get("thread_id")
                        if isinstance(new_session_id, str) and new_session_id:
                            self._persist_session(task, new_session_id)
                            session_id = new_session_id
                    task.record_codex_usage(
                        event,
                        model_name=provider_metadata.get("model", ""),
                    )
                    task.record_codex_failure(event)
                    if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            final_message = text.strip()
                    task.append_event("codex", event)

                return_code = process.wait()
                stderr_thread.join(timeout=2)
                if task.cancel_requested:
                    task.set_state(status="cancelled", final_message=final_message, error="cancelled by user")
                    return
                if return_code != 0:
                    with task.lock:
                        stderr_detail = "\n".join(task.recent_stderr[-6:]).strip()
                        event_detail = "\n".join(task.recent_codex_errors[-4:]).strip()
                    details = [
                        value
                        for value in (
                            f"stderr: {stderr_detail}" if stderr_detail else "",
                            f"codex: {event_detail}" if event_detail else "",
                        )
                        if value
                    ]
                    detail = "\n".join(details) or "Codex emitted no diagnostic text before exit"
                    task.append_event(
                        "host",
                        {
                            "message": "Codex Agent process failed",
                            "return_code": return_code,
                            "detail": detail,
                        },
                    )
                    raise RuntimeError(
                        f"Codex Agent process exited with code {return_code}: {detail}"
                    )

                candidates = _candidate_pptx_files(task.output_baseline)
                if not candidates:
                    # A normal upstream confirmation boundary has no final
                    # PPTX yet. Return control to DeerFlow so the real user can
                    # operate Confirm UI or continue in chat.
                    task.set_state(status="completed", final_message=final_message, artifacts=[], error="")
                    return
                issues = validate_delivery_contract(candidates[0], task.delivery_contract)
                if not issues:
                    artifacts = _discover_artifacts(candidates)
                    task.set_state(status="completed", final_message=final_message, artifacts=artifacts, error="")
                    return
                task.append_event(
                    "host",
                    {
                        "message": "Final PPTX rejected by deterministic delivery acceptance",
                        "issues": issues,
                        "repair_attempt": repair_attempt + 1,
                    },
                )
                if repair_attempt >= MAX_DELIVERY_REPAIR_ATTEMPTS:
                    raise RuntimeError("final PPTX still violates the explicit delivery contract: " + "; ".join(issues))
                session_id = str(task.state.get("session_id") or session_id or "") or None
                prompt = (
                    "The final PPTX was rejected by the deterministic host acceptance boundary. "
                    "Do not report completion. Reopen the installed ppt-master Skill and the exact upstream references "
                    "for each failed capability, repair the same project, rerun the owning official commands without "
                    "pipelines or output truncation, export again, and overwrite the final file in /mnt/user-data/outputs.\n"
                    "Acceptance failures:\n- " + "\n- ".join(issues)
                )
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            task.set_state(status="failed", error=_redact_text(str(exc), task.secret_values))
        finally:
            task.process = None
            with self.lock:
                if self.active_task_id == task.task_id:
                    self.active_task_id = None


class AgentHostHandler(BaseHTTPRequestHandler):
    manager: CodexTaskManager
    api_key: str
    provider_metadata: dict[str, str] = {}

    server_version = "ppt-master-agent-host/1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"ppt-master-agent-host: {fmt % args}", flush=True)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.api_key}"
        return bool(self.api_key) and hmac.compare_digest(supplied, expected)

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"detail": "unauthorized"})
        return False

    def _read_payload(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok", "provider": self.provider_metadata})
            return
        match = re.fullmatch(r"/v1/tasks/([A-Za-z0-9_-]{1,128})", parsed.path)
        if not match:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        if not self._require_auth():
            return
        task = self.manager.get(match.group(1))
        if task is None:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "task not found"})
            return
        query = parse_qs(parsed.query)
        try:
            after_seq = max(0, int((query.get("after_seq") or ["0"])[0]))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": "after_seq must be an integer"})
            return
        self._json(HTTPStatus.OK, task.snapshot(after_seq))

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not self._require_auth():
            return
        try:
            if parsed.path == "/v1/tasks":
                payload = self._read_payload()
                task = self.manager.start(
                    task_id=payload.get("task_id"),
                    thread_id=payload.get("thread_id"),
                    prompt=payload.get("prompt"),
                    resume=bool(payload.get("resume", False)),
                    delivery_contract=payload.get("delivery_contract"),
                )
                self._json(HTTPStatus.ACCEPTED, task.snapshot())
                return
            match = re.fullmatch(r"/v1/tasks/([A-Za-z0-9_-]{1,128})/cancel", parsed.path)
            if match:
                task = self.manager.cancel(match.group(1))
                self._json(HTTPStatus.ACCEPTED, task.snapshot())
                return
            self._json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
        except KeyError:
            self._json(HTTPStatus.NOT_FOUND, {"detail": "task not found"})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
        except RuntimeError as exc:
            self._json(HTTPStatus.CONFLICT, {"detail": str(exc)})


def main() -> None:
    api_key = os.environ.get("PPT_MASTER_AGENT_HOST_KEY") or os.environ.get("SANDBOX_API_KEY", "")
    if not api_key:
        raise SystemExit("PPT_MASTER_AGENT_HOST_KEY or SANDBOX_API_KEY is required")
    provider_metadata = {
        **validate_codex_provider_contract(dict(os.environ), CODEX_HOME),
        **validate_ppt_master_source_contract(SOURCE_SKILL_ROOT),
    }
    AgentHostHandler.manager = CodexTaskManager()
    AgentHostHandler.api_key = api_key
    AgentHostHandler.provider_metadata = provider_metadata
    server = ThreadingHTTPServer(("0.0.0.0", AGENT_HOST_PORT), AgentHostHandler)
    print(f"ppt-master-agent-host listening on 0.0.0.0:{AGENT_HOST_PORT}", flush=True)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
