"""Remote grep/glob command wrapper and bounded-output contract."""

from __future__ import annotations

import shlex
from typing import Literal, NamedTuple

SearchTool = Literal["grep", "find"]

_STATUS_PREFIX = "__DF_SEARCH_STATUS__:"
_MISSING_ROOT = "missing"
_SIGPIPE = 141
_OK_STATUSES: dict[str, tuple[int, ...]] = {
    "grep": (0, 1, _SIGPIPE),
    "find": (0, _SIGPIPE),
}
_READ_ERROR_STATUS: dict[str, int] = {"grep": 2, "find": 1}


class RemoteSearchOutput(NamedTuple):
    """Bounded search text and whether an additional raw line was dropped."""

    text: str
    truncated: bool


def remote_search_command(search: str, root: str, *, limit: int) -> str:
    """Wrap a search so its own status survives the output-cap pipeline."""
    quoted_root = shlex.quote(root)
    # One extra raw line is the truncation signal; the parser drops it.
    output_limit = int(limit) + 1
    return (
        f"set +e; if [ ! -e {quoted_root} ]; then printf '%s\\n' "
        f"{_STATUS_PREFIX}{_MISSING_ROOT}; exit 0; fi; "
        f'_st=/tmp/df_search_$$; {{ {search}; echo $? > "$_st"; }} | '
        f"head -n {output_limit}; "
        f'st=$(cat "$_st" 2>/dev/null); rm -f "$_st"; '
        f"printf '\\n%s\\n' {_STATUS_PREFIX}\"$st\"; exit 0"
    )


def parse_remote_search_output(
    stdout: str | None,
    root: str,
    *,
    tool: SearchTool,
    limit: int,
) -> RemoteSearchOutput:
    """Return bounded output or reject a missing/incomplete remote search."""
    lines = (stdout or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or not lines[-1].startswith(_STATUS_PREFIX):
        raise OSError(f"Failed to {tool} under {root}: search status marker missing")

    raw_status = lines.pop()[len(_STATUS_PREFIX) :]
    if raw_status == _MISSING_ROOT:
        raise FileNotFoundError(root)
    if lines and lines[-1] == "":
        lines.pop()

    try:
        status = int(raw_status)
    except ValueError:
        raise OSError(f"Failed to {tool} under {root}: search status unavailable") from None

    if status in _OK_STATUSES[tool]:
        return RemoteSearchOutput("\n".join(lines[:limit]), len(lines) > limit)
    if status == _READ_ERROR_STATUS[tool]:
        raise OSError(f"Failed to {tool} under {root}: {tool} exited with code {status}, usually because some files or directories could not be read; results would be incomplete, so search a narrower path")
    raise OSError(f"Failed to {tool} under {root}: command exited with code {status}")
