from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STACK = REPO_ROOT / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml"


def test_gateway_uses_persistent_writable_extensions_config() -> None:
    manifest = STACK.read_text(encoding="utf-8")

    assert "name: seed-extensions-config" in manifest
    assert "value: /app/backend/.deer-flow/runtime-config/extensions_config.json" in manifest
    assert 'target_path = Path("/runtime-home/runtime-config/extensions_config.json")' in manifest
    assert "mountPath: /runtime-home" in manifest
    assert "subPath: deer-flow" in manifest


def test_extensions_seed_merge_preserves_runtime_admin_choices() -> None:
    manifest = STACK.read_text(encoding="utf-8")

    assert "merged.update(current)" in manifest
    assert 'for key in ("mcpServers", "skills")' in manifest
    assert "entries.update(current.get(key) or {})" in manifest
    assert "os.chmod(temporary_path, 0o600)" in manifest
    assert "temporary_path.replace(target_path)" in manifest


def test_release_seed_remains_read_only() -> None:
    manifest = STACK.read_text(encoding="utf-8")

    assert "mountPath: /seed/extensions_config.json" in manifest
    assert "mountPath: /app/backend/extensions_config.json" in manifest
    assert manifest.count("readOnly: true") >= 3
