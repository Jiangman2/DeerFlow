"""Tests for the shared host→virtual output-mask pattern (``sandbox/path_patterns.py``).

The rule these pin is shared by the stable-root regex path and the dynamic-root
scanner. Both accept Windows and POSIX separator spellings and must stop at
colon-joined path-list boundaries.

The move itself was cleared by a differential against the *real* pre-extraction
expressions, run once on the parent commit. That run cannot be committed: after
this lands there is no old inline expression left to diff against, only the
frozen copies below. So the committed guard is the weaker snapshot, and its
red-ness rests on those literals — not on the length of ``_BASES``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from deerflow.sandbox import path_patterns as path_patterns_module
from deerflow.sandbox.local import local_sandbox as local_sandbox_module
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.sandbox.path_patterns import build_output_mask_pattern, normalize_mask_tail
from deerflow.sandbox.tools import _compiled_mask_patterns


def _legacy_tools_pattern(base: str) -> re.Pattern[str]:
    """The expression ``_compiled_mask_patterns`` inlined before the extraction."""
    escaped = re.escape(base).replace(r"\\", r"[/\\]")
    return re.compile(escaped + r"(?=/|$|[^\w./-])" + r"(?:[/\\][^\s\"';&|<>()]*)?")


def _legacy_local_pattern(base: str) -> re.Pattern[str]:
    """The expression ``_reverse_output_patterns`` inlined before the extraction."""
    return re.compile(re.escape(base) + r"(?=/|$|[^\w./-])" + r"(?:[/\\][^\s\"';&|<>()]*)?")


_LEGACY_TAIL = r"(?:[/\\][^\s\"';&|<>()]*)?"
_CURRENT_TAIL = r"(?:[/\\][^\s\"';&|<>():]*)?"


def _with_current_tail(legacy: str) -> str:
    assert legacy.endswith(_LEGACY_TAIL)
    return legacy.removesuffix(_LEGACY_TAIL) + _CURRENT_TAIL


_BASES = [
    "/host/skills",
    "/host/dir with spaces",
    "/host/re+meta(chars)[x]",
    "/host/dots.in.name",
    "/Users/a/.deer-flow/users/u1/threads/t1/user-data",
    "C:\\host\\skills",
    "/host/技能",
    # Drive root: the only base either caller can hand the helper that still ends in a
    # separator (``Path.resolve()`` strips them everywhere else), so it is the one shape
    # that goes red if the helper starts normalizing the base it is given.
    "C:\\",
]


@pytest.mark.parametrize("base", _BASES)
def test_helper_reproduces_the_pre_extraction_expressions(base: str) -> None:
    """Byte-identical to what each call site built inline, for both separator modes.

    This is the anchor for the move itself: edit the helper in a way that changes
    either site's regex and this goes red.
    """
    assert build_output_mask_pattern(base, separator_agnostic=True).pattern == _with_current_tail(_legacy_tools_pattern(base).pattern)
    assert build_output_mask_pattern(base).pattern == _with_current_tail(_legacy_local_pattern(base).pattern)


def test_separator_agnostic_mode_accepts_forward_spelled_windows_paths() -> None:
    windows_base = "C:\\host\\skills"
    posix_spelling = "C:/host/skills/file.md"

    assert build_output_mask_pattern(windows_base, separator_agnostic=True).search(posix_spelling)
    assert build_output_mask_pattern(windows_base).search(posix_spelling) is None

    # On a base with no separator ambiguity the two modes agree exactly.
    posix_base = "/host/skills"
    assert build_output_mask_pattern(posix_base, separator_agnostic=True).pattern == build_output_mask_pattern(posix_base).pattern


def test_boundary_still_rejects_prefix_siblings_and_accepts_real_segments() -> None:
    """The #4035/#4053 rule itself, now asserted once against the shared helper."""
    pattern = build_output_mask_pattern("/host/skills")

    # Matches: the root itself, a child, a Windows-separated child, and a root
    # followed by text punctuation (``$`` and the ``[^\w./-]`` class).
    assert pattern.fullmatch("/host/skills")
    assert pattern.match("/host/skills/a/b.md")
    assert pattern.match("/host/skills\\a\\b.md")
    assert pattern.search("paths: /host/skills, and more")

    # Does not match inside a sibling that merely shares the prefix.
    assert pattern.search("/host/skills-extra/file.md") is None
    assert pattern.search("/host/skills.bak") is None
    assert pattern.search("/host/skills2/file.md") is None


def test_direct_replacer_matches_shared_boundary_and_tail_contract() -> None:
    replacer = path_patterns_module.replace_output_path_matches

    assert replacer("see /host/skills/a.md", "/host/skills", "/mnt/skills", separator_agnostic=True) == "see /mnt/skills/a.md"
    assert replacer("see \\host\\skills\\a.md", "/host/skills", "/mnt/skills", separator_agnostic=True) == "see /mnt/skills/a.md"
    assert replacer("see /host/skills-extra/a.md", "/host/skills", "/mnt/skills", separator_agnostic=True) == "see /host/skills-extra/a.md"


def test_direct_replacer_normalizes_windows_tail_to_virtual_posix_style() -> None:
    assert (
        path_patterns_module.replace_output_path_matches(
            "see C:\\host\\skills\\pkg\\a.md",
            "C:\\host\\skills",
            "/mnt/skills",
            separator_agnostic=True,
        )
        == "see /mnt/skills/pkg/a.md"
    )


def test_local_sandbox_reverse_mask_routes_through_direct_helper(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "skills"
    local.mkdir()
    sandbox = LocalSandbox(
        id="local",
        path_mappings=[PathMapping(container_path="/mnt/skills", local_path=str(local), read_only=True)],
    )

    resolved = str(Path(local).resolve())
    calls: list[tuple[str, str, dict]] = []
    original = path_patterns_module.replace_output_path_matches

    def recording_replacer(output, base, replacement, **kwargs):
        calls.append((output, base, kwargs))
        return original(output, base, replacement, **kwargs)

    monkeypatch.setattr(local_sandbox_module, "replace_output_path_matches", recording_replacer)

    assert sandbox._reverse_resolve_paths_in_output(f"read {resolved}/SKILL.md") == "read /mnt/skills/SKILL.md"
    assert calls == [(f"read {resolved}/SKILL.md", resolved, {"separator_agnostic": True})]


def _regex_mask(output: str, base: str, virtual: str) -> str:
    def replace(match: re.Match[str]) -> str:
        relative = normalize_mask_tail(match.group(0)[len(base) :])
        return f"{virtual}/{relative}" if relative else virtual

    return build_output_mask_pattern(base, separator_agnostic=True).sub(replace, output)


def _scanner_mask(output: str, base: str, virtual: str) -> str:
    return path_patterns_module.replace_output_path_matches(
        output,
        base,
        virtual,
        separator_agnostic=True,
    )


@pytest.mark.parametrize("mask", [_regex_mask, _scanner_mask])
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (
            "PATH=/host/ws/.venv/bin:/host/ws/node_modules/.bin:/host/ws/bin:/usr/bin",
            "PATH=/mnt/ws/.venv/bin:/mnt/ws/node_modules/.bin:/mnt/ws/bin:/usr/bin",
        ),
        ("PYTHONPATH=/host/ws/a:/host/ws/b", "PYTHONPATH=/mnt/ws/a:/mnt/ws/b"),
    ],
)
def test_colon_joined_path_list_masks_every_entry(mask, output: str, expected: str) -> None:
    assert mask(output, "/host/ws", "/mnt/ws") == expected


def test_local_sandbox_masks_every_colon_joined_host_path(tmp_path: Path) -> None:
    local = tmp_path / "workspace"
    local.mkdir()
    sandbox = LocalSandbox(
        id="local",
        path_mappings=[
            PathMapping(
                container_path="/mnt/user-data/workspace",
                local_path=str(local),
            )
        ],
    )
    resolved = str(local.resolve())

    output = sandbox._reverse_resolve_paths_in_output(f"{resolved}/.venv/bin:{resolved}/bin")

    assert output == ("/mnt/user-data/workspace/.venv/bin:/mnt/user-data/workspace/bin")


def test_tools_mask_patterns_route_through_the_helper(tmp_path: Path) -> None:
    """Same wiring check for the other copy — and it must stay separator-agnostic."""
    host = tmp_path / "skills"
    host.mkdir()

    compiled = _compiled_mask_patterns(((str(host), "/mnt/skills"),))

    assert compiled
    for pattern, variant, virtual_base in compiled:
        assert virtual_base == "/mnt/skills"
        assert pattern.pattern == build_output_mask_pattern(variant, separator_agnostic=True).pattern
