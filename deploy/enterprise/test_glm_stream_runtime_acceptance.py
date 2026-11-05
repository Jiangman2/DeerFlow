"""Real installed SDK acceptance helper, with network disabled by the runner."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

FOLDER = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("runtime_acceptance", FOLDER / "check-glm-stream-runtime.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FILES = json.loads((FOLDER / "glm-stream-release-20261021-r1.json").read_text())["profile"]["tail_files"]["gateway-forward"]["files"]


def require(ok, code):
    if not ok:
        raise ValueError(code)


def test_actual_sdk_complete_and_incomplete_stream():
    result = MODULE.check_stream_runtime(True, FILES, require)
    assert result["offline_terminal_and_done_checked"] is True
    assert result["provider_requests"] == 0
    assert result["source_count"] == 4


def test_changed_sdk_fails_before_semantic_probe():
    with patch("importlib.metadata.version", return_value="unqualified"):
        with pytest.raises(ValueError, match="stream_sdk_version"):
            MODULE.check_stream_runtime(True, FILES, require)


def test_old_base_rejected_on_new_source():
    with pytest.raises(ValueError, match="stream_source_mismatch"):
        MODULE.check_stream_runtime(False, FILES, require)
