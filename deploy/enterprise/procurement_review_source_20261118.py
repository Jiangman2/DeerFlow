"""Verify the finite procurement source delta before exposing its sealed parent.

Source qualification only; never deployment or business acceptance.
"""

import ast
import hashlib
import json

ARCHIVE = "release/procurement-review-source-20261118/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "29414d34f9d1f878ba9d2755b02b4e02c3f67224eb40887ad9cb017a48d51399"
BASE_COMMIT = "c0c6dfa547a1963677db974a98f3dbc47b9c130e"
PATHS = (
    "backend/AGENTS.md",
    "backend/README.md",
    "backend/app/gateway/app.py",
    "backend/app/gateway/auth_middleware.py",
    "backend/app/gateway/csrf_middleware.py",
    "backend/app/gateway/procurement_review/__init__.py",
    "backend/app/gateway/procurement_review/config.py",
    "backend/app/gateway/procurement_review/files.py",
    "backend/app/gateway/procurement_review/router.py",
    "backend/app/gateway/procurement_review/rules.py",
    "backend/app/gateway/procurement_review/service.py",
    "backend/packages/harness/deerflow/persistence/migrations/versions/0023_procurement_review_jobs.py",
    "backend/packages/harness/deerflow/persistence/models/__init__.py",
    "backend/packages/harness/deerflow/persistence/procurement_review/__init__.py",
    "backend/packages/harness/deerflow/persistence/procurement_review/model.py",
    "backend/packages/harness/deerflow/persistence/procurement_review/sql.py",
    "backend/tests/test_migration_0022_work.py",
    "backend/tests/test_migration_0023_procurement.py",
    "backend/tests/test_procurement_review.py",
    "docs/procurement-review-agent-prompt.md",
    "docs/procurement-review-integration.md",
    "backend/tests/test_persistence_bootstrap.py",
    "backend/tests/test_persistence_bootstrap_concurrency.py",
    "backend/tests/test_persistence_bootstrap_regression.py",
    "backend/tests/test_work_release_0716.py",
    "backend/tests/test_work_runner_release.py",
    "docker/ppt-master-runner/Dockerfile.production-work-overlay",
)
MARKER = "backend/app/gateway/procurement_review/router.py"


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def historical_view(read):
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        try:
            read(MARKER)
        except (KeyError, FileNotFoundError):
            return read, None
        raise AssertionError("PROCUREMENT_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "PROCUREMENT_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert (
        manifest["schema"] == "procurement-review-source.v1"
        and manifest["base_commit"] == BASE_COMMIT
    ), "PROCUREMENT_PARENT_CHANGED"
    assert manifest["production_acceptance_claimed"] is False, (
        "PROCUREMENT_ACCEPTANCE_OVERCLAIM"
    )
    assert set(manifest["changes"]) == set(PATHS), "PROCUREMENT_SCOPE_CHANGED"
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        assert digest(current) == row["current_sha256"], (
            path,
            "PROCUREMENT_CURRENT_CHANGED",
        )
        parent = (
            None if row["base_sha256"] is None else read(ARCHIVE + "baseline/" + path)
        )
        assert (digest(parent) if parent is not None else None) == row["base_sha256"], (
            path,
            "PROCUREMENT_BASELINE_CHANGED",
        )
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        if path == MANIFEST or path in parents and parents[path] is None:
            raise KeyError(path)
        return parents[path] if path in parents else read(path)

    return projected, {
        "changed_paths_verified": len(parents),
        "added_gateway_runtime": sorted(
            path
            for path, parent in parents.items()
            if parent is None
            and path.endswith(".py")
            and path.startswith(("backend/app/", "backend/packages/harness/"))
        ),
        "production_acceptance_claimed": False,
    }
