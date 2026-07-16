"""Inject the selected WeKnora knowledge-base scope into the current turn."""

from __future__ import annotations

import uuid
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

_WEKNORA_SCOPE_REMINDER_KEY = "weknora_scope_reminder"


def _clean_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped and stripped not in cleaned:
            cleaned.append(stripped)
    return cleaned


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
        selected_ids = _clean_ids(context.get("weknora_knowledge_base_ids"))
        if not selected_ids:
            return None

        names = _clean_names(context.get("weknora_knowledge_bases"), selected_ids)
        display_scope = names or selected_ids
        scope_lines = "\n".join(f"- {item}" for item in display_scope)
        ids_literal = ", ".join(f'"{item}"' for item in selected_ids)
        return "\n".join(
            [
                "<system-reminder>",
                "<weknora_scope>",
                "The user selected these WeKnora knowledge bases for this run:",
                scope_lines,
                "",
                "When the task needs enterprise/local knowledge, call `weknora_search` or `weknora_ask`.",
                f"Pass `knowledge_base_ids` exactly as [{ids_literal}].",
                "Do not use unselected WeKnora knowledge bases for this run unless the user asks you to change scope.",
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
