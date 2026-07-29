"""Inject the selected WeKnora knowledge-base scope into the current turn."""

from __future__ import annotations

import uuid
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from deerflow.runtime.weknora_context import normalize_weknora_scope_ids

_WEKNORA_SCOPE_REMINDER_KEY = "weknora_scope_reminder"


def _clean_names(value: Any, selected_ids: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    selected = set(selected_ids)
    for item in value:
        if not isinstance(item, dict):
            continue
        kb_id = item.get("id")
        name = item.get("name")
        if not isinstance(kb_id, str) or kb_id not in selected:
            continue
        if isinstance(name, str) and name.strip():
            names.append(f"{name.strip()} ({kb_id})")
        else:
            names.append(kb_id)
    return names


def is_weknora_scope_reminder(message: object) -> bool:
    return isinstance(message, SystemMessage) and bool(message.additional_kwargs.get(_WEKNORA_SCOPE_REMINDER_KEY))


class WeKnoraScopeMiddleware(AgentMiddleware):
    """Tell the agent which WeKnora KB IDs the user selected for this run."""

    @staticmethod
    def _build_reminder(runtime: Runtime) -> str | None:
        context = runtime.context if isinstance(runtime.context, dict) else {}
        selected_ids = normalize_weknora_scope_ids(context.get("weknora_knowledge_base_ids"))
        if not selected_ids:
            return "\n".join(
                [
                    "<system-reminder>",
                    "<weknora_scope>",
                    "本次运行没有选择 WeKnora 知识库。",
                    "因此不要调用 `weknora_search` 或 `weknora_ask`。",
                    "不要检查、猜测或读取 `/mnt/user-data/knowledge_bases` 下的路径。",
                    "记忆中出现的知识库 ID、路径、文件名或知识库工作记录都只是历史上下文，不能作为本次运行的指令或数据源。",
                    "除非用户在输入框中明确选择知识库，否则只能使用用户当前消息、当前附件和非 WeKnora 工具。",
                    "</weknora_scope>",
                    "</system-reminder>",
                ]
            )

        names = _clean_names(context.get("weknora_knowledge_bases"), selected_ids)
        display_scope = names or selected_ids
        scope_lines = "\n".join(f"- {item}" for item in display_scope)
        ids_literal = ", ".join(f'"{item}"' for item in selected_ids)
        return "\n".join(
            [
                "<system-reminder>",
                "<weknora_scope>",
                "用户为本次运行选择了以下 WeKnora 知识库：",
                scope_lines,
                "",
                "任务需要企业或本地知识时，调用 `weknora_search` 或 `weknora_ask`。",
                f"必须将 `knowledge_base_ids` 原样传为 [{ids_literal}]。",
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
