#!/usr/bin/env python3
"""Exercise the same async model factory path used by real DeerFlow runs."""

from __future__ import annotations

import asyncio
import traceback

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from deerflow.models.factory import create_chat_model


MODEL_CONFIG_KEY = "deepseek-v4-flash"


@tool
def connectivity_probe(value: str) -> str:
    """Return the supplied value."""
    return value


async def consume(label: str, model) -> bool:
    try:
        chunks = 0
        text: list[str] = []
        stream = model.astream([HumanMessage(content="只回复 OK，不要解释。")])
        async with asyncio.timeout(120):
            async for chunk in stream:
                chunks += 1
                content = getattr(chunk, "content", "")
                if isinstance(content, str):
                    text.append(content)
        if chunks < 1:
            raise RuntimeError("stream returned no chunks")
        print(
            "%s=PASS chunks=%d text=%r" % (label, chunks, "".join(text)[:120]),
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            "%s=FAIL exception=%s detail=%s"
            % (label, type(exc).__name__, str(exc)),
            flush=True,
        )
        traceback.print_exc()
        return False


async def main() -> None:
    model = create_chat_model(
        MODEL_CONFIG_KEY,
        thinking_enabled=False,
        attach_tracing=False,
    )
    plain_ok = await consume("factory_plain_direct_transport_probe", model)
    tool_ok = await consume(
        "factory_tool_direct_transport_probe",
        model.bind_tools([connectivity_probe]),
    )
    if not plain_ok or not tool_ok:
        raise SystemExit(1)


asyncio.run(main())
