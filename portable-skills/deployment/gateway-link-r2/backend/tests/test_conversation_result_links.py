"""Current-thread links are host-owned and require successful file publication."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels import manager as channel_manager
from app.channels.message_bus import InboundMessage, MessageBus, ResolvedAttachment
from app.channels.store import ChannelStore
from deerflow.utils import conversation_url as links

present_files = importlib.import_module("deerflow.tools.builtins.present_file_tool")
ORIGIN = "https://portal.example.com"
THREAD = "current-thread_123"
URL = f"{ORIGIN}/workspace/chats/{THREAD}"
VIRTUAL_FILE = "/mnt/user-data/outputs/report.png"


def config(*, iam=None, oidc=None):
    return SimpleNamespace(
        auth=SimpleNamespace(
            iam=SimpleNamespace(enabled=iam is not None, frontend_base_url=iam),
            oidc=SimpleNamespace(enabled=oidc is not None, frontend_base_url=oidc),
        )
    )


@pytest.fixture(autouse=True)
def configured_origin(monkeypatch):
    monkeypatch.setattr(links, "get_app_config", lambda: config(iam=ORIGIN))
    monkeypatch.delenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL", raising=False)
    monkeypatch.delenv("DEER_FLOW_WORK_PROFILE", raising=False)


@pytest.mark.parametrize(
    "origin",
    [
        "",
        "http://portal.example.com",
        "https://name:secret@portal.example.com",
        "https://portal.example.com/path",
        "https://portal.example.com/?token=secret",
        "https://portal.example.com/#fragment",
        "https://portal.example.com:bad",
        "https://portal.example.com:65536",
        "https://[invalid",
        "https://portal.example.com\\other",
        "https://portal.example.com\n.invalid",
        "//portal.example.com",
    ],
)
def test_origin_rejects_unsafe_or_non_origin_values(origin):
    assert links.validated_web_origin(origin) == ""


def test_origin_reuses_uat_loopback_exception_only():
    assert links.validated_web_origin("http://localhost:3000", profile="uat") == "http://localhost:3000"
    assert links.validated_web_origin("http://localhost:3000") == ""
    assert links.validated_web_origin("http://other.example.com", profile="uat") == ""


@pytest.mark.parametrize("thread", [None, "", "../other", "other?admin_review=1", "x" * 65, "中文"])
def test_invalid_thread_never_creates_a_link(thread):
    assert links.current_conversation_url(thread) == ""


def test_reuses_existing_frontend_settings_and_notification_override(monkeypatch):
    assert links.current_conversation_url(THREAD) == URL
    monkeypatch.setattr(links, "get_app_config", lambda: config(oidc=ORIGIN + "/"))
    assert links.current_conversation_url(THREAD) == URL
    monkeypatch.setattr(links, "get_app_config", lambda: config())
    assert links.current_conversation_url(THREAD) == ""  # No production hostname guessed.
    monkeypatch.setenv("DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL", ORIGIN)
    assert links.current_conversation_url(THREAD) == URL
    monkeypatch.setattr(links, "get_app_config", lambda: config(iam="https://invalid.example/path"))
    assert links.current_conversation_url(THREAD) == ""


def test_configuration_failure_does_not_break_publication(monkeypatch):
    def broken():
        raise ValueError("private configuration")

    monkeypatch.setattr(links, "get_app_config", broken)
    assert links.current_conversation_url(THREAD) == ""


def runtime_for(tmp_path, thread=THREAD):
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    file = outputs / "report.txt"
    file.write_text("report", encoding="utf-8")
    runtime = SimpleNamespace(state={"thread_data": {"outputs_path": str(outputs), "workspace_path": str(tmp_path / "workspace")}}, context={"thread_id": thread}, config={})
    return runtime, file


def test_present_files_returns_host_link_in_content_and_artifact(tmp_path):
    runtime, file = runtime_for(tmp_path)
    result = present_files.present_file_tool.func(runtime=runtime, filepaths=[str(file)], tool_call_id="tc-1")
    message = result.update["messages"][0]
    assert message.content == f"Successfully presented files\nconversation_url: {URL}"
    assert message.artifact["conversation_url"] == URL
    assert message.artifact["presented_files"] == ["/mnt/user-data/outputs/report.txt"]
    assert "conversation_url" not in present_files.present_file_tool.args
    assert "thread_id" not in present_files.present_file_tool.args


def test_present_files_uses_runtime_config_not_file_name(tmp_path):
    runtime, file = runtime_for(tmp_path)
    runtime.context = {}
    runtime.config = {"configurable": {"thread_id": THREAD}}
    result = present_files.present_file_tool.func(runtime=runtime, filepaths=[str(file)], tool_call_id="tc-config")
    assert result.update["messages"][0].artifact["conversation_url"] == URL


def test_present_files_without_origin_keeps_original_success_response(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "get_app_config", lambda: config())
    runtime, file = runtime_for(tmp_path)
    result = present_files.present_file_tool.func(runtime=runtime, filepaths=[str(file)], tool_call_id="tc-no-origin")
    message = result.update["messages"][0]
    assert message.content == "Successfully presented files"
    assert "conversation_url" not in message.artifact


def test_failed_present_files_link_explicitly_does_not_claim_delivery(tmp_path):
    runtime, file = runtime_for(tmp_path)
    result = present_files.present_file_tool.func(runtime=runtime, filepaths=[str(file) + ".missing"], tool_call_id="tc-failed")
    message = result.update["messages"][0]
    assert message.status == "error"
    assert "File delivery incomplete" in message.content
    assert message.artifact["publication_status"] == "failed"
    assert message.artifact["presented_files"] == []
    assert message.artifact["conversation_url"] == URL


def publication_messages():
    return [
        {"type": "human", "content": "生成日报"},
        {"type": "ai", "content": "", "tool_calls": [{"name": "present_files", "args": {"filepaths": [VIRTUAL_FILE]}}]},
        {"type": "tool", "name": "present_files", "status": "success", "content": "Successfully presented files", "artifact": {"presented_files": [VIRTUAL_FILE], "conversation_url": "https://untrusted.example/wrong-thread"}},
        {"type": "ai", "content": "日报已生成。"},
    ]


def attachment(tmp_path):
    file = tmp_path / "report.png"
    file.write_bytes(b"report")
    return ResolvedAttachment(VIRTUAL_FILE, file, "report.png", "image/png", 6, True)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("workspace_source", [False, True])
@pytest.mark.asyncio
async def test_normal_and_streaming_work_final_replies_append_host_link(tmp_path, monkeypatch, streaming, workspace_source):
    bus = MessageBus()
    bus.publish_outbound = AsyncMock()
    manager = channel_manager.ChannelManager(bus=bus, store=ChannelStore(path=tmp_path / "store.json"))
    monkeypatch.setattr(manager, "_resolve_run_params", lambda *args: ("lead_agent", {}, {}))
    monkeypatch.setattr(manager, "_apply_channel_policy", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_channel_supports_streaming", lambda name: streaming)
    owner_reads = []

    def resolve(thread_id, paths, *, user_id=None):
        owner_reads.append((thread_id, user_id))
        return [attachment(tmp_path)] if paths == [VIRTUAL_FILE] else []

    monkeypatch.setattr(channel_manager, "_resolve_attachments", resolve)
    monkeypatch.setattr(channel_manager, "STREAM_UPDATE_MIN_INTERVAL_SECONDS", 0)
    result = {"messages": publication_messages()}
    if workspace_source:
        result["messages"][1]["tool_calls"][0]["args"]["filepaths"] = ["/mnt/user-data/workspace/deliveries/report.png"]
    client = MagicMock()
    client.runs.wait = AsyncMock(return_value=result)

    async def stream():
        yield SimpleNamespace(event="messages", data=[{"id": "ai-1", "type": "AIMessageChunk", "content": "正在生成"}, {"langgraph_node": "agent"}])
        yield SimpleNamespace(event="values", data=result)

    client.runs.stream = MagicMock(return_value=stream()) if streaming else MagicMock()
    inbound = InboundMessage(channel_name="feishu", chat_id="work-chat", user_id="platform-user", text="生成日报")
    await manager._handle_chat_on_thread(client, inbound, THREAD, storage_user_id="owner")
    replies = [call.args[0] for call in bus.publish_outbound.call_args_list]
    assert replies[-1].is_final
    assert replies[-1].text.endswith(f"查看完整结果：{URL}")
    assert replies[-1].text.count(URL) == 1
    assert "untrusted.example" not in replies[-1].text
    assert all(URL not in reply.text for reply in replies if not reply.is_final)
    assert owner_reads and all(identity == (THREAD, "owner") for identity in owner_reads)


@pytest.mark.parametrize("case", ["failed", "old_turn", "missing_receipt", "missing_file", "other_channel"])
def test_footer_requires_current_successful_published_file(tmp_path, monkeypatch, case):
    messages = publication_messages()
    files = [attachment(tmp_path)]
    channel = "feishu"
    monkeypatch.setattr(channel_manager, "_resolve_attachments", lambda *args, **kwargs: [])
    if case == "failed":
        messages[2]["status"] = "error"
    elif case == "old_turn":
        messages.append({"type": "human", "content": "新问题"})
    elif case == "missing_receipt":
        messages.pop(2)
    elif case == "missing_file":
        files = []
    elif case == "other_channel":
        channel = "slack"
    assert channel_manager._append_presented_conversation_link("原回复", channel_name=channel, thread_id=THREAD, result={"messages": messages}, attachments=files) == "原回复"


@pytest.mark.parametrize("text", [f"查看完整结果：{URL}", f"日报已生成：[查看报告]({URL})", f"{URL}。"])
def test_exact_existing_link_is_not_duplicated(text):
    result = links.append_conversation_link(text, URL)
    assert result.count(URL) == 1
    assert result.endswith(f"查看完整结果：{URL}")
    assert links.append_conversation_link(result, URL) == result


@pytest.mark.parametrize("existing", [URL, f"<{URL}>", f"[查看报告]({URL})", f"查看完整结果：{URL}"])
def test_existing_link_is_moved_after_all_text(existing):
    result = links.append_conversation_link(f"日报已生成。\n\n{existing}\n\n行情有缺口，已在图中标注。", URL)
    assert result.count(URL) == 1
    assert result.endswith(f"行情有缺口，已在图中标注。\n\n查看完整结果：{URL}")


def test_different_thread_url_does_not_suppress_current_link():
    text = URL + "-other"
    assert links.append_conversation_link(text, URL).endswith(f"查看完整结果：{URL}")


def test_daily_footer_replaces_claimed_drive_and_id_but_keeps_news():
    text = "日报。\n会话ID：old-id\n平台入口：https://wrong.example/drive/home/\n新闻原文：https://oilprice.com/story.html"
    expected = f"[点击查看完整日报]({URL})"
    actual = links.append_conversation_link(text, URL, daily=True)
    assert actual.endswith(expected)
    assert "old-id" not in actual and "wrong.example" not in actual
    assert "https://oilprice.com/story.html" in actual
    assert links.append_conversation_link(actual, URL, daily=True) == actual


def test_failed_footer_does_not_offer_complete_daily():
    actual = links.append_conversation_link("文件未交付", URL, delivery_failed=True)
    assert actual.endswith(f"文件交付未完成。[查看本次对话]({URL})")
    assert "点击查看完整日报" not in actual
    assert links.append_conversation_link(actual, URL, delivery_failed=True) == actual


def test_daily_label_requires_all_four_published_files():
    paths = [f"/mnt/user-data/outputs/油气与金融市场日报_2026-11-11.{ext}" for ext in ("png", "html", "json", "txt")]
    assert links.energy_daily_files(paths)
    assert not links.energy_daily_files(paths[:-1])
    assert not links.energy_daily_files([p.replace("2026-11-11", "other") for p in paths])


def test_work_uses_daily_markdown_link_from_host_not_model(tmp_path, monkeypatch):
    paths = [f"/mnt/user-data/outputs/油气与金融市场日报_2026-11-11.{ext}" for ext in ("png", "html", "json", "txt")]
    messages = publication_messages()
    messages[2]["artifact"]["presented_files"] = paths
    files = []
    for path in paths:
        files.append(ResolvedAttachment(path, tmp_path / path.rsplit("/", 1)[-1], path.rsplit("/", 1)[-1], "text/plain", 1, True))
    monkeypatch.setattr(channel_manager, "_resolve_attachments", lambda *args, **kwargs: [])
    text = channel_manager._append_presented_conversation_link("日报正文", channel_name="feishu", thread_id=THREAD, result={"messages": messages}, attachments=files)
    assert text.endswith(f"[点击查看完整日报]({URL})")


def test_scheduler_daily_keeps_only_public_news_urls_and_uses_host_link():
    from app.scheduler import work_notification_delivery as delivery
    from app.scheduler.work_notifications import _notification_summary

    body = "油气与金融市场日报\n1. [真实新闻](https://oilprice.com/real.html)\n其他：https://unsafe.example/private?token=private"
    notification = SimpleNamespace(title="日报", result_text=body, finished_at=None, thread_id=THREAD)
    text = delivery._message(notification, ORIGIN)
    assert "[真实新闻](https://oilprice.com/real.html)" in text
    assert "unsafe.example" not in text and "token=private" not in text
    assert text.endswith(f"[点击查看完整日报]({URL})")
    assert _notification_summary(body) == body
    clipped = _notification_summary("油气与金融市场日报\n" + "一行很长的中文\n" * 2000)
    assert len(clipped.encode()) <= 9000
    assert clipped.endswith("正文较长，完整内容请点击日报链接查看。")
    assert _notification_summary("ordinary" * 200) == ("ordinary" * 200)[:1200]


def test_scheduler_origin_and_host_footer_behavior_are_preserved(monkeypatch):
    from app.scheduler import work_notification_delivery as delivery

    assert delivery._platform_origin("production") == "https://cnoocclaw.cnooc.com.cn"
    assert delivery._platform_origin("uat") == ""
    notification = SimpleNamespace(title="日报", result_text=f"已完成。{URL}", finished_at=None, thread_id=THREAD)
    text = delivery._message(notification, ORIGIN)
    assert text.endswith(f"查看完整结果：{URL}")
    assert text.count(URL) == 1
    assert "[链接见平台]" in text
