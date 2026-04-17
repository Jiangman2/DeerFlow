from __future__ import annotations

from typing import Any

import httpx
from langchain_openai import ChatOpenAI


class HeccChatOpenAI(ChatOpenAI):
    def __init__(
        self,
        *args: Any,
        tls_verify: bool = True,
        trust_env: bool = False,
        **kwargs: Any,
    ) -> None:
        if "http_client" not in kwargs and "http_async_client" not in kwargs:
            kwargs["http_client"] = httpx.Client(verify=tls_verify, trust_env=trust_env)
            kwargs["http_async_client"] = httpx.AsyncClient(verify=tls_verify, trust_env=trust_env)
        super().__init__(*args, **kwargs)
