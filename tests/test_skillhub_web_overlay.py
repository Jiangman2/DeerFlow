import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_skillhub_web_overlay_repairs_subpath_assets_without_weakening_headers():
    dockerfile = (ROOT / "k8s/skillhub/Dockerfile.web-overlay").read_text(encoding="utf-8")
    patch = (ROOT / "k8s/skillhub/web-overlay/patch-subpath-assets.sh").read_text(encoding="utf-8")

    assert "ARG BASE_IMAGE" in dockerfile
    assert "patch-subpath-assets.sh" in dockerfile
    assert "frame-ancestors 'none'" in patch
    assert "SKILLHUB_WEB_BASE_PATH:-/platform/skillhub" in patch
    assert "url('./" in patch
    assert "Content-Security-Policy" not in dockerfile


def test_skillhub_web_overlay_rewrites_realistic_static_tree(tmp_path):
    root = tmp_path / "html"
    fonts = root / "fonts"
    fonts.mkdir(parents=True)
    (root / "index.html").write_text(
        '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; frame-ancestors \'none\';"><link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/fonts/fonts.css">',
        encoding="utf-8",
    )
    (fonts / "fonts.css").write_text(
        "a{src:url('/fonts/inter-latin-400-normal.woff2')}b{src:url(\"/fonts/inter-latin-500-normal.woff2\")}",
        encoding="utf-8",
    )
    for name in (
        "inter-latin-400-normal.woff2",
        "inter-latin-500-normal.woff2",
        "inter-latin-600-normal.woff2",
        "inter-latin-700-normal.woff2",
        "jetbrains-mono-latin-400-normal.woff2",
        "jetbrains-mono-latin-500-normal.woff2",
    ):
        (fonts / name).write_bytes(b"font")

    shell = shutil.which("sh")
    if os.name == "nt":
        git_shell = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/sh.exe"
        if git_shell.is_file():
            shell = str(git_shell)
    if shell is None:
        pytest.skip("POSIX shell is unavailable")

    subprocess.run(
        [shell, str(ROOT / "k8s/skillhub/web-overlay/patch-subpath-assets.sh"), str(root)],
        check=True,
    )

    index = (root / "index.html").read_text(encoding="utf-8")
    stylesheet = (fonts / "fonts.css").read_text(encoding="utf-8")
    assert "frame-ancestors" not in index
    assert 'href="/platform/skillhub/favicon.svg"' in index
    assert 'href="/platform/skillhub/fonts/fonts.css"' in index
    assert "url('./inter-latin-400-normal.woff2')" in stylesheet
    assert 'url("./inter-latin-500-normal.woff2")' in stylesheet


@pytest.mark.parametrize(
    ("favicon_href", "fonts_href"),
    (
        ("/__SKILLHUB_WEB_BASE_PATH__/favicon.svg", "/__SKILLHUB_WEB_BASE_PATH__/fonts/fonts.css"),
        ("/platform/skillhub/favicon.svg", "/platform/skillhub/fonts/fonts.css"),
    ),
)
def test_skillhub_web_overlay_accepts_build_placeholder_and_final_subpath(tmp_path, favicon_href, fonts_href):
    root = tmp_path / "html"
    fonts = root / "fonts"
    fonts.mkdir(parents=True)
    (root / "index.html").write_text(
        f'<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; frame-ancestors \'none\';"><link rel="icon" href="{favicon_href}"><link rel="stylesheet" href="{fonts_href}">',
        encoding="utf-8",
    )
    (fonts / "fonts.css").write_text(
        "a{src:url('/fonts/inter-latin-400-normal.woff2')}",
        encoding="utf-8",
    )
    for name in (
        "inter-latin-400-normal.woff2",
        "inter-latin-500-normal.woff2",
        "inter-latin-600-normal.woff2",
        "inter-latin-700-normal.woff2",
        "jetbrains-mono-latin-400-normal.woff2",
        "jetbrains-mono-latin-500-normal.woff2",
    ):
        (fonts / name).write_bytes(b"font")

    shell = shutil.which("sh")
    if os.name == "nt":
        git_shell = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/sh.exe"
        if git_shell.is_file():
            shell = str(git_shell)
    if shell is None:
        pytest.skip("POSIX shell is unavailable")

    subprocess.run(
        [shell, str(ROOT / "k8s/skillhub/web-overlay/patch-subpath-assets.sh"), str(root)],
        check=True,
    )

    index = (root / "index.html").read_text(encoding="utf-8")
    assert "frame-ancestors" not in index
    assert 'href="/platform/skillhub/favicon.svg"' in index
    assert 'href="/platform/skillhub/fonts/fonts.css"' in index


def test_skillhub_native_rbac_tool_uses_native_tables_and_allowlisted_roles():
    script = (ROOT / "k8s/skillhub/manage-native-rbac.sh").read_text(encoding="utf-8")

    for table in ("identity_binding", "user_account", "user_role_binding", "role", "audit_log"):
        assert table in script
    for role in ("SUPER_ADMIN", "SKILL_ADMIN", "USER_ADMIN", "AUDITOR"):
        assert role in script
    assert "ON CONFLICT (user_id, role_id) DO NOTHING" in script
    assert "deerflow-session" in script
