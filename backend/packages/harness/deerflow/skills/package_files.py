"""Shared classification of skill-package files by name and leading bytes.

The installer and SkillScan must agree on which files are code and which bytes
mark an executable, so both rules live only here.
"""

from __future__ import annotations

from pathlib import PurePath, PurePosixPath

CODE_SUFFIXES = frozenset({".bash", ".cjs", ".js", ".mjs", ".php", ".pl", ".ps1", ".py", ".rb", ".sh", ".ts", ".zsh"})
_EXECUTABLE_MAGIC_PREFIXES = (
    b"\x7fELF",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
)


def _posix(path: str | PurePath) -> PurePosixPath:
    return PurePosixPath(str(path).replace("\\", "/"))


def is_code_path(path: str | PurePath) -> bool:
    """Return whether a package-relative path is code by name."""
    posix = _posix(path)
    return (bool(posix.parts) and posix.parts[0] == "scripts") or posix.suffix.lower() in CODE_SUFFIXES


def is_code_file(path: str | PurePath, head: bytes) -> bool:
    """Return whether a package file is code by path or extensionless shebang."""
    return is_code_path(path) or (not _posix(path).suffix and head.startswith(b"#!"))


def is_executable_binary_prefix(prefix: bytes) -> bool:
    """Detect ELF, PE, and Mach-O executables by magic bytes."""
    return prefix.startswith(_EXECUTABLE_MAGIC_PREFIXES)
