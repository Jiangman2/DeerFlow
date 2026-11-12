"""Qualify the finite monitoring successor before projecting its exact parent."""

import ast
import hashlib
import json

ARCHIVE = "release/lease-work-source-20261110/model-monitor-20261111/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "99b40ba741493cfbae2329181183ce37f9dca78fb50c56a9e92522b4c7502c82"
BASE_COMMIT = "c54b723b427702fdbafd688c86887972bd62a51a"
PATHS = (
    "backend/app/gateway/model_monitor.py",
    "backend/packages/harness/deerflow/models/factory.py",
    "backend/packages/harness/deerflow/observability/model_metrics.py",
    "backend/tests/test_model_monitor.py",
    "backend/tests/test_model_metrics_cancellation.py",
    "frontend/src/components/workspace/model-monitor-panel.tsx",
    "frontend/src/core/console/model-monitor.ts",
    "frontend/src/core/i18n/model-monitor.ts",
)
MARKER = "backend/tests/test_model_metrics_cancellation.py"


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
        raise AssertionError("MODEL_MONITOR_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "MODEL_MONITOR_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "model-monitor-source-provenance.v1", "MODEL_MONITOR_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT, "MODEL_MONITOR_PARENT"
    assert manifest["production_acceptance_claimed"] is False, "MODEL_MONITOR_ACCEPTANCE"
    assert set(manifest["changes"]) == set(PATHS), "MODEL_MONITOR_SCOPE"
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "MODEL_MONITOR_CURRENT_CHANGED")
        parent = None if row["base_sha256"] is None else read(ARCHIVE + "baseline/" + path)
        assert (digest(parent) if parent is not None else None) == row["base_sha256"], (path, "MODEL_MONITOR_PARENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        if path == MANIFEST or path in parents and parents[path] is None:
            raise KeyError(path)
        return parents[path] if path in parents else read(path)

    return projected, {"base_commit": BASE_COMMIT, "changed_paths_verified": len(parents), "production_acceptance_claimed": False}
