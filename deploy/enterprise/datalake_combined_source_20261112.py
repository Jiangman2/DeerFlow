"""Qualify PR46/49 and their integration fixes before projecting its exact parent."""

import ast
import hashlib
import json

ARCHIVE = 'release/datalake-combined-20261112/'
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = 'c9e9cc0b25d4aed3ddab40766b840f73cdd644ebb1bf03fbd1d2bfe4850c8028'
BASE_COMMIT = 'd58c87dfd5f961548dcb44e861ee88c5ea6853ee'
PATHS = ('backend/AGENTS.md', 'backend/README.md', 'backend/app/gateway/auth/cnooc.py', 'backend/app/gateway/services.py', 'backend/docs/REPLAY_E2E.md', 'backend/packages/harness/deerflow/agents/lead_agent/prompt.py', 'backend/packages/harness/deerflow/agents/memory/backends/deermem/deermem/core/prompt.py', 'backend/packages/harness/deerflow/agents/middlewares/datalake_source_middleware.py', 'backend/packages/harness/deerflow/agents/middlewares/input_sanitization_middleware.py', 'backend/packages/harness/deerflow/tools/builtins/clarification_tool.py', 'backend/tests/replay_provider.py', 'backend/tests/test_cnooc_auth.py', 'backend/tests/test_data_lake_auth.py', 'backend/tests/test_datalake_model_routing.py', 'backend/tests/test_gateway_services.py', 'backend/tests/test_replay_provider.py', 'docs/assets/data-lake-pr-46/file-statuses.png', 'docs/assets/data-lake-pr-46/login-required.png', 'docs/datalake-natural-query-routing.md', 'frontend/AGENTS.md', 'frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx', 'frontend/src/app/workspace/chats/[thread_id]/page.tsx', 'frontend/src/components/workspace/input-box.tsx', 'frontend/src/components/workspace/shop-cart-attachments/new-shop-cart-dialog.tsx', 'frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx', 'frontend/src/core/i18n/locales/en-US.ts', 'frontend/src/core/i18n/locales/types.ts', 'frontend/src/core/i18n/locales/zh-CN.ts', 'frontend/src/core/shop-cart/api-files.ts', 'frontend/tests/e2e/data-lake-branch.spec.ts', 'frontend/tests/unit/app/api/cnooc-platform-token-routes.test.ts', 'frontend/tests/unit/components/workspace/new-shop-cart-dialog.dom.test.tsx', 'frontend/tests/unit/components/workspace/shop-cart-attachment-button.test.ts', 'frontend/tests/unit/components/workspace/shop-cart-authorization.dom.test.ts', 'frontend/tests/unit/core/shop-cart/api-files.test.ts')
MARKER = "frontend/tests/e2e/data-lake-branch.spec.ts"


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
        raise AssertionError("DATALAKE_COMBINED_MANIFEST_REQUIRED")
    assert digest(raw) == MANIFEST_SHA256, "DATALAKE_COMBINED_MANIFEST_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "datalake-combined-source.v1", "DATALAKE_COMBINED_SCHEMA"
    assert manifest["base_commit"] == BASE_COMMIT, "DATALAKE_COMBINED_PARENT"
    assert manifest["production_acceptance_claimed"] is False, "DATALAKE_COMBINED_ACCEPTANCE"
    assert set(manifest["changes"]) == set(PATHS), "DATALAKE_COMBINED_SCOPE"
    parents = {}
    for path, row in manifest["changes"].items():
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "DATALAKE_COMBINED_CURRENT_CHANGED")
        parent = None if row["base_sha256"] is None else read(ARCHIVE + "baseline/" + path)
        assert (digest(parent) if parent is not None else None) == row["base_sha256"], (path, "DATALAKE_COMBINED_PARENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        parents[path] = parent

    def projected(path):
        if path == MANIFEST or path in parents and parents[path] is None:
            raise KeyError(path)
        return parents[path] if path in parents else read(path)

    return projected, {"base_commit": BASE_COMMIT, "changed_paths_verified": len(parents), "production_acceptance_claimed": False}
