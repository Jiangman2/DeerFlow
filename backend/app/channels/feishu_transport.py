"""SDK compatibility and content-free Work WebSocket health observations."""

import base64
import json
import re
import time
from dataclasses import dataclass
from http import HTTPStatus

MAX_WORK_CARD_FRAME_BYTES = 16_384
_MAX_CARD_FRAME_PARTS = 8


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


def observed_client(base, owner, *, card_handler=None):
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

    if card_handler is None:
        return ObservedClient

    from lark_oapi.ws import client as sdk

    class CardObservedClient(ObservedClient):
        async def _handle_data_frame(self, frame):
            headers = frame.headers
            frame_type = sdk._get_by_key(headers, sdk.HEADER_TYPE)
            if frame_type not in (sdk.MessageType.EVENT.value, sdk.MessageType.CARD.value):
                return await super()._handle_data_frame(frame)

            started = time.monotonic()
            parts = int(sdk._get_by_key(headers, sdk.HEADER_SUM))
            index = int(sdk._get_by_key(headers, sdk.HEADER_SEQ))
            bounded = 1 <= parts <= _MAX_CARD_FRAME_PARTS and 0 <= index < parts and len(frame.payload) <= MAX_WORK_CARD_FRAME_BYTES
            if frame_type == sdk.MessageType.EVENT.value:
                payload = self._peek_event_card(frame, parts, index) if bounded else None
                if payload is None:
                    return await super()._handle_data_frame(frame)
                result = await card_handler(payload)
            elif bounded:
                # lark-oapi 1.5.5 drops CARD frames before invoking its dispatcher.
                payload = frame.payload
                if parts > 1:
                    message_id = sdk._get_by_key(headers, sdk.HEADER_MESSAGE_ID)
                    payload = self._combine("card:" + message_id, parts, index, payload)
                    if payload is None:
                        return
                result = await card_handler(payload)
            else:
                result = None

            # Match the SDK ACK format and preserve the original frame type.
            header = headers.add()
            header.key = sdk.HEADER_BIZ_RT
            header.value = str(int((time.monotonic() - started) * 1000))
            response = sdk.Response(code=HTTPStatus.OK)
            if result is not None:
                response.data = base64.b64encode(sdk.JSON.marshal(result).encode())
            frame.payload = sdk.JSON.marshal(response).encode()
            await self._write_message(frame.SerializeToString())

        def _peek_event_card(self, frame, parts, index):
            payload = frame.payload
            if parts > 1:
                # SDK 1.5.5 caches prior fragments for five seconds. Inspect a
                # copy only, so ordinary fragments still follow SDK assembly.
                message_id = sdk._get_by_key(frame.headers, sdk.HEADER_MESSAGE_ID)
                cache_read = getattr(getattr(self, "_cache", None), "get", None)
                previous = cache_read(message_id) if callable(cache_read) else None
                if not isinstance(previous, list) or len(previous) != parts:
                    return None
                chunks = previous.copy()
                chunks[index] = payload
                if any(not isinstance(chunk, bytes) or not chunk for chunk in chunks) or sum(map(len, chunks)) > MAX_WORK_CARD_FRAME_BYTES:
                    return None
                payload = b"".join(chunks)
            try:
                value = json.loads(payload)
            except (ValueError, RecursionError):
                return None
            header = value.get("header") if isinstance(value, dict) else None
            return value if isinstance(header, dict) and header.get("event_type") == "card.action.trigger" else None

    return CardObservedClient
