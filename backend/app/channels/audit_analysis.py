"""Ask a model to explain *why* the failures in an audit digest happened.

This is a plain one-shot completion, not an agent run. Two consequences are
deliberate:

* Nothing is persisted as a thread, so an ops question never shows up in the
  user's chat list and never consumes the global run budget.
* The model sees only the evidence we hand it. It cannot call tools, read logs,
  or grep the repo — so the prompt has to be blunt about not inventing causes it
  cannot see, and about saying "证据不足" when the sample is too thin.

Run-name choice matters for tracing: it becomes the Langfuse ``assistant_id``, so
keeping it distinct (``channel_audit_analysis``) keeps audit analysis out of the
input-polish / suggestions latency dashboards.
"""

from __future__ import annotations

import logging
from typing import Any

import deerflow.utils.llm_text as llm_text
from deerflow.config.app_config import AppConfig
from deerflow.utils.oneshot_llm import run_oneshot_llm

logger = logging.getLogger(__name__)

#: Langfuse assistant_id / LangChain run_name for the one-shot call.
AUDIT_ANALYSIS_RUN_NAME = "channel_audit_analysis"

#: Hard ceiling on evidence handed to the model (characters, not tokens).
MAX_INPUT_CHARS = 20000

SYSTEM_INSTRUCTION = (
    "你是企业 IM 机器人平台的运维分析助手，负责根据审计日志摘要定位故障原因。\n"
    "你只能看到下面提供的摘要与失败样本，看不到完整日志、代码仓库或服务器。\n"
    "严格要求：\n"
    "1. 不要编造摘要中不存在的事实、时间、文件名、错误码或用户。\n"
    "2. 每条判断都要指出它依据的是哪一条证据（引用事件类型、错误文本或原因标记）。\n"
    "3. 如果证据不足以得出结论，直接说明缺少什么信息，不要猜测。\n"
    "4. 用中文输出，结构固定为五个小节：\n"
    "   **一句话结论**：一句话概括这段窗口内发生了什么。\n"
    "   **关键证据**：3-6 条，每条以「- 」开头，带具体数字或错误文本。\n"
    "   **最可能的根因**：给 1-3 个候选原因，按可能性排序，并写明判断依据。\n"
    "   **处置建议**：可执行的下一步（谁去做什么，或需要看哪个日志/配置）。\n"
    "   **还需补充的信息**：如果要把结论坐实，还需要采集什么。\n"
    "5. 语气克制、面向运维同事；不要写代码块，不要写表格。"
)


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def build_evidence(summary: dict[str, Any], *, question: str | None = None, error_limit: int = 15) -> str:
    """Flatten an ``audit_summary`` payload into a bounded evidence block."""
    window = summary.get("window") or {}
    totals = summary.get("totals") or {}
    lines: list[str] = []

    if question:
        lines.append(f"运维同事的问题：{question.strip()}")
        lines.append("")

    lines.append("【统计窗口】")
    lines.append(f"开始：{window.get('start_at') or '-'}")
    lines.append(f"结束：{window.get('end_at') or '-'}")
    lines.append(f"时长：{window.get('hours') or '-'} 小时，分桶粒度 {window.get('bucket_minutes') or '-'} 分钟")
    lines.append("")

    events = int(totals.get("events") or 0)
    lines.append("【总量】")
    lines.append(f"事件 {_fmt_int(events)} 条，成功 {_fmt_int(totals.get('success'))} 条，失败 {_fmt_int(totals.get('failure'))} 条" + ("（数据已截断，仅统计最近一部分记录）" if totals.get("truncated") else ""))
    if events:
        lines.append(f"失败率：{int(totals.get('failure') or 0) / events * 100:.1f}%")
    lines.append("")

    by_action = summary.get("by_action") or []
    if by_action:
        lines.append("【按事件类型】")
        for item in by_action[:20]:
            lines.append(f"- {item.get('action')}：{_fmt_int(item.get('count'))} 次，失败 {_fmt_int(item.get('failure'))} 次")
        lines.append("")

    by_user = summary.get("by_user") or []
    if by_user:
        lines.append("【按用户（前 10）】")
        for item in by_user[:10]:
            who = item.get("user_email") or item.get("user_id") or "未知用户"
            lines.append(f"- {who}：{_fmt_int(item.get('count'))} 次，失败 {_fmt_int(item.get('failure'))} 次，最近 {item.get('last_at') or '-'}")
        lines.append("")

    by_channel = summary.get("by_channel") or []
    if by_channel:
        lines.append("【按渠道】")
        for item in by_channel[:10]:
            lines.append(f"- {item.get('channel')}：{_fmt_int(item.get('count'))} 次，失败 {_fmt_int(item.get('failure'))} 次")
        lines.append("")

    by_result = summary.get("by_result") or []
    if by_result:
        lines.append("【结果分布】")
        for item in by_result:
            lines.append(f"- {item.get('result')}：{_fmt_int(item.get('count'))} 次")
        lines.append("")

    errors = summary.get("errors") or []
    if errors:
        lines.append(f"【失败样本（最多 {min(len(errors), error_limit)} 条）】")
        for index, item in enumerate(errors[:error_limit], start=1):
            lines.append(
                f"{index}. 时间={item.get('created_at') or '-'}｜事件={item.get('action')}｜"
                f"用户={item.get('user_email') or item.get('user_id') or '-'}｜"
                f"渠道={item.get('channel') or '-'}｜原因标记={item.get('reason') or '-'}｜"
                f"thread={item.get('thread_id') or '-'}｜run={item.get('run_id') or '-'}"
            )
            error = str(item.get("error") or "").strip()
            if error:
                lines.append(f"   错误：{error[:800]}")
            preview = str(item.get("text_preview") or "").strip()
            if preview:
                lines.append(f"   用户原文片段：{preview[:200]}")
        lines.append("")
    else:
        lines.append("【失败样本】")
        lines.append("该窗口内没有失败记录。")
        lines.append("")

    block = "\n".join(lines).strip()
    if len(block) > MAX_INPUT_CHARS:
        block = block[:MAX_INPUT_CHARS] + "\n…（证据过长已截断）"
    return block


async def analyze_digest(
    summary: dict[str, Any],
    *,
    app_config: AppConfig,
    model_name: str | None = None,
    question: str | None = None,
) -> str:
    """Run the one-shot failure analysis and return cleaned Markdown text."""
    evidence = build_evidence(summary, question=question)
    raw = await run_oneshot_llm(
        system_instruction=SYSTEM_INSTRUCTION,
        user_content=evidence,
        run_name=AUDIT_ANALYSIS_RUN_NAME,
        app_config=app_config,
        model_name=model_name,
    )
    cleaned = llm_text.strip_markdown_code_fence(llm_text.strip_think_blocks(raw, truncate_unclosed=True))
    return cleaned.strip()


def render_analysis_message(
    summary: dict[str, Any],
    analysis: str,
    *,
    title: str | None = None,
) -> str:
    """Wrap the model's analysis in the same window header the digest uses."""
    window = summary.get("window") or {}
    totals = summary.get("totals") or {}
    header = title or "机器人日志分析"
    start_at = str(window.get("start_at") or "")[:16].replace("T", " ")
    end_at = str(window.get("end_at") or "")[:16].replace("T", " ")
    lines = [
        f"**{header}**",
        f"统计窗口：{start_at or '-'} → {end_at or '-'}",
        f"事件 {_fmt_int(totals.get('events'))} 条（失败 {_fmt_int(totals.get('failure'))} 条）",
        "",
        analysis.strip(),
    ]
    return "\n".join(lines).strip()