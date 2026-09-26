"""The snapshot exclusion contract must match tool output storage."""

import pytest
from pydantic import ValidationError

from deerflow.config.tool_output_config import ToolOutputConfig


@pytest.mark.parametrize("name", ["", ".", "..", "/tmp", "cache/results", r"cache\results"])
def test_tool_result_storage_rejects_non_segment_paths(name):
    with pytest.raises(ValidationError, match="single"):
        ToolOutputConfig(storage_subdir=name)


@pytest.mark.parametrize("name", [".tool-results", "custom-results", "工具日志"])
def test_tool_result_storage_accepts_single_segment(name):
    assert ToolOutputConfig(storage_subdir=name).storage_subdir == name
