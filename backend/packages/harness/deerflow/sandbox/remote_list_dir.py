"""Remote ``list_dir`` command and stdout contract.

Remote providers list with ``find ... | head`` under ``sh -lc``. POSIX
``sh`` reports the status of ``head``, so a missing ``find`` binary or an
incomplete traversal can otherwise look like a valid empty directory.
"""

from __future__ import annotations

import shlex

_STATUS_PREFIX = "__DF_FIND_STATUS__:"
_MISSING_ROOT = "missing"
_LIST_LIMIT = 500
_FIND_OK = (0, 141)


def remote_list_dir_command(path: str, max_depth: int, *, limit: int = _LIST_LIMIT) -> str:
    """Build a bounded POSIX listing that preserves ``find``'s own status."""
    quoted = shlex.quote(path)
    depth = int(max_depth)
    result_limit = int(limit)
    return (
        f"set +e; if [ ! -e {quoted} ]; then printf '%s\\n' "
        f"{_STATUS_PREFIX}{_MISSING_ROOT}; exit 1; fi; "
        f"_st=/tmp/df_find_$$; "
        f"{{ find -H {quoted} -maxdepth {depth} \\( -type f -o -type d \\) "
        f'2>/dev/null; echo $? > "$_st"; }} | head -n {result_limit}; '
        f'st=$(cat "$_st" 2>/dev/null); '
        f"printf '\\n%s\\n' {_STATUS_PREFIX}$st; "
        f'rm -f "$_st"; exit "${{st:-126}}"'
    )


def parse_remote_list_dir_output(
    stdout: str | None,
    resolved: str,
    *,
    pipeline_exit_code: int | None = None,
) -> list[str]:
    """Parse a remote listing or raise when its result is not trustworthy."""
    # ``find`` delimits records with newline only. ``splitlines`` would also
    # split legal Linux filename characters, and stripping would corrupt
    # trailing whitespace in a filename.
    lines = (stdout or "").split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    find_status: int | None = None
    if lines and lines[-1].startswith(_STATUS_PREFIX):
        raw_status = lines.pop()[len(_STATUS_PREFIX) :]
        if raw_status == _MISSING_ROOT:
            raise FileNotFoundError(resolved)
        try:
            find_status = int(raw_status)
        except ValueError:
            find_status = None
        if lines and lines[-1] == "":
            lines.pop()

    if find_status is None:
        if pipeline_exit_code is not None and pipeline_exit_code not in (*_FIND_OK, 1):
            raise OSError(f"Failed to list_dir {resolved}: command exited with code {pipeline_exit_code}")
        raise OSError(f"Failed to list_dir {resolved}: find status marker missing")

    entries = [line for line in lines if line]
    if find_status == 1:
        raise OSError(f"Failed to list_dir {resolved}: find exited with code 1, usually because some files or directories could not be read; results would be incomplete, so list a narrower path")
    if find_status not in _FIND_OK:
        raise OSError(f"Failed to list_dir {resolved}: command exited with code {find_status}")
    if not entries:
        raise FileNotFoundError(resolved)
    return entries
