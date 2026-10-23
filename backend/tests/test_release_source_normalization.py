"""Formatting provenance must reject executable changes, not just hash drift."""

import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/enterprise/verify-release-source-20261023.py"
spec = importlib.util.spec_from_file_location("release_source_verifier", SOURCE)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_layout_normalization_preserves_syntax():
    assert verifier.syntax_fingerprint("def f():\n return (1 + 2)\n") == verifier.syntax_fingerprint("def f():\n    return 1 + 2\n")


def test_import_order_requires_explicit_opt_in():
    before = "import logging\nimport json\nVALUE = 3\n"
    after = "import json\nimport logging\nVALUE = 3\n"
    assert verifier.syntax_fingerprint(before) != verifier.syntax_fingerprint(after)
    assert verifier.syntax_fingerprint(before, import_order=True) == verifier.syntax_fingerprint(after, import_order=True)


@pytest.mark.parametrize(
    "candidate",
    [
        "import json as logging\nVALUE = 3\n",
        "import json\nVALUE = 4\n",
        "import json\nVALUE = dangerous()\n",
        "VALUE = 3\nimport json\n",
    ],
)
def test_executable_or_binding_changes_cannot_be_classified_as_formatting(candidate):
    assert verifier.syntax_fingerprint("import json\nVALUE = 3\n", import_order=True) != verifier.syntax_fingerprint(candidate, import_order=True)
