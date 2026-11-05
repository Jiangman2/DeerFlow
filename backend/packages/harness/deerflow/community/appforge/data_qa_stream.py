"""Bounded handling of the existing AppForge event forms for the new Data QA.

These forms come from the existing adapter, not a captured app-v16p2syh trace.
Live protocol acceptance remains required before release. Master-data parsing
is intentionally unchanged while the new workflow is being qualified.
"""

import codecs
import json
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any

import httpx

_MAX_BYTES = 2 * 1024 * 1024
_MAX_FRAME = 64 * 1024
_FAILED = frozenset({"failed", "error", "failure", "cancelled", "canceled", "aborted"})
_FINISHED = frozenset({"done", "finished", "success", "succeeded", "completed"})


class DataQAStreamError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def iter_data_qa_events(response: httpx.Response) -> Iterator[dict[str, Any]]:
    """Parse actual SSE boundaries, including multiline data and heartbeats."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, event_name = "", ""
    data: list[str] = []
    received = frame_size = 0
    try:
        # A fixed chunk_size buffers small SSE events until 8 KB or EOF.
        # Decode each transport chunk so progress is observable immediately.
        for chunk in response.iter_bytes():
            received += len(chunk)
            if received > _MAX_BYTES:
                raise DataQAStreamError("response_limit")
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.removesuffix("\r")
                frame_size += len(line)
                if frame_size > _MAX_FRAME:
                    raise DataQAStreamError("frame_limit")
                if not line:
                    if data:
                        raw = "\n".join(data)
                        if raw == "[DONE]":
                            yield {"transport_done": True}
                        else:
                            value = json.loads(raw)
                            if not isinstance(value, dict):
                                raise DataQAStreamError("invalid_sse_event")
                            if event_name and not value.get("event") and not value.get("type"):
                                value["event"] = event_name
                            yield value
                    elif event_name == "error":
                        raise DataQAStreamError("upstream_failed")
                    data, event_name, frame_size = [], "", 0
                elif line.startswith(":"):
                    continue
                else:
                    field, sep, value = line.partition(":")
                    if value.startswith(" "):
                        value = value[1:]
                    if field == "data" and sep:
                        data.append(value)
                    elif field == "event" and sep:
                        event_name = value
            if len(buffer) > _MAX_FRAME:
                raise DataQAStreamError("frame_limit")
        decoder.decode(b"", final=True)
        if data or buffer.strip() or event_name:
            raise DataQAStreamError("incomplete_sse_frame")
    except (UnicodeError, json.JSONDecodeError) as error:
        raise DataQAStreamError("invalid_sse_event") from error


def collect_data_qa_stream(response: httpx.Response, *, parse_knowledge: Callable, on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    answer = end_answer = ""
    knowledge: list[dict[str, str]] = []
    progress: deque[str] = deque(maxlen=10)
    interrupt = None
    usage: dict[str, int | float] = {}
    terminal = truncated = False
    status = ""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "text/event-stream":
        raise DataQAStreamError("unexpected_content_type")
    for event in iter_data_qa_events(response):
        if event.get("transport_done"):
            if not terminal:
                raise DataQAStreamError("incomplete_workflow")
            break
        kind = event.get("type") or event.get("event")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        extra = event.get("xx_ceai_extra_data") if isinstance(event.get("xx_ceai_extra_data"), dict) else {}
        states = [str(v.get("status") or "").lower() for v in (event, data, extra)]
        if kind in ("error", "workflow_failed", "workflow_error") or any(s in _FAILED for s in states) or any(v.get("error") for v in (event, data, extra)):
            raise DataQAStreamError("upstream_failed")
        for key in ("inputToken", "outputToken", "totalToken"):
            val = extra.get(key)
            if type(val) in (int, float) and 0 <= val <= 10**12:
                usage[key] = val
        if kind == "answer":
            if on_progress:
                # The observed protocol has unrecognized intermediate node
                # types. Do not infer SQL execution or intent extraction from
                # prose. An end-node answer only proves answer composition.
                on_progress("answering" if data.get("node_type") == "end" or event.get("type") == "answer" else "processing")
            # Legacy type=answer is a delta. Workflow answer/end is a separate
            # answer channel; prefer the terminal full output when provided.
            if event.get("type") == "answer":
                text = str(event.get("content") or "")
                truncated |= len(answer) + len(text) > 5000
                answer = (answer + text)[:5001]
            else:
                text = str(data.get("text") or data.get("answer") or data.get("content") or "")
                if data.get("node_type") == "end":
                    truncated |= len(end_answer) + len(text) > 5000
                    end_answer = (end_answer + text)[:5001]
                else:
                    if not progress:
                        progress.append("处理中")
        elif kind == "knowledge":
            content = event.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    content = [{"content": content}]
            if isinstance(content, list):
                truncated |= len(knowledge) + len(content) > 5
                truncated |= any(isinstance(row, dict) and any(len(str(v)) > 600 for v in row.values()) for row in content)
            items = parse_knowledge(content)
            truncated |= len(knowledge) + len(items) > 5
            knowledge = (knowledge + [{k: v[:600] for k, v in row.items()} for row in items])[:5]
        elif kind == "workflow_interrupt":
            outputs = data.get("outputs")
            interrupt = outputs if isinstance(outputs, dict) else data
            status, terminal = "interrupted", True
            break  # User decision boundary; never consume a later auto-answer.
        elif kind in ("done", "workflow_finished"):
            terminal_states = [s for s in states if s]
            if any(s not in _FINISHED for s in terminal_states):
                raise DataQAStreamError("unrecognized_terminal_status")
            if kind == "workflow_finished":
                outputs = data.get("outputs")
                if isinstance(outputs, dict) and "output" in outputs:
                    text = str(outputs["output"] or "")
                    truncated |= len(text) > 5000
                    answer, end_answer = text[:5001], ""
            status, terminal = "done", True
        # No raw thinking is returned to the model or user by this new tool.
    if not terminal:
        raise DataQAStreamError("incomplete_workflow")
    answer = (answer or end_answer).strip()
    return dict(
        answer=answer[:5000], knowledge=knowledge, thinking=[], progress="\n".join(progress)[-1200:], interrupt=interrupt, status=status if status == "interrupted" or answer or knowledge else "empty", usage=usage, truncated=truncated
    )
