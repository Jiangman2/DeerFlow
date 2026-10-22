"""SDK compatibility and content-free Work WebSocket health observations."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class HandshakeFailure:
    http_status: int | None = None
    provider_status: int | None = None
    business_code: int | None = None

    def __str__(self):
        return f"http={self.http_status} provider={self.provider_status} code={self.business_code}"


def _number(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value.strip()):
        return int(value)
    return None


def handshake_failure(error) -> HandshakeFailure:
    """Work puts quota code in handshake-msg; websockets 14+ changed exception type."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None) or {}
    return HandshakeFailure(
        _number(getattr(response, "status_code", None) or getattr(error, "status_code", None)),
        _number(headers.get("handshake-status") or getattr(error, "code", None)),
        _number(headers.get("handshake-autherrcode")) or _number(headers.get("handshake-msg")),
    )


class TransportError(Exception):
    """Only numeric status codes, never a signed endpoint URL or provider text."""


def observed_client(base, owner):
    """Keep the installed SDK protocol/retry policy while observing true health."""

    class ObservedClient(base):
        async def _connect(self):
            try:
                result = await super()._connect()
                if self._conn is not None and not owner._ws_stop_requested.is_set():
                    owner._ws_failure = None
                    owner._ws_connected.set()
                return result
            except Exception as error:
                owner._ws_connected.clear()
                owner._ws_failure = handshake_failure(error)
                raise TransportError("Work WebSocket handshake failed: " + str(owner._ws_failure)) from None

        async def _disconnect(self):
            owner._ws_connected.clear()
            return await super()._disconnect()

        def _fmt_log(self, fmt, *args):
            text = super()._fmt_log(fmt, *args)
            # SDK INFO normally logs URLs carrying access_key/ticket credentials.
            return re.sub(r"(wss?://[^\s?]+)\?\S+", r"\1?[redacted]", text)

    return ObservedClient
