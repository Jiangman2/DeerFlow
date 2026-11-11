"""Current-request provenance survives real Gateway assembly, never user labels."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.gateway.auth_disabled import AUTH_SOURCE_INTERNAL
from app.gateway.message_source import CHANNEL_PROVIDERS
from app.gateway.services import inject_authenticated_user_context, merge_run_context_overrides


def request(source, user=None):
    return SimpleNamespace(state=SimpleNamespace(auth_source=source, user=user))


@pytest.mark.parametrize("auth_source", ["session", "auth_disabled", "oa_policy_flow", None])
@pytest.mark.parametrize("section", ["context", "configurable"])
def test_external_or_historical_labels_cannot_make_a_work_message(auth_source, section):
    config = {
        section: {"channel_name": "feishu", "channel_source": {"type": "im_channel", "provider": "feishu"}, "message_source": {"kind": "im", "provider": "feishu"}, "channel_user_id": "forged", "work_capabilities": {"available": True}}
    }
    body = {"channel_name": "feishu", "message_source": {"kind": "im", "provider": "feishu"}, "channel_user_id": "forged", "work_capabilities": {"available": True}}
    inject_authenticated_user_context(config, request(auth_source), request_context=body)
    assert config["context"]["message_source"] == {"kind": "web"}
    assert config["context"]["channel_name"] is None
    assert "channel_source" not in config["context"]
    assert "channel_user_id" not in config["context"]
    assert config["context"]["work_capabilities"] is None
    assert not {"channel_name", "channel_source", "message_source", "channel_user_id", "work_capabilities"} & config.get("configurable", {}).keys()


@pytest.mark.parametrize("provider", sorted(CHANNEL_PROVIDERS))
def test_authenticated_channel_uses_only_top_level_provider(provider):
    config = {
        "context": {"channel_name": "stale", "message_source": {"kind": "web"}, "model_name": "unchanged"},
        "configurable": {"channel_name": "stale", "message_source": {"kind": "im", "provider": "feishu"}, "thread_id": "same-thread"},
    }
    body = {"channel_name": provider, "channel_user_id": "sender", "message_source": {"kind": "web"}, "work_capabilities": {"available": True}}
    original_body = deepcopy(body)
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context=body)
    assert config["context"]["message_source"] == {"kind": "im", "provider": provider}
    assert config["context"]["channel_name"] == provider
    assert config["context"]["channel_user_id"] == "sender"
    assert config["context"]["model_name"] == "unchanged"
    assert config["context"]["work_capabilities"] == ({"available": True} if provider == "feishu" else None)
    assert config["configurable"] == {"thread_id": "same-thread"}
    assert body == original_body


@pytest.mark.parametrize("available", [False, True])
def test_internal_feishu_capability_hint_requires_an_actual_boolean(available):
    config = {}
    body = {"channel_name": "feishu", "work_capabilities": {"available": available}}
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context=body)
    assert config["context"]["work_capabilities"] == {"available": available}
    assert config["context"]["work_capabilities"] is not body["work_capabilities"]


@pytest.mark.parametrize("capabilities", [None, {}, True, "available", [], {"available": 1}, {"available": "true"}, {"available": True, "token": "synthetic-secret"}])
def test_malformed_or_expanded_capability_claims_are_not_promoted(capabilities):
    config = {"context": {"work_capabilities": {"available": True}}, "configurable": {"work_capabilities": {"available": True}}}
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context={"channel_name": "feishu", "work_capabilities": capabilities})
    assert config["context"]["work_capabilities"] is None
    assert "work_capabilities" not in config["configurable"]


def test_source_provider_catalog_matches_registered_channels():
    from app.channels.service import _CHANNEL_REGISTRY

    assert CHANNEL_PROVIDERS == set(_CHANNEL_REGISTRY)


@pytest.mark.parametrize("provider", [None, "", "unknown", "Feishu", "feishu ", "feishu\n", 1, True, ["feishu"], {"provider": "feishu"}])
def test_internal_auth_is_not_itself_a_work_source(provider):
    config = {"context": {"channel_name": "feishu", "message_source": {"kind": "im", "provider": "feishu"}}}
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context={"channel_name": provider, "message_source": {"kind": "im", "provider": "feishu"}})
    assert config["context"]["is_internal"] is True
    assert config["context"]["message_source"] == {"kind": "internal"}
    assert config["context"]["channel_name"] is None


def test_each_turn_replaces_source_without_mutating_the_prior_receipt():
    config = {}
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context={"channel_name": "feishu", "channel_user_id": "first", "work_capabilities": {"available": True}})
    prior_source = config["context"]["message_source"]
    inject_authenticated_user_context(config, request("session"))
    assert config["context"]["message_source"] == {"kind": "web"}
    assert config["context"]["channel_name"] is None
    assert "channel_user_id" not in config["context"]
    assert config["context"]["work_capabilities"] is None
    assert prior_source == {"kind": "im", "provider": "feishu"}
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context={"channel_name": "slack", "channel_user_id": "second"})
    assert config["context"]["message_source"] == {"kind": "im", "provider": "slack"}
    assert config["context"]["channel_user_id"] == "second"


def test_existing_internal_channel_policy_flags_remain_intact():
    config = {}
    body = {"channel_name": "github", "channel_user_id": "commenter", "non_interactive": True, "disable_clarification": True, "github_token": "synthetic-token"}
    merge_run_context_overrides(config, body, internal=True)
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context=body)
    assert config["context"]["channel_name"] == "github"
    assert config["context"]["message_source"] == {"kind": "im", "provider": "github"}
    assert config["context"]["non_interactive"] is True
    assert config["context"]["disable_clarification"] is True
    assert config["context"]["github_token"] == "synthetic-token"
    assert "github_token" not in config["configurable"]


def test_channel_adapter_identity_wins_over_session_source_hints(tmp_path):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(
        MessageBus(),
        ChannelStore(path=tmp_path / "channels.json"),
        default_session={"context": {"channel_name": "github", "message_source": {"kind": "web"}, "work_capabilities": {"available": True}}},
        channel_sessions={"feishu": {"context": {"channel_name": "slack", "channel_user_id": "stale-sender"}}},
    )
    msg = InboundMessage(channel_name="feishu", chat_id="chat", user_id="actual-sender", text="hello", owner_user_id="owner")
    _, config, context = manager._resolve_run_params(msg, "thread")
    merge_run_context_overrides(config, context, internal=True)
    inject_authenticated_user_context(config, request(AUTH_SOURCE_INTERNAL), request_context=context)
    assert config["context"]["message_source"] == {"kind": "im", "provider": "feishu"}
    assert config["context"]["channel_user_id"] == msg.user_id
    assert config["context"]["user_id"] == msg.owner_user_id
    assert config["context"]["work_capabilities"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("initialization_fails", [False, True])
@pytest.mark.parametrize("handled", [False, True])
async def test_only_unavailable_feishu_integration_uses_intent_fallback(tmp_path, caplog, initialization_fails, handled):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    manager._get_client = Mock(return_value=object())
    manager._get_or_create_thread = AsyncMock(return_value=("thread", False))
    manager._update_thread_channel_metadata = AsyncMock()
    manager._handle_chat_on_thread = AsyncMock()
    msg = InboundMessage(channel_name="feishu", chat_id="chat", user_id="sender", text="hello", owner_user_id="owner")
    active = AsyncMock(side_effect=RuntimeError("must-not-log-secret") if initialization_fails else None, return_value=None)
    fallback = AsyncMock(return_value=handled)
    with (
        patch("app.integrations.work.service.active_work_service", active),
        patch("app.integrations.work.service.handle_unavailable_work", fallback),
    ):
        await manager._handle_chat(msg, bound_identity_checked=True)
    fallback.assert_awaited_once_with(msg, send=manager.bus.publish_outbound, semaphore=manager._work_unavailable_semaphore)
    assert isinstance(manager._work_unavailable_semaphore, asyncio.Semaphore)
    assert manager._handle_chat_on_thread.await_count == (0 if handled else 1)
    assert "must-not-log-secret" not in caplog.text


@pytest.mark.asyncio
async def test_unavailable_work_fallback_reuses_one_bounded_manager_semaphore(tmp_path):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    assert manager._work_unavailable_semaphore is None
    entered_two = asyncio.Event()
    release = asyncio.Event()
    active, maximum = 0, 0
    semaphores = []

    async def classify(msg, *, send, semaphore):
        nonlocal active, maximum
        assert send == manager.bus.publish_outbound
        semaphores.append(semaphore)
        async with semaphore:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered_two.set()
            try:
                await release.wait()
            finally:
                active -= 1
        return True

    messages = [InboundMessage(channel_name="feishu", chat_id=f"chat-{index}", user_id="sender", text="hello", owner_user_id="owner") for index in range(4)]
    with (
        patch("app.integrations.work.service.active_work_service", AsyncMock(return_value=None)),
        patch("app.integrations.work.service.handle_unavailable_work", AsyncMock(side_effect=classify)) as fallback,
    ):
        tasks = [asyncio.create_task(manager._handle_chat(msg, bound_identity_checked=True)) for msg in messages]
        try:
            await asyncio.wait_for(entered_two.wait(), timeout=2)
            assert maximum == 2
        finally:
            release.set()
            await asyncio.gather(*tasks)
    assert fallback.await_count == 4
    assert len(semaphores) == 4
    assert all(semaphore is manager._work_unavailable_semaphore for semaphore in semaphores)
    assert maximum == 2
    assert active == 0


@pytest.mark.asyncio
async def test_other_channels_never_enter_the_work_unavailable_fallback(tmp_path):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    manager._get_client = Mock(return_value=object())
    manager._get_or_create_thread = AsyncMock(return_value=("thread", False))
    manager._update_thread_channel_metadata = AsyncMock()
    manager._handle_chat_on_thread = AsyncMock()
    msg = InboundMessage(channel_name="slack", chat_id="chat", user_id="sender", text="my calendar", owner_user_id="owner")
    with (
        patch("app.integrations.work.service.active_work_service", new_callable=AsyncMock) as active,
        patch("app.integrations.work.service.handle_unavailable_work", new_callable=AsyncMock) as fallback,
    ):
        await manager._handle_chat(msg, bound_identity_checked=True)
    active.assert_not_awaited()
    fallback.assert_not_awaited()
    assert manager._work_unavailable_semaphore is None
    manager._handle_chat_on_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_work_execution_failure_never_replays_through_another_handler(tmp_path):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    manager._get_client = Mock()
    msg = InboundMessage(channel_name="feishu", chat_id="chat", user_id="sender", text="create an event", owner_user_id="owner")
    service = SimpleNamespace(handle=AsyncMock(side_effect=RuntimeError("execution already started")))
    with (
        patch("app.integrations.work.service.active_work_service", AsyncMock(return_value=service)),
        patch("app.integrations.work.service.handle_unavailable_work", new_callable=AsyncMock) as fallback,
        pytest.raises(RuntimeError, match="execution already started"),
    ):
        await manager._handle_chat(msg, bound_identity_checked=True)
    fallback.assert_not_awaited()
    assert manager._work_unavailable_semaphore is None
    manager._get_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["feishu", "slack"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("available", [False, True])
async def test_general_feishu_turn_gets_only_current_server_capability_hint(tmp_path, channel, enabled, available):
    from app.channels.manager import ChannelManager
    from app.channels.message_bus import InboundMessage, MessageBus
    from app.channels.store import ChannelStore

    manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
    manager._get_client = Mock(return_value=object())
    manager._get_or_create_thread = AsyncMock(return_value=("thread", False))
    manager._update_thread_channel_metadata = AsyncMock()
    manager._handle_chat_on_thread = AsyncMock()
    msg = InboundMessage(channel_name=channel, chat_id="chat", user_id="sender", text="你能查询 Work 日历吗", owner_user_id="owner")
    service = SimpleNamespace(handle=AsyncMock(return_value=False)) if available else None
    extra = {"is_bootstrap": True, "work_capabilities": {"available": not available}}
    original = deepcopy(extra)
    with (
        patch("app.integrations.work.service.active_work_service", AsyncMock(return_value=service)),
        patch("app.integrations.work.service.handle_unavailable_work", AsyncMock(return_value=False)),
        patch("app.integrations.work.config.WorkSettings.from_env", return_value=SimpleNamespace(enabled=enabled)) as settings,
    ):
        await manager._handle_chat(msg, extra_context=extra, bound_identity_checked=True)
    passed = manager._handle_chat_on_thread.call_args.kwargs["extra_context"]
    assert extra == original
    if channel == "feishu":
        assert passed == ({"is_bootstrap": True, "work_capabilities": {"available": available}} if enabled else {"is_bootstrap": True})
        assert passed is not extra
    else:
        assert passed is extra
        settings.assert_not_called()


@pytest.fixture
def source_app_config():
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config

    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}))
    yield
    reset_app_config()


@pytest.fixture
def source_owner():
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    owner = SimpleNamespace(id="owner", system_role="user", oauth_provider=None, oauth_id=None)
    token = set_current_user(owner)
    try:
        yield owner
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_source,provider", [("session", "feishu"), (AUTH_SOURCE_INTERNAL, "feishu"), (AUTH_SOURCE_INTERNAL, "github"), (AUTH_SOURCE_INTERNAL, None)])
async def test_real_run_assembly_rechecks_source_on_a_feishu_origin_thread(source_app_config, source_owner, auth_source, provider):
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME
    from app.gateway.services import start_run
    from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
    from deerflow.runtime import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    owner = source_owner
    provider_service = SimpleNamespace(get_user=None)

    async def get_user(user_id):
        assert user_id == owner.id
        return owner

    provider_service.get_user = get_user
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("same-thread", user_id=owner.id, metadata={"channel_source": {"type": "im_channel", "provider": "feishu"}})
    checkpointer = InMemorySaver()
    historical = await checkpointer.aput(
        {"configurable": {"thread_id": "same-thread", "checkpoint_ns": ""}},
        empty_checkpoint(),
        {"source": "input", "channel_name": "feishu", "message_source": {"kind": "im", "provider": "feishu"}},
        {},
    )
    app_state = SimpleNamespace(
        stream_bridge=SimpleNamespace(),
        run_manager=RunManager(store=MemoryRunStore()),
        checkpointer=checkpointer,
        store=InMemoryStore(),
        run_event_store=SimpleNamespace(),
        run_events_config=None,
        thread_store=thread_store,
    )
    internal = auth_source == AUTH_SOURCE_INTERNAL
    req = SimpleNamespace(
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: owner.id} if internal else {},
        cookies={},
        state=SimpleNamespace(auth_source=auth_source, user=SimpleNamespace(id=owner.id, system_role="internal") if internal else owner),
        app=SimpleNamespace(state=app_state),
    )
    body = SimpleNamespace(
        assistant_id="lead_agent",
        input={"messages": [{"role": "human", "content": "new turn"}]},
        metadata={"channel_source": {"type": "im_channel", "provider": "feishu"}},
        config={"context": {"channel_name": "slack", "message_source": {"kind": "im", "provider": "slack"}, "channel_user_id": "forged", "work_capabilities": {"available": True}}},
        context={"channel_name": provider, "channel_user_id": "sender", "message_source": {"kind": "im", "provider": "feishu"}, "work_capabilities": {"available": False}},
        checkpoint=historical["configurable"],
        on_disconnect="cancel",
        multitask_strategy="reject",
        stream_mode=None,
        stream_subgraphs=False,
        interrupt_before=None,
        interrupt_after=None,
    )
    captured = {}

    async def capture_run(*args, **kwargs):
        captured.update(kwargs["config"])

    with (
        patch("app.gateway.services.get_local_provider", return_value=provider_service),
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=capture_run),
    ):
        record = await start_run(body, "same-thread", req)
        await record.task

    expected = {"kind": "im", "provider": provider} if internal and provider else {"kind": "internal" if internal else "web"}
    assert captured["context"]["message_source"] == expected
    assert captured["context"]["channel_name"] == (provider if internal else None)
    assert captured["context"]["user_id"] == owner.id
    assert captured["context"]["work_capabilities"] == ({"available": False} if internal and provider == "feishu" else None)
    assert not {"message_source", "channel_name", "channel_user_id", "work_capabilities"} & captured["configurable"].keys()
    assert (await thread_store.get("same-thread", user_id=owner.id))["metadata"]["channel_source"]["provider"] == "feishu"
