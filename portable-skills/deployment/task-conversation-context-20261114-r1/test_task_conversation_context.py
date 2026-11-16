"""Real complete tools module; mock only transport/config, never the prefix."""

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.sandbox import tools
from deerflow.utils import conversation_url

URL = "https://cnoocclaw.cnooc.com.cn/workspace/chats/thread-current"
ENV = "DEERFLOW_CONVERSATION_URL"


def runtime(context=None, config=None, *, local=False):
    return SimpleNamespace(
        context=context,
        config=config,
        state={"sandbox": {"sandbox_id": "local" if local else "aio-test"}, "thread_data": {
            "workspace_path": "/tmp/thread/workspace", "uploads_path": "/tmp/thread/uploads", "outputs_path": "/tmp/thread/outputs",
        }},
    )


@pytest.fixture(autouse=True)
def stable_origin(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_CONVERSATION_WEB_URL", "https://cnoocclaw.cnooc.com.cn")
    monkeypatch.setenv("DEER_FLOW_WORK_PROFILE", "production")
    monkeypatch.setattr(tools, "get_config", lambda: {})


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def execute(command, env=None, timeout=None):
        calls.append({"command": command, "env": env, "timeout": timeout})
        return "ok"

    sandbox = SimpleNamespace(execute_command=execute)
    monkeypatch.setattr(tools, "ensure_sandbox_initialized", lambda runtime: sandbox)

    async def initialize(runtime):
        return sandbox

    monkeypatch.setattr(tools, "ensure_sandbox_initialized_async", initialize)
    monkeypatch.setattr(tools, "ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr(tools, "is_host_bash_allowed", lambda: True)
    monkeypatch.setattr(tools, "_is_windows", lambda: False)
    from deerflow.config import app_config
    monkeypatch.setattr(app_config, "get_app_config", lambda: SimpleNamespace(sandbox=None))
    return calls


@pytest.mark.parametrize("context,config,ambient,expected", [
    ({"thread_id": "thread-current"}, {"configurable": {"thread_id": "other"}}, {"configurable": {"thread_id": "ambient"}}, "thread-current"),
    ({}, {"configurable": {"thread_id": "thread-config"}}, {"configurable": {"thread_id": "ambient"}}, "thread-config"),
    (None, None, {"configurable": {"thread_id": "thread-ambient"}}, "thread-ambient"),
    ({"thread_id": ""}, {"configurable": {"thread_id": "thread-config"}}, {}, "thread-config"),
    (None, {}, {}, None),
    ({"thread_id": "../other"}, {"configurable": {"thread_id": "valid-other"}}, {}, None),
    ({"thread_id": "x; printf injected"}, None, {}, None),
    ({"thread_id": 123}, None, {}, None),
    ({"thread_id": "x" * 65}, None, {}, None),
])
def test_identity_precedence_matches_present_files(monkeypatch, context, config, ambient, expected):
    monkeypatch.setattr(tools, "get_config", lambda: ambient)
    prefix = tools._conversation_context_prefix(runtime(context, config))
    if expected:
        assert prefix == f"export {ENV}=https://cnoocclaw.cnooc.com.cn/workspace/chats/{expected}; "
    else:
        assert prefix == f"unset {ENV}; "


@pytest.mark.parametrize("origin", ["", "http://external.example", "https://example.com/drive/home", "https://user:password@example.com", "https://example.com?q=1", "https://example.com#fragment"])
def test_invalid_operator_origin_clears_stale_context(monkeypatch, origin):
    monkeypatch.setenv("DEER_FLOW_CONVERSATION_WEB_URL", origin)
    assert tools._conversation_context_prefix(runtime({"thread_id": "thread-current"})) == f"unset {ENV}; "


def test_current_task_ignores_model_supplied_url_and_inherited_env(monkeypatch):
    monkeypatch.setenv(ENV, "https://wrong.example/workspace/chats/wrong")
    task = runtime({"thread_id": "thread-current", "conversation_url": "https://wrong.example/drive/home", ENV: "https://wrong.example"})
    assert tools._conversation_context_prefix(task) == f"export {ENV}={URL}; "


def test_no_runtime_config_context_clears_env(monkeypatch):
    def unavailable():
        raise RuntimeError("outside runtime")
    monkeypatch.setattr(tools, "get_config", unavailable)
    assert tools._conversation_context_prefix(runtime()) == f"unset {ENV}; "


def test_optional_context_failure_does_not_disclose_error(monkeypatch, capture):
    def broken(_):
        raise ValueError("private configuration value")
    monkeypatch.setattr(tools, "current_conversation_url", broken)
    assert tools.bash_tool.func(runtime({"thread_id": "thread-current"}), "echo safe") == "ok"
    assert capture[0]["command"] == f"unset {ENV}; cd /mnt/user-data/workspace; echo safe"


@pytest.mark.parametrize("local", [False, True])
def test_posix_paths_share_prefix_without_env_transport_change(capture, local):
    assert tools.bash_tool.func(runtime({"thread_id": "thread-current"}, local=local), "echo safe") == "ok"
    call = capture[0]
    assert call["command"].startswith(f"export {ENV}={URL}; cd ")
    assert call["command"].endswith("echo safe")
    assert call["env"] is None


def test_local_windows_transport_stays_unchanged(monkeypatch, capture):
    monkeypatch.setattr(tools, "_is_windows", lambda: True)
    tools.bash_tool.func(runtime({"thread_id": "thread-current", "channel_user_id": "sender"}, local=True), "echo safe")
    assert capture[0]["command"] == "cd /tmp/thread/workspace && echo safe"
    assert capture[0]["env"] is None
    assert ENV not in capture[0]["command"]


@pytest.mark.parametrize("local", [False, True])
def test_active_secrets_github_and_channel_identity_preserved(monkeypatch, capture, local):
    task = runtime({"thread_id": "thread-current", "channel_user_id": "sender-1", "__active_skill_secrets": {"TEST_KEY": "private-test-value"}, "github_token": "private-github-value"}, local=local)
    tools.bash_tool.func(task, "echo safe")
    call = capture[0]
    assert call["env"] == {"TEST_KEY": "private-test-value", "GH_TOKEN": "private-github-value", "GITHUB_TOKEN": "private-github-value"}
    assert call["command"].startswith(f"export DEERFLOW_CHANNEL_USER_ID=sender-1; export {ENV}={URL}; ")
    assert "private-test-value" not in call["command"]
    assert "private-github-value" not in call["command"]


@pytest.mark.parametrize("local", [False, True])
def test_async_tool_uses_same_sync_implementation(capture, local):
    task = runtime({"thread_id": "thread-current"}, local=local)
    sync = tools.bash_tool.func(task, "echo safe", "test")
    asynchronous = asyncio.run(tools.bash_tool.coroutine(task, "echo safe", "test"))
    assert sync == asynchronous == "ok"
    assert capture[0] == capture[1]


def posix_shell():
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    shell = str(git_bash) if git_bash.is_file() else shutil.which("bash") or shutil.which("sh")
    if not shell:
        pytest.skip("POSIX shell unavailable")
    return shell


def shell_value(prefix):
    command = f"export {ENV}=stale-previous-thread; " + prefix + f"printf '%s' \"${{{ENV}-UNSET}}\""
    completed = subprocess.run([posix_shell(), "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
    return completed.stdout.decode("utf-8")


@pytest.mark.parametrize("context,expected", [({"thread_id": "thread-current"}, URL), ({}, "UNSET"), ({"thread_id": "../other"}, "UNSET")])
def test_real_posix_shell_overwrites_or_unsets_stale_value(context, expected):
    assert shell_value(tools._conversation_context_prefix(runtime(context))) == expected


def test_quoting_keeps_shell_metacharacters_data(monkeypatch):
    value = "https://operator.example/'quoted'; printf INJECTED; $(printf injected)"
    monkeypatch.setattr(tools, "current_conversation_url", lambda _: value)
    prefix = tools._conversation_context_prefix(runtime({"thread_id": "thread-current"}))
    assert prefix == f"export {ENV}={shlex.quote(value)}; "
    assert shell_value(prefix) == value


def test_early_context_does_not_require_file_publication(capture):
    task = runtime({"thread_id": "thread-current"})
    assert "messages" not in task.state
    tools.bash_tool.func(task, "python daily.py build")
    assert f"{ENV}={URL}" in capture[0]["command"]


def test_host_configuration_fallback_is_preserved(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_CONVERSATION_WEB_URL")
    auth = SimpleNamespace(iam=SimpleNamespace(enabled=True, frontend_base_url="https://configured.example"), oidc=SimpleNamespace(enabled=False))
    monkeypatch.setattr(conversation_url, "get_app_config", lambda: SimpleNamespace(auth=auth))
    assert tools._conversation_context_prefix(runtime({"thread_id": "thread-current"})) == f"export {ENV}=https://configured.example/workspace/chats/thread-current; "


def test_signed_or_other_conversation_input_is_not_accepted(capture):
    tools.bash_tool.func(runtime({"thread_id": "thread-current", "conversation_url": "https://cnoocclaw.cnooc.com.cn/workspace/chats/other?token=private"}), "echo safe")
    assert "token=" not in capture[0]["command"]
    assert "chats/other" not in capture[0]["command"]


@pytest.mark.parametrize("thread", ["thread-current", "8e27d29f-730b-4a89-9681-36fdddc87887", None])
def test_real_shell_context_is_consumed_by_skill_212(thread):
    scripts = Path(__file__).resolve().parents[2] / "candidates/energy-market-daily-2.1.2/scripts"
    assert (scripts / "delivery_text.py").is_file()
    child = f"import sys,json;sys.path.insert(0,{scripts.as_posix()!r});from delivery_text import host_conversation_context;print(json.dumps(host_conversation_context(),sort_keys=True))"
    command = f"export {ENV}=https://wrong.example/workspace/chats/stale; "
    command += tools._conversation_context_prefix(runtime({"thread_id": thread}))
    command += " ".join(shlex.quote(arg) for arg in (Path(sys.executable).as_posix(), "-I", "-B", "-c", child))
    completed = subprocess.run([posix_shell(), "-c", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
    result = json.loads(completed.stdout)
    if thread:
        assert result["conversation_url"] == f"https://cnoocclaw.cnooc.com.cn/workspace/chats/{thread}"
        assert result["conversation_thread_id"] == thread
        assert result["conversation_link_available"] is True
        assert result["conversation_context_source"] == "HOST_INJECTED_ENVIRONMENT"
        assert result["conversation_context_error"] is None
    else:
        assert result["conversation_url"] is None
        assert result["conversation_thread_id"] is None
        assert result["conversation_link_available"] is False
        assert result["conversation_context_error"] == "HOST_CONVERSATION_CONTEXT_UNAVAILABLE"
