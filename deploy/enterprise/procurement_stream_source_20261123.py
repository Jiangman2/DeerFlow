"""Finite Markdown/SSE and Data QA deployment-source successor; no live acceptance claim."""
import ast
import hashlib
import json

ARCHIVE = 'release/procurement-stream-source-20261123/'
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = 'bda59f789efb33b791f4d6e2f79fa3479fe282c78562d6fb9b5e24aad0d4b797'
BASE_COMMIT = 'd2a7ea4dbb0f98c70321ddc65958d41aa4d28825'
PATHS = ('.github/workflows/nov02-released-fixes.yml', 'backend/AGENTS.md', 'backend/README.md', 'backend/app/gateway/procurement_review/config.py', 'backend/app/gateway/procurement_review/files.py', 'backend/app/gateway/procurement_review/markdown_report.py', 'backend/app/gateway/procurement_review/router.py', 'backend/app/gateway/procurement_review/rules.py', 'backend/app/gateway/procurement_review/service.py', 'backend/app/gateway/procurement_review/stream.py', 'backend/tests/fixtures/procurement_callback.py', 'backend/tests/test_procurement_input_formats.py', 'backend/tests/test_procurement_markdown.py', 'backend/tests/test_procurement_review.py', 'backend/tests/test_procurement_stream.py', 'deploy/enterprise/procurement-dataqa-20261123/AGENTS.md', 'deploy/enterprise/procurement-dataqa-20261123/README.md', 'deploy/enterprise/procurement-dataqa-20261123/config_reconcile.py', 'deploy/enterprise/procurement-dataqa-20261123/dataqa_config.py', 'deploy/enterprise/procurement-dataqa-20261123/fixtures/files.py', 'deploy/enterprise/procurement-dataqa-20261123/fixtures/service.py', 'deploy/enterprise/procurement-dataqa-20261123/fixtures/stream.py', 'deploy/enterprise/procurement-dataqa-20261123/manifest.json', 'deploy/enterprise/procurement-dataqa-20261123/payload/markdown_report.py', 'deploy/enterprise/procurement-dataqa-20261123/probe.py', 'deploy/enterprise/procurement-dataqa-20261123/readiness_wait.py', 'deploy/enterprise/procurement-dataqa-20261123/reconcile_runtime.py', 'deploy/enterprise/procurement-dataqa-20261123/source.patch', 'deploy/enterprise/procurement-dataqa-20261123/test_config_reconcile.py', 'deploy/enterprise/procurement-dataqa-20261123/test_dataqa.py', 'deploy/enterprise/procurement-dataqa-20261123/test_delivery.py', 'deploy/enterprise/procurement-dataqa-20261123/test_readiness.py', 'deploy/enterprise/procurement-dataqa-20261123/test_secret_names.py', 'deploy/enterprise/procurement-dataqa-20261123/update.py', 'deploy/enterprise/test_procurement_review_source_20261118.py', 'deploy/enterprise/test_procurement_stream_source_20261123.py', 'deploy/enterprise/verify-dashboard-navigation-source-20261103.py', 'deploy/enterprise/verify-history-composer-source-20261106.py', 'deploy/enterprise/verify-nov01-released-source-20261102.py', 'deploy/enterprise/verify-nov02-released-source-20261103.py', 'deploy/enterprise/verify-personal-notification-source-20261117.py', 'deploy/enterprise/verify-post51-energy-source-20261116.py', 'deploy/enterprise/verify-release-source-20261023.py', 'docs/procurement-review-agent-prompt.md', 'docs/procurement-review-integration.md', 'k8s/local-pvc/nginx.conf', 'tests/test_procurement_nginx_route.py')
MARKER = "backend/app/gateway/procurement_review/stream.py"

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
        raise AssertionError("PROCUREMENT_STREAM_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "PROCUREMENT_STREAM_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "procurement-stream-source.v1" and manifest["base_commit"] == BASE_COMMIT
    assert manifest["production_acceptance_claimed"] is False
    assert set(manifest["changes"]) == set(PATHS), "PROCUREMENT_STREAM_SCOPE_CHANGED"
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "PROCUREMENT_STREAM_CURRENT_CHANGED")
        parent = None if row["base_sha256"] is None else read(ARCHIVE + "baseline/" + path)
        assert (digest(parent) if parent is not None else None) == row["base_sha256"], (path, "PROCUREMENT_STREAM_PARENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        parents[path] = parent
    def projected(path):
        if path == MANIFEST or path in parents and parents[path] is None:
            raise KeyError(path)
        return parents[path] if path in parents else read(path)
    return projected, {"changed_paths_verified": len(parents), "production_acceptance_claimed": False,
        "added_gateway_runtime": sorted(p for p, old in parents.items() if old is None and p.endswith(".py") and p.startswith(("backend/app/", "backend/packages/harness/")))}
