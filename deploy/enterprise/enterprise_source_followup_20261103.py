"""Validate a committed successor before exposing immutable historical source bytes.

This is source provenance only, not acceptance of a new production deployment.
"""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

_spec = importlib.util.spec_from_file_location("dashboard_navigation_source_20261103", Path(__file__).with_name("dashboard_navigation_source_20261103.py"))
navigation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(navigation)

_alignment_spec = importlib.util.spec_from_file_location("work_release_alignment_20261104", Path(__file__).with_name("work_release_alignment_20261104.py"))
alignment = importlib.util.module_from_spec(_alignment_spec)
_alignment_spec.loader.exec_module(alignment)

_new_chat_spec = importlib.util.spec_from_file_location("new_chat_model_source_20261105", Path(__file__).with_name("new_chat_model_source_20261105.py"))
new_chat = importlib.util.module_from_spec(_new_chat_spec)
_new_chat_spec.loader.exec_module(new_chat)

_sidechat_spec = importlib.util.spec_from_file_location("sidechat_frontend_source_20261106", Path(__file__).with_name("sidechat_frontend_source_20261106.py"))
sidechat = importlib.util.module_from_spec(_sidechat_spec)
_sidechat_spec.loader.exec_module(sidechat)

_delivery_spec = importlib.util.spec_from_file_location("artifact_delivery_source_20261109", Path(__file__).with_name("artifact_delivery_source_20261109.py"))
delivery = importlib.util.module_from_spec(_delivery_spec)
_delivery_spec.loader.exec_module(delivery)

ARCHIVE = "release/enterprise-source-followup-20261103/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "d383005667ae3f623045ae54fe333ba98677ec3ea03096914b23daf59342d1bc"
BASE_COMMIT = "5a29e5aa66b4190024bfd55be14cbd1cc8b16f77"
SOURCE_COMMIT = "1fbc07fcf6a7a7258d15e84e040042da4b5c8748"
CI_FORMATTING = ARCHIVE + "CI_FORMATTING.json"
CI_FORMATTING_SHA256 = "427d6bcc12ba537c06b97dcbc4755a27b4244b47e671004116750bdef9e2c922"
UNUSED_TEST_IMPORTS = {
    "backend/tests/test_weknora_relay_budget.py": "json",
    "backend/tests/test_weknora_router_counts.py": "asyncio",
}


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


# This projection is for pre-November-4 inventories only. The source and its
# regression tests are pinned to commit 5eb50e329; the parent copy is from ef4ce7a17.
ARTIFACT_SOURCE = "backend/packages/harness/deerflow/agents/middlewares/artifact_delivery_middleware.py"
ARTIFACT_TEST = "backend/tests/test_artifact_delivery_middleware.py"
ARTIFACT_PARENT = "release/artifact-directory-20261104/baseline/" + ARTIFACT_SOURCE
ARTIFACT_LISTING_BASE = "release/artifact-listing-20261105/baseline/"
IMAGE_PNG_ARCHIVE = "release/image-default-png-20261105/"
IMAGE_PNG_MANIFEST_SHA = "604cffe767ee36b266b6d026ce139a32a3e73fb1f3f772cdd502606bf96e2d80"
LISTING_MIDDLEWARE_SHA = "abc9fba6b4ef895460eed366b06c7ffa4472bcf4f77a1e2714e8aef14232877f"
LISTING_SUPPORT = {
    "backend/packages/harness/deerflow/runtime/journal.py": (
        "c48501e0737ff371a33fa8098bb487a3c9e6e9c1a7e1eb3d5ee3d5c3c753d4af", "0057a129f936d36d250b3597683f6d014e7b6a06db093997683627fb9ffd560a"),
    "backend/packages/harness/deerflow/tools/builtins/present_file_tool.py": (
        "7e08d9ff305c1d92eb5591d7b5e74333dce352aaceb3871a7f3d2bf308a947b5", "532af34c6e78a271f90b903cb328710424aec75d15dcc59d65bc01e10f0271ad"),
}


def artifact_directory_historical_read(read):
    read = delivery.historical_read(read)
    try:
        parent = read(ARTIFACT_PARENT)
    except (FileNotFoundError, KeyError):
        return read
    originals = {ARTIFACT_SOURCE: parent}
    expected_source = "f5ed5aae9d465bbda447049b559c7b8c7364542e646b07d2c858e8e32ed6a4af"
    if digest(read(ARTIFACT_SOURCE)) == LISTING_MIDDLEWARE_SHA:
        expected_source = LISTING_MIDDLEWARE_SHA
        for path, (old_sha, new_sha) in LISTING_SUPPORT.items():
            old = read(ARTIFACT_LISTING_BASE + path)
            if digest(old) != old_sha or digest(read(path)) != new_sha:
                raise ValueError(("ARTIFACT_DIRECTORY_SOURCE_CHANGED", path))
            originals[path] = old
    for path, raw, expected in (
        (ARTIFACT_PARENT, parent, "0ad708d197b16e2f1494541059bb9e2054670c921717e5c8e0b9f9372f8df0ba"),
        (ARTIFACT_SOURCE, read(ARTIFACT_SOURCE), expected_source),
        (ARTIFACT_TEST, read(ARTIFACT_TEST), "e3a87278252e3fac05bdb561ac1593076d70536162e7f683ca8955631f8cb619"),
    ):
        if digest(raw) != expected:
            raise ValueError(("ARTIFACT_DIRECTORY_SOURCE_CHANGED", path))
    return lambda path: originals[path] if path in originals else read(path)


def formatting_fingerprint(raw, path):
    """Ignore layout and contiguous import ordering, never executable statements."""
    tree = ast.parse(raw)
    unused = UNUSED_TEST_IMPORTS.get(path)
    if unused:
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.Import) and len(node.names) == 1
            and node.names[0].name == unused and node.names[0].asname is None
        )]
    result, imports = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                atom = ast.Import(names=[alias]) if isinstance(node, ast.Import) else ast.ImportFrom(module=node.module, names=[alias], level=node.level)
                imports.append(ast.dump(atom, include_attributes=False))
        else:
            if imports:
                result.append(sorted(imports))
                imports = []
            result.append(ast.dump(node, include_attributes=False))
    if imports:
        result.append(sorted(imports))
    return result


def formatting_read(read, manifest):
    try:
        raw = read(CI_FORMATTING)
    except (FileNotFoundError, KeyError):
        return read, 0
    assert digest(raw) == CI_FORMATTING_SHA256, "ENTERPRISE_FORMATTING_SEAL_CHANGED"
    formatting = json.loads(raw)
    assert formatting["schema"] == "enterprise-source-formatting.v1", "ENTERPRISE_FORMATTING_SCHEMA"
    assert formatting["parent_manifest_sha256"] == MANIFEST_SHA256, "ENTERPRISE_FORMATTING_PARENT"
    originals = {}
    for path, row in formatting["changes"].items():
        assert path in manifest["changes"] and path.endswith(".py"), "ENTERPRISE_FORMATTING_SCOPE"
        assert row["baseline_path"] == ARCHIVE + "ci-baseline/" + path, "ENTERPRISE_FORMATTING_PATH"
        original, current = read(row["baseline_path"]), read(path)
        assert digest(original) == row["baseline_sha256"] == manifest["changes"][path]["current_sha256"], (path, "ENTERPRISE_FORMATTING_BASE_CHANGED")
        assert digest(current) == row["formatted_sha256"], (path, "ENTERPRISE_FOLLOWUP_CURRENT_CHANGED")
        assert formatting_fingerprint(original, path) == formatting_fingerprint(current, path), (path, "ENTERPRISE_FORMATTING_LOGIC_CHANGED")
        originals[path] = original
    return lambda path: originals[path] if path in originals else read(path), len(originals)


def qualify(read):
    if isinstance(read, HistoricalReader):
        verified = qualify(read.original_read)
        assert verified == read.qualification, "ENTERPRISE_FOLLOWUP_READER_CHANGED"
        return verified
    return _qualify_projected(sidechat.historical_read(new_chat.historical_read(alignment.historical_read(read))))


def _qualify_projected(read):
    read = navigation.historical_read(read)
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        return {}
    assert digest(raw) == MANIFEST_SHA256, "ENTERPRISE_FOLLOWUP_SEAL_CHANGED"
    manifest = json.loads(raw)
    assert manifest["schema"] == "enterprise-source-followup.v1", "ENTERPRISE_FOLLOWUP_SCHEMA"
    assert manifest["status"] == "COMMITTED_SOURCE_NOT_DEPLOYMENT_ACCEPTANCE", "ENTERPRISE_FOLLOWUP_STATUS"
    assert manifest["base_commit"] == BASE_COMMIT, "ENTERPRISE_FOLLOWUP_PARENT"
    assert manifest["source_commit"] == SOURCE_COMMIT, "ENTERPRISE_FOLLOWUP_SOURCE"
    assert manifest["production_acceptance_claimed"] is False, "ENTERPRISE_FOLLOWUP_OVERCLAIM"
    assert manifest["work_datalake_deferred"] is True and manifest["database_head"] == "0022", "ENTERPRISE_FOLLOWUP_FEATURE_SCOPE"
    read, formatted = formatting_read(read, manifest)
    for path, row in manifest["changes"].items():
        pure = PurePosixPath(path)
        assert not pure.is_absolute() and ".." not in pure.parts and "\\" not in path and pure.as_posix() == path, "ENTERPRISE_FOLLOWUP_PATH"
        runtime = path.startswith(("backend/app/", "backend/packages/harness/", "frontend/src/"))
        tests = path.startswith(("backend/tests/", "frontend/tests/"))
        assert (runtime and row["kind"] == "runtime-source") or (tests and row["kind"] == "regression-test"), "ENTERPRISE_FOLLOWUP_SCOPE"
        current = read(path)
        assert digest(current) == row["current_sha256"], (path, "ENTERPRISE_FOLLOWUP_CURRENT_CHANGED")
        if path.endswith(".py"):
            ast.parse(current, filename=path)
        if row["base_sha256"] is None:
            assert row["baseline_path"] is None, "ENTERPRISE_FOLLOWUP_UNEXPECTED_BASELINE"
        else:
            assert row["baseline_path"] == ARCHIVE + "baseline/" + path, "ENTERPRISE_FOLLOWUP_BASELINE_PATH"
            assert digest(read(row["baseline_path"])) == row["base_sha256"], (path, "ENTERPRISE_FOLLOWUP_BASELINE_CHANGED")
    return dict(manifest, formatting_corrections_verified=formatted)


def image_png_historical_read(read):
    try:
        manifest = read(IMAGE_PNG_ARCHIVE + "SOURCE.json")
    except (FileNotFoundError, KeyError):
        return read
    if digest(manifest) != IMAGE_PNG_MANIFEST_SHA:
        raise ValueError("IMAGE_PNG_MANIFEST_CHANGED")
    originals = {}
    for path, row in json.loads(manifest)["files"].items():
        if digest(read(path)) != row["after_sha256_lf"]:
            raise ValueError(("IMAGE_PNG_SOURCE_CHANGED", path))
        if row["before_sha256_lf"] is None:
            continue
        parent = read(IMAGE_PNG_ARCHIVE + "baseline/" + path)
        if digest(parent) != row["before_sha256_lf"]:
            raise ValueError(("IMAGE_PNG_PARENT_CHANGED", path))
        originals[path] = parent
    return lambda path: originals[path] if path in originals else read(path)


class HistoricalReader:
    """Only the fixed, verified changes are projected back for older audits."""

    def __init__(self, read, qualification):
        verified = qualify(read)
        assert verified and verified == qualification, "ENTERPRISE_FOLLOWUP_READER_CHANGED"
        self.original_read = read
        self.read = navigation.historical_read(sidechat.historical_read(new_chat.historical_read(alignment.historical_read(read))))
        self.read = image_png_historical_read(self.read)
        self.qualification = verified

    def __call__(self, path):
        row = self.qualification["changes"].get(path)
        if row is None:
            return self.read(path)
        if row["baseline_path"] is None:
            raise KeyError(path)
        return self.read(row["baseline_path"])


def historical_read(read):
    if isinstance(read, HistoricalReader):
        qualify(read)
        return read
    original = read
    read = alignment.historical_read(read)
    read = new_chat.historical_read(read)
    read = sidechat.historical_read(read)
    read = navigation.historical_read(read)
    qualification = _qualify_projected(read)
    return HistoricalReader(original, qualification) if qualification else read


def summary(qualification):
    if not qualification:
        return None
    return {
        "status": qualification["status"],
        "source_commit": qualification["source_commit"],
        "changed_paths_verified": len(qualification["changes"]),
        "runtime_sources_verified": sum(row["kind"] == "runtime-source" for row in qualification["changes"].values()),
        "production_acceptance_claimed": False,
        "formatting_corrections_verified": qualification.get("formatting_corrections_verified", 0),
    }
