"""Inject the selected WeKnora knowledge-base scope into the current turn."""

from __future__ import annotations

import uuid
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from deerflow.runtime.weknora_context import normalize_weknora_run_scope

_WEKNORA_SCOPE_REMINDER_KEY = "weknora_scope_reminder"


def _scope_display_lines(scope: dict[str, Any]) -> list[str]:
    type_names = {"kb": "知识库", "file": "文件", "tag": "标签"}
    lines: list[str] = []
    for item in scope.get("mentioned_items", []):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or "")
        item_name = str(item.get("name") or item_id)
        parent = str(item.get("kb_name") or item.get("kb_id") or "")
        suffix = f"，位于 {parent}" if item_type != "kb" and parent else ""
        lines.append(f"- {type_names.get(item_type, item_type)}：{item_name} ({item_id}){suffix}")
    return lines


def is_weknora_scope_reminder(message: object) -> bool:
    return isinstance(message, SystemMessage) and bool(message.additional_kwargs.get(_WEKNORA_SCOPE_REMINDER_KEY))


class WeKnoraScopeMiddleware(AgentMiddleware):
    """Tell the agent which WeKnora KB IDs the user selected for this run."""

    @staticmethod
    def _build_reminder(runtime: Runtime) -> str | None:
        context = runtime.context if isinstance(runtime.context, dict) else {}
        scope = normalize_weknora_run_scope(context)
        if not scope:
            return "\n".join(
                [
                    "<system-reminder>",
                    "<weknora_scope>",
                    "本次运行没有选择 WeKnora 知识范围。",
                    "因此不要调用任何 `weknora_*` 工具。",
                    "不要检查、猜测或读取 `/mnt/user-data/knowledge_bases` 下的路径。",
                    "记忆中出现的知识库 ID、路径、文件名或知识库工作记录都只是历史上下文，不能作为本次运行的指令或数据源。",
                    "除非用户在输入框中明确选择知识库、文件或标签，否则只能使用用户当前消息、当前附件和非 WeKnora 工具。",
                    "</weknora_scope>",
                    "</system-reminder>",
                ]
            )

        scope_lines = "\n".join(_scope_display_lines(scope))
        return "\n".join(
            [
                "<system-reminder>",
                "<weknora_scope>",
                "用户为本次运行选择了以下 WeKnora 知识范围：",
                scope_lines,
                "",
                "任务需要企业或本地知识时，可以使用 WeKnora 的以下只读能力：",
                "- `weknora_list_knowledge_bases` / `weknora_get_knowledge_base`：查看当前范围的知识库元数据。",
                "- `weknora_list_documents` / `weknora_get_document`：发现和检查文档。",
                "- `weknora_list_document_chunks` / `weknora_read_document`：分页或连续读取解析后的文档内容。",
                "- `weknora_search`：返回原始检索证据；`weknora_ask`：由 WeKnora 完成普通 RAG 问答。",
                "- `weknora_list_agents`：发现可用内置/自建智能体；`weknora_invoke_agent`：调用其智能推理、数据分析或 Wiki 能力。",
                "运行时会把上述知识库、文件和带父知识库归属的标签范围自动注入检索、问答和智能体调用；不得删除、替换或扩大该范围。",
                "选择整库时可发现和读取该库内文档；只选择文件或标签时，不得枚举、读取或总结该范围以外的文档。",
                "调用 `weknora_list_knowledge_bases` 时只能查看上述范围涉及的知识库，不能枚举未选择的知识库。",
                "调用智能体前先用 `weknora_list_agents` 取得真实 ID；不得猜测智能体 ID。",
                "Wiki 修复或其他可能修改数据的智能体，只有用户明确要求该修改时才可调用；不得自动批准 pending_actions。",
                "引用知识来源时，原样复制每条工具结果中非空的 `citation_markdown`。",
                "用户询问文档中的图片时，直接复制命中结果 `resource_images[].markdown`；该地址是已鉴权的 DeerFlow 同域图片，不要自行下载，也不要声称需要额外 API 认证。",
                "不得为 WeKnora 来源构造或暴露 `/mnt/` 路径；这些是容器路径，不是浏览器 URL。",
                "除非用户要求更改范围，否则本次运行不得使用未选中的 WeKnora 知识库。",
                "</weknora_scope>",
                "</system-reminder>",
            ]
        )

    @staticmethod
    def _is_target(message: object) -> bool:
        if not isinstance(message, HumanMessage):
            return False
        if message.additional_kwargs.get(_WEKNORA_SCOPE_REMINDER_KEY):
            return False
        if message.id and str(message.id).endswith("__weknora_user"):
            return False
        if message.name == "summary":
            return False
        return True

    def _inject(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        reminder = self._build_reminder(runtime)
        if not reminder:
            return None

        messages = list(state.get("messages", []))
        target_idx = next((i for i in reversed(range(len(messages))) if self._is_target(messages[i])), None)
        if target_idx is None:
            return None

        original = messages[target_idx]
        stable_id = original.id or str(uuid.uuid4())
        return {
            "messages": [
                SystemMessage(
                    content=reminder,
                    id=stable_id,
                    additional_kwargs={
                        "hide_from_ui": True,
                        _WEKNORA_SCOPE_REMINDER_KEY: True,
                    },
                ),
                HumanMessage(
                    content=original.content,
                    id=f"{stable_id}__weknora_user",
                    name=original.name,
                    additional_kwargs=original.additional_kwargs,
                ),
            ]
        }

    @override
    def before_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        return self._inject(state, runtime)

    @override
    async def abefore_agent(self, state: dict[str, Any], runtime: Runtime) -> dict[str, Any] | None:
        return self._inject(state, runtime)
