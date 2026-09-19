"""Run-scoped metadata for content-free LLM timing logs.

The model adapter is intentionally below the Gateway and LangGraph layers, so
it cannot otherwise associate a provider call with the DeerFlow run that owns
it.  :class:`contextvars.ContextVar` keeps this diagnostic metadata isolated
per async task, including when multiple Gateway workers serve concurrent runs.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Final


_MAX_TIMING_IDENTIFIER_LENGTH: Final[int] = 512


@dataclass(frozen=True, slots=True)
class LLMTimingContext:
    """Identifiers safe to include in opt-in model timing records."""

    thread_id: str
    run_id: str


_CURRENT_LLM_TIMING_CONTEXT: ContextVar[LLMTimingContext | None] = ContextVar(
    "deerflow_llm_timing_context",
    default=None,
)


def _normalize_timing_identifier(value: object) -> str:
    """Return a bounded, log-safe run identifier or the unavailable sentinel."""

    if not isinstance(value, str):
        return "-"
    identifier = value.strip()
    if not identifier or len(identifier) > _MAX_TIMING_IDENTIFIER_LENGTH:
        return "-"
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in identifier):
        return "-"
    return identifier


def set_current_llm_timing_context(*, thread_id: str, run_id: str) -> Token[LLMTimingContext | None]:
    """Bind one DeerFlow run to model logs in the current async context."""

    return _CURRENT_LLM_TIMING_CONTEXT.set(
        LLMTimingContext(
            thread_id=_normalize_timing_identifier(thread_id),
            run_id=_normalize_timing_identifier(run_id),
        )
    )


def get_current_llm_timing_context() -> LLMTimingContext | None:
    """Return the current run's timing context, if the call belongs to one."""

    return _CURRENT_LLM_TIMING_CONTEXT.get()


def reset_current_llm_timing_context(token: Token[LLMTimingContext | None]) -> None:
    """Restore the caller's previous timing context."""

    _CURRENT_LLM_TIMING_CONTEXT.reset(token)
