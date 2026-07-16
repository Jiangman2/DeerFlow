"""Run the Gateway API with Windows-compatible asyncio policy.

This wrapper is useful for local Windows development when using async psycopg
backends such as Postgres checkpointer/store. Linux deployments can continue
using the standard uvicorn command.
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


def _configure_windows_event_loop_policy() -> None:
    if sys.platform != "win32":
        return

    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return

    if not isinstance(asyncio.get_event_loop_policy(), selector_policy):
        asyncio.set_event_loop_policy(selector_policy())


_configure_windows_event_loop_policy()


if __name__ == "__main__":
    config = uvicorn.Config("app.gateway.app:app", host="127.0.0.1", port=8001)
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())
