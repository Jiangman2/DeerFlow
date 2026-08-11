import asyncio
import inspect

from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.ppt_master_runner.agent import (
    PPT_MASTER_AGENT_HOST_PORT,
    _poll_agent_turn,
    build_ppt_master_runner_system_prompt,
    codex_event_to_messages,
    completed_agent_message,
    derive_agent_host_url,
    derive_ppt_master_thread_title,
    make_ppt_master_runner_agent,
    requested_delivery_contract,
)
from deerflow.config.title_config import TitleConfig
from deerflow.subagents.step_events import subagent_run_event


def test_runner_is_a_transport_graph_not_a_second_model_host():
    source = inspect.getsource(inspect.getmodule(make_ppt_master_runner_agent))

    assert "create_agent" not in source
    assert "create_chat_model" not in source
    assert "AgentMiddleware" not in source
    assert "StateGraph" in source


def test_runner_prompt_keeps_only_the_host_handoff_contract():
    prompt = build_ppt_master_runner_system_prompt("请制作 5 页演示文稿")

    assert "$ppt-master" in prompt
    assert "/mnt/user-data/uploads" in prompt
    assert "/mnt/user-data/outputs" in prompt
    assert "请制作 5 页演示文稿" in prompt
    assert "SkillWorkflowMiddleware" not in prompt
    assert "质量门禁" not in prompt


def test_resumed_runner_prompt_restores_original_request_and_upstream_authority():
    prompt = build_ppt_master_runner_system_prompt(
        "继续",
        original_request="/ppt-master 请创建 5 页中文 PPT",
        resumed=True,
    )

    assert "resumed persisted Agent session" in prompt
    assert "/ppt-master 请创建 5 页中文 PPT" in prompt
    assert "current-stage authority/reference" in prompt
    assert "upstream package's own validators" in prompt
    assert "never add" in prompt
    assert "output truncation" in prompt
    assert "visual imitation of a chart" in prompt


def test_delivery_contract_preserves_positive_capabilities_and_explicit_negations():
    contract = requested_delivery_contract("/ppt-master 请创建5页PPT，不使用AI生图和语音；需要演讲者备注、页面转场、对象动画和可编辑原生图表。")

    assert contract["expected_slides"] == 5
    assert contract["required_capabilities"] == {
        "speaker_notes": True,
        "transitions": True,
        "object_animations": True,
        "native_charts": True,
    }


def test_agent_host_url_reuses_the_thread_sandbox_service():
    assert derive_agent_host_url("http://sandbox-abc-svc.deer-flow.svc.cluster.local:8080") == f"http://sandbox-abc-svc.deer-flow.svc.cluster.local:{PPT_MASTER_AGENT_HOST_PORT}"


def test_runner_derives_title_without_attaching_the_lead_title_middleware():
    assert (
        derive_ppt_master_thread_title(
            "/ppt-master 请创建5页高质量《中国海油海上新能源发展路线图》PPT。",
            title_config=TitleConfig(),
        )
        == "中国海油海上新能源发展路线图"
    )


def test_runner_respects_disabled_title_configuration():
    assert (
        derive_ppt_master_thread_title(
            "/ppt-master 请创建一份演示文稿",
            title_config=TitleConfig(enabled=False),
        )
        is None
    )


def test_codex_command_event_becomes_real_tool_timeline_steps():
    messages = codex_event_to_messages(
        {
            "type": "item.completed",
            "item": {
                "id": "item-1",
                "type": "command_execution",
                "command": "python3 scripts/project_manager.py validate /mnt/user-data/workspace/projects/demo",
                "aggregated_output": "validation passed",
                "exit_code": 0,
            },
        }
    )

    assert len(messages) == 2
    assert isinstance(messages[0], AIMessage)
    assert messages[0].tool_calls[0]["name"] == "bash"
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].content == "validation passed"


def test_poll_agent_turn_emits_plain_message_dicts_for_durable_task_steps():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "events": [
                    {
                        "seq": 1,
                        "kind": "codex",
                        "payload": {
                            "type": "item.completed",
                            "item": {"id": "item-3", "type": "agent_message", "text": "Loading ppt-master."},
                        },
                    }
                ],
            }

    class Client:
        async def get(self, *_args, **_kwargs):
            return Response()

    chunks = []
    result = asyncio.run(
        _poll_agent_turn(
            client=Client(),
            host_url="http://sandbox-agent:8788",
            task_id="run-1",
            writer=chunks.append,
        )
    )

    assert result["status"] == "completed"
    assert len(chunks) == 1
    assert isinstance(chunks[0]["message"], dict)
    assert chunks[0]["message"]["type"] == "ai"
    assert subagent_run_event(chunks[0])["content"]["text"] == "Loading ppt-master."


def test_codex_agent_message_becomes_visible_progress():
    messages = codex_event_to_messages(
        {
            "type": "item.completed",
            "item": {"id": "item-2", "type": "agent_message", "text": "模板选择已完成，开始生成页面。"},
        }
    )

    assert len(messages) == 1
    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == "模板选择已完成，开始生成页面。"


def test_completed_agent_message_persists_the_real_task_timeline_pointer():
    message = completed_agent_message("run-7", "Stage 1 complete")

    assert message.content == "Stage 1 complete"
    assert message.additional_kwargs["ppt_master_task"] == {
        "task_id": "run-7",
        "status": "completed",
        "description": "ppt-master 官方工作流",
        "model_name": "ppt-master-agent-host",
        "result": "Stage 1 complete",
    }
