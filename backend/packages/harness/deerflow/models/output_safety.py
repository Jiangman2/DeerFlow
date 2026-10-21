"""Bound pathological model output without interpreting or repairing tool arguments."""

from __future__ import annotations

from typing import Any


def visible_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block if isinstance(block, str) else block.get("text", "") for block in content if isinstance(block, str) or (isinstance(block, dict) and block.get("type") in {"text", "output_text"} and isinstance(block.get("text"), str))
    )


def has_repetitive_tail(text: str) -> bool:
    """Require >= 2K characters and >= 8 exact cycles; inspect a bounded suffix.

    This deliberately detects only severe exact repetition, not semantic
    similarity. Short refrains, lists, and ordinary repeated headings remain
    valid. Tool argument strings never enter this detector.
    """
    tail = text[-8192:]
    if len(tail) < 2048:
        return False
    for period in range(8, min(1024, len(tail) // 8) + 1):
        cycles = max(8, (2048 + period - 1) // period)
        if cycles * period > len(tail):
            continue
        unit = tail[-period:]
        if any(char.isalpha() for char in unit) and tail[-period * cycles :] == unit * cycles:
            return True
    return False


OUTPUT_STOP_MESSAGES = {
    "incomplete_glm_stream": "GLM 的流式响应未完整结束，已停止本轮任务，未自动重试。已显示的内容可能不完整，请核对后重新发起。",
    "repetitive_output": "检测到模型持续重复输出，已停止本轮任务，未自动重试。请缩小任务范围或切换模型后继续。",
    "reasoning_budget_exceeded": "模型的推理输出已达到本次安全预算，已停止本轮任务，未自动重试。请拆分任务或调整模型的思考设置后继续。",
    "reasoning_output_limit": "模型耗尽输出额度时仍未给出可见答案，已停止本轮任务，未自动重试。请拆分任务或调整模型的思考设置后继续。",
    "output_length_limit": "模型输出因长度限制被截断，已停止本轮任务，未自动重试。请缩小单次输出范围后继续。",
}
PARTIAL_WORK_NOTICE = "此前成功的工具操作不会自动回滚，请核对已有结果；本轮未确认任务完成。"


class ModelOutputSafetyError(RuntimeError):
    """Non-retriable local stop; never include model text or arguments."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ModelOutputGuard:
    """Per-invocation guard, with bounded memory independent of response length.

    The reasoning budget is Unicode *characters*, not tokens. It is a local
    cost/latency safety limit and does not change the provider's token budget.
    """

    def __init__(self, *, repetition_enabled: bool = True, max_reasoning_chars: int = 65536):
        self.repetition_enabled = repetition_enabled
        self.max_reasoning_chars = max_reasoning_chars
        self.reasoning_chars = 0
        self.tail = ""
        self._unchecked_chars = 0

    def observe(self, message: Any) -> None:
        reasoning = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
        if isinstance(reasoning, str):
            self.reasoning_chars += len(reasoning)
        if self.max_reasoning_chars and self.reasoning_chars >= self.max_reasoning_chars:
            raise ModelOutputSafetyError("reasoning_budget_exceeded")
        if not self.repetition_enabled:
            return
        text = visible_text(message.content)
        self.tail = (self.tail + text)[-8192:]
        self._unchecked_chars += len(text)
        if self._unchecked_chars >= 512:
            self._unchecked_chars = 0
            if has_repetitive_tail(self.tail):
                raise ModelOutputSafetyError("repetitive_output")
