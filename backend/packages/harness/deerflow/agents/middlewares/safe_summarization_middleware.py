import asyncio
import logging
import time
from typing import Any

from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


class SafeSummarizationMiddleware(SummarizationMiddleware):
    @staticmethod
    def _has_chinese(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    def _build_new_messages(self, summary: str) -> list[HumanMessage]:
        if self._has_chinese(summary):
            prefix = "以下是截至目前的对话总结：\n\n"
        else:
            prefix = "Here is a summary of the conversation to date:\n\n"
        return [HumanMessage(content=f"{prefix}{summary}")]

    @staticmethod
    def _looks_like_failed_summary(patch: dict[str, Any] | None) -> bool:
        if not isinstance(patch, dict):
            return False
        messages = patch.get("messages")
        if not isinstance(messages, list):
            return False
        for m in messages:
            content = getattr(m, "content", None)
            if isinstance(content, str) and "Error generating summary:" in content:
                return True
        return False

    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        for attempt in range(3):
            try:
                patch = super().before_model(state, runtime)
                if self._looks_like_failed_summary(patch):
                    logger.warning("Summarization produced an error message; skipping summarization.")
                    return None
                return patch
            except Exception:
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                logger.warning("Summarization failed; continuing without summarizing.", exc_info=True)
                return None

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                patch = await super().abefore_model(state, runtime)
                if self._looks_like_failed_summary(patch):
                    logger.warning("Summarization produced an error message; skipping summarization.")
                    return None
                return patch
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                logger.warning("Summarization failed; continuing without summarizing.", exc_info=True)
                return None
