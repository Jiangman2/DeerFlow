"""Server-to-server callback for authenticated Work calendar card clicks."""

import asyncio
import logging

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.gateway.routers.work_documents import _resolve_service
from app.integrations.work.cards import CARD_CALLBACK_PATH, CardSettings, card_callback_response, decode_callback
from app.integrations.work.config import WorkSettings

router = APIRouter(tags=["work"])
logger = logging.getLogger(__name__)
_HEADERS = {"Cache-Control": "no-store"}


@router.post(CARD_CALLBACK_PATH, include_in_schema=False)
async def work_card_callback(request: Request):
    try:
        work = WorkSettings.from_env()
        settings = CardSettings.from_env(work.profile) if work.enabled and work.persistent else None
    except ValueError:
        settings = None
    if settings is None or settings.transport != "http":
        return JSONResponse({}, status_code=404, headers=_HEADERS)

    try:
        async with asyncio.timeout(2):
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 16_384:
                    return JSONResponse({}, status_code=413, headers=_HEADERS)
                raw.extend(chunk)
            for name in ("x-lark-request-timestamp", "x-lark-request-nonce", "x-lark-signature"):
                if len(request.headers.getlist(name)) > 1:
                    return JSONResponse({}, status_code=400, headers=_HEADERS)
            try:
                action = decode_callback(bytes(raw), request.headers, settings)
            except Exception:
                return JSONResponse({}, status_code=400, headers=_HEADERS)
            if isinstance(action, str):
                return JSONResponse({"challenge": action}, headers=_HEADERS)
            service = await _resolve_service()
            status = await service.enqueue_card_click(action, settings) if service is not None else "unavailable"
    except TimeoutError:
        status = "busy"
    except Exception:
        logger.warning("work_card stage=callback_admission_failed")
        status = "unavailable"
    return JSONResponse(card_callback_response(status), headers=_HEADERS)
