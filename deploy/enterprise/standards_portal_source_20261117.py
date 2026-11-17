"""Qualify six added portal runtime sources and their module development guide.

The parent source inventories and deployment receipts remain immutable. This
registration describes an undeployed frontend candidate, not operator acceptance.
"""
import hashlib
import json

ARCHIVE = "release/standards-portal-source-20261117/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "4a3935a6724f8194ddc74dc300e5d05bd5ce648d8499c708214365ebeb065026"
BASE_COMMIT = "c0c6dfa547a1963677db974a98f3dbc47b9c130e"
GUIDE = "frontend/src/app/(standards-portal)/AGENTS.md"
RUNTIME = frozenset({
    "frontend/src/app/(standards-portal)/workspace/standards/layout.tsx",
    "frontend/src/app/(standards-portal)/workspace/standards/page.tsx",
    "frontend/src/app/(standards-portal)/workspace/standards/portal-entry.tsx",
    "frontend/src/app/(standards-portal)/workspace/standards/standards.module.css",
    "frontend/src/core/i18n/standards-portal.ts",
    "frontend/src/core/standards-portal/config.ts",
})
FILES = RUNTIME | {GUIDE}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    """Use repository LF text bytes, preserving all other UTF-8 content."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def qualify(read):
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        for path in sorted(FILES):
            try:
                read(path)
            except (FileNotFoundError, KeyError):
                continue
            raise ValueError("STANDARDS_PORTAL_MANIFEST_REQUIRED")
        return None
    require(digest(raw) == MANIFEST_SHA256, "STANDARDS_PORTAL_SEAL_CHANGED")
    manifest = json.loads(raw)
    require(set(manifest) == {
        "schema", "status", "base_commit", "production_acceptance_claimed",
        "runtime_modified_by_registration", "source_files",
    }, "STANDARDS_PORTAL_SCHEMA")
    require(manifest["schema"] == "standards-portal-source.v1", "STANDARDS_PORTAL_SCHEMA")
    require(manifest["status"] == "NOT_DEPLOYED", "STANDARDS_PORTAL_STATUS")
    require(manifest["base_commit"] == BASE_COMMIT, "STANDARDS_PORTAL_PARENT")
    require(manifest["production_acceptance_claimed"] is False
            and manifest["runtime_modified_by_registration"] is False,
            "STANDARDS_PORTAL_OVERCLAIM")
    require(set(manifest["source_files"]) == FILES, "STANDARDS_PORTAL_SCOPE")
    for path, row in manifest["source_files"].items():
        require(set(row) == {"kind", "sha256"}, "STANDARDS_PORTAL_ROW_SCHEMA")
        require(row["kind"] == ("runtime-source" if path in RUNTIME else "development-guide"),
                "STANDARDS_PORTAL_KIND")
        try:
            current = read(path)
        except (FileNotFoundError, KeyError):
            raise ValueError((path, "STANDARDS_PORTAL_FILE_MISSING")) from None
        current.decode("utf-8")
        require(digest(current) == row["sha256"], (path, "STANDARDS_PORTAL_SOURCE_CHANGED"))
    return manifest


def summary(manifest):
    if manifest is None:
        return None
    return {
        "status": "NOT_DEPLOYED", "base_commit": BASE_COMMIT,
        # The baseline's full src/public inventory also contains module guides.
        "frontend_runtime_paths": sorted(FILES),
        "runtime_sources_verified": len(RUNTIME),
        "development_guides_verified": 1,
        "production_acceptance_claimed": False,
        "historical_inventories_modified": False,
    }
