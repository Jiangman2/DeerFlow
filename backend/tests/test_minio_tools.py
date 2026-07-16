from deerflow.community.minio.tools import HARD_MAX_FETCH_CHARS, _truncate_text_for_tool


def test_truncate_text_for_tool_enforces_hard_cap_when_model_requests_more():
    text = "x" * (HARD_MAX_FETCH_CHARS + 100)

    result = _truncate_text_for_tool(text, requested_max_chars=HARD_MAX_FETCH_CHARS + 40_000)

    assert len(result) < len(text)
    assert "truncated to" in result
    assert str(HARD_MAX_FETCH_CHARS) in result
    assert result.startswith("x" * 100)
