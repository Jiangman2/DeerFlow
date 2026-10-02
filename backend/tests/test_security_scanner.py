from types import SimpleNamespace

import pytest

from deerflow.skills.security_scanner import _extract_json_object, scan_skill_content


def _make_env(monkeypatch, response_content):
    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
    fake_response = SimpleNamespace(content=response_content)

    class FakeModel:
        async def ainvoke(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            return fake_response

    model = FakeModel()

    def _fake_create_chat_model(**kwargs):
        model.create_kwargs = kwargs
        return model

    monkeypatch.setattr("deerflow.skills.security_scanner.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.skills.security_scanner.create_chat_model", _fake_create_chat_model)
    return model


SKILL_CONTENT = "---\nname: demo-skill\ndescription: demo\n---\n"


# --- _extract_json_object unit tests ---


def test_extract_json_plain():
    assert _extract_json_object('{"decision":"allow","reason":"ok"}') == {"decision": "allow", "reason": "ok"}


def test_extract_json_markdown_fence():
    raw = '```json\n{"decision": "allow", "reason": "ok"}\n```'
    assert _extract_json_object(raw) == {"decision": "allow", "reason": "ok"}


def test_extract_json_fence_no_language():
    raw = '```\n{"decision": "allow", "reason": "ok"}\n```'
    assert _extract_json_object(raw) == {"decision": "allow", "reason": "ok"}


def test_extract_json_prose_wrapped():
    raw = 'Looking at this content I conclude: {"decision": "allow", "reason": "clean"} and that is final.'
    assert _extract_json_object(raw) == {"decision": "allow", "reason": "clean"}


def test_extract_json_nested_braces_in_reason():
    raw = '{"decision": "allow", "reason": "no issues with {placeholder} found"}'
    assert _extract_json_object(raw) == {"decision": "allow", "reason": "no issues with {placeholder} found"}


def test_extract_json_nested_braces_code_snippet():
    raw = 'Here is my review: {"decision": "block", "reason": "contains {\\"x\\": 1} code injection"}'
    assert _extract_json_object(raw) == {"decision": "block", "reason": 'contains {"x": 1} code injection'}


def test_extract_json_returns_none_for_garbage():
    assert _extract_json_object("no json here") is None


def test_extract_json_returns_none_for_unclosed_brace():
    assert _extract_json_object('{"decision": "allow"') is None


# --- scan_skill_content integration tests ---


@pytest.mark.anyio
async def test_scan_skill_content_passes_run_name_to_model(monkeypatch):
    model = _make_env(monkeypatch, '{"decision":"allow","reason":"ok"}')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "allow"
    assert model.kwargs["config"] == {"run_name": "security_agent"}


@pytest.mark.anyio
async def test_scan_skill_content_parses_responses_api_text_blocks(monkeypatch):
    _make_env(
        monkeypatch,
        [{"type": "text", "text": '{"decision":"allow","reason":"clean"}'}],
    )

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "allow"
    assert result.reason == "clean"


@pytest.mark.anyio
async def test_scan_skill_content_ignores_non_text_blocks_and_joins_text_blocks(monkeypatch):
    _make_env(
        monkeypatch,
        [
            {"type": "reasoning", "text": '{"decision":"block","reason":"fake"}'},
            {"type": "text", "text": '{"decision":"allow",'},
            {"type": "output_text", "text": '"reason":"clean"}'},
            {"type": "tool_call", "text": '{"decision":"block","reason":"fake"}'},
        ],
    )

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "allow"
    assert result.reason == "clean"


@pytest.mark.anyio
async def test_scan_skill_content_blocks_when_model_unavailable(monkeypatch):
    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
    monkeypatch.setattr("deerflow.skills.security_scanner.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.skills.security_scanner.create_chat_model", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "block"
    assert "unavailable" in result.reason


@pytest.mark.anyio
async def test_scan_skill_content_retries_transient_model_failure(monkeypatch):
    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
    attempts = 0
    create_kwargs = []

    class FakeModel:
        async def ainvoke(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary provider failure")
            return SimpleNamespace(content='{"decision":"allow","reason":"ok"}')

    monkeypatch.setattr("deerflow.skills.security_scanner.get_app_config", lambda: config)
    monkeypatch.setattr(
        "deerflow.skills.security_scanner.create_chat_model",
        lambda **kwargs: create_kwargs.append(kwargs) or FakeModel(),
    )
    monkeypatch.setattr("deerflow.skills.security_scanner.asyncio.sleep", lambda *_: _async_none())

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "allow"
    assert attempts == 2
    assert len(create_kwargs) == 2
    assert all(kwargs["attach_tracing"] is False for kwargs in create_kwargs)


@pytest.mark.anyio
async def test_scan_skill_content_retries_unparseable_model_response(monkeypatch):
    config = SimpleNamespace(skill_evolution=SimpleNamespace(moderation_model_name=None))
    attempts = 0

    class FakeModel:
        async def ainvoke(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return SimpleNamespace(content="temporarily truncated")
            return SimpleNamespace(content='{"decision":"allow","reason":"ok"}')

    monkeypatch.setattr("deerflow.skills.security_scanner.get_app_config", lambda: config)
    monkeypatch.setattr("deerflow.skills.security_scanner.create_chat_model", lambda **kwargs: FakeModel())
    monkeypatch.setattr("deerflow.skills.security_scanner.asyncio.sleep", lambda *_: _async_none())

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "allow"
    assert attempts == 2


async def _async_none():
    return None


@pytest.mark.anyio
async def test_scan_skill_content_blocks_when_configuration_unavailable(monkeypatch):
    monkeypatch.setattr(
        "deerflow.skills.security_scanner.get_app_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )

    result = await scan_skill_content(SKILL_CONTENT, executable=False)

    assert result.decision == "block"
    assert "unavailable" in result.reason


@pytest.mark.anyio
async def test_scan_allows_markdown_fenced_response(monkeypatch):
    _make_env(monkeypatch, '```json\n{"decision": "allow", "reason": "clean"}\n```')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "allow"
    assert result.reason == "clean"


@pytest.mark.anyio
async def test_scan_normalizes_decision_case(monkeypatch):
    _make_env(monkeypatch, '{"decision": "Allow", "reason": "looks fine"}')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "allow"


@pytest.mark.anyio
async def test_scan_normalizes_uppercase_decision(monkeypatch):
    _make_env(monkeypatch, '{"decision": "BLOCK", "reason": "dangerous"}')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "block"


@pytest.mark.anyio
async def test_scan_handles_nested_braces_in_reason(monkeypatch):
    _make_env(monkeypatch, '{"decision": "allow", "reason": "no issues with {placeholder}"}')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "allow"
    assert "{placeholder}" in result.reason


@pytest.mark.anyio
async def test_scan_handles_prose_wrapped_json(monkeypatch):
    _make_env(monkeypatch, 'I reviewed the content: {"decision": "allow", "reason": "safe"}\nDone.')
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "allow"


@pytest.mark.anyio
async def test_scan_distinguishes_unparseable_from_unavailable(monkeypatch):
    _make_env(monkeypatch, "I can't decide, this is just prose without any JSON at all.")
    result = await scan_skill_content(SKILL_CONTENT, executable=False)
    assert result.decision == "block"
    assert "unparseable" in result.reason


@pytest.mark.anyio
async def test_scan_distinguishes_unparseable_executable(monkeypatch):
    _make_env(monkeypatch, "no json here")
    result = await scan_skill_content(SKILL_CONTENT, executable=True)
    # Even for executable content, unparseable uses the unparseable message
    assert result.decision == "block"
    assert "unparseable" in result.reason


# Standalone/in-graph moderation tracing is deferred with its two incoming-only
# tests until the scanner signature and all callers are reconciled. Retain the
# pre-merge attach_tracing=False policy and every preexisting test above.
