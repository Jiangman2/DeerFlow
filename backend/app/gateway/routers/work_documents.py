"""Production browser OAuth and UAT-only machine relay; never return user tokens."""

import hmac
import inspect
import json
import logging
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.channels.work_documents import RELAY_PATH, TOKEN_RE, WorkError
from app.integrations.work.config import BROWSER_AUTHORIZE_PATH, BROWSER_CALLBACK_PATH, WorkSettings

router = APIRouter(tags=["work"])
logger = logging.getLogger(__name__)


class RelayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["health", "authorize", "callback"]
    ticket: str = Field(default="", max_length=128)
    browser_session: str = Field(default="", max_length=128)
    state: str = Field(default="", max_length=128)
    code: str = Field(default="", max_length=4096)
    error: str = Field(default="", max_length=200)


async def _active_service():
    if not WorkSettings.from_env().enabled:
        return None
    from app.channels.service import get_channel_service

    channels = get_channel_service()
    if channels is None:
        return None
    from app.integrations.work.service import active_work_service

    return await active_work_service(channels.manager)


async def _resolve_service():
    result = _active_service()
    return await result if inspect.isawaitable(result) else result


@router.post(RELAY_PATH)
async def oauth_relay(request: Request):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

    def reply(data, status=200):
        return JSONResponse(data, status_code=status, headers=headers)

    expected = os.getenv("DEER_FLOW_WORK_DOCS_RELAY_KEY", "")
    provided = request.headers.get("Authorization", "")
    if len(expected) < 32 or not hmac.compare_digest(provided, "Bearer " + expected):
        return reply({"stage": "relay_unauthorized"}, 401)
    try:
        settings = WorkSettings.from_env()
        # The legacy relay must never become an alternative production OAuth entry.
        service = await _resolve_service() if settings.profile == "uat" else None
    except ValueError:
        service = None
    if service is None:
        return reply({"stage": "uat_integration_disabled"}, 404)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16_384:
            return reply({"stage": "request_too_large"}, 413)
    try:
        body = RelayBody.model_validate(json.loads(raw))
        if body.action == "health":
            return reply({"profile": "uat", "ready": True, "pending_jobs": len(service.jobs), "authorized_users": len(service.grants)})
        if body.action == "authorize":
            if not TOKEN_RE.fullmatch(body.ticket):
                raise ValueError
            return reply(await service.authorize(body.ticket, body.browser_session))
        if not TOKEN_RE.fullmatch(body.state) or not TOKEN_RE.fullmatch(body.browser_session):
            raise ValueError
        return reply(await service.callback(state=body.state, code=body.code, browser_session=body.browser_session, error=body.error))
    except (ValueError, ValidationError):
        return reply({"stage": "invalid_request"}, 400)
    except WorkError as exc:
        return reply({"stage": exc.stage, "http_status": exc.http_status, "business_code": exc.business_code, "message": "授权未完成、链接已过期或账号不匹配。请回到机器人重新发送请求，并使用同一个 UAT 账号授权。"}, 400)
    except Exception:
        return reply({"stage": "relay_internal_error", "message": "本地授权处理未完成，请重新发送请求或检查本地服务。"}, 503)


_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
}
_COOKIE = "__Host-work-oauth"


def _page(success=False, status=400):
    text = "授权账号已核对。请回到海能 Work 机器人查看结果；修改操作仍需单独确认。" if success else "授权未完成、链接已过期或账号不匹配。请回到机器人重新发起请求，并使用与消息发送者相同的账号授权。"
    return HTMLResponse('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>海能 Work 授权</title><body><p>' + text + "</p></body></html>", status_code=status, headers=_HEADERS)


def _bounded_query(request, name, maximum, *, required=False):
    values = request.query_params.getlist(name)
    if len(values) > 1 or required and not values or values and len(values[0]) > maximum:
        raise WorkError("invalid_request")
    return values[0] if values else ""


@router.get(BROWSER_AUTHORIZE_PATH, include_in_schema=False)
async def browser_authorize(request: Request):
    try:
        ticket = _bounded_query(request, "ticket", 128, required=True)
        if not TOKEN_RE.fullmatch(ticket):
            return _page()
        service = await _resolve_service()
        if service is None:
            return _page(status=503)
        session = request.cookies.get(_COOKIE, "")
        if not TOKEN_RE.fullmatch(session):
            session = secrets.token_urlsafe(32)
        result = await service.authorize(ticket, session)
        # Never derive the destination or redirect_uri from the browser Host header.
        if not result["authorize_url"].startswith(service.settings.authorize_url + "?"):
            return _page(status=503)
        response = RedirectResponse(result["authorize_url"], status_code=303, headers=_HEADERS)
        response.set_cookie(_COOKIE, session, max_age=600, secure=True, httponly=True, samesite="lax", path="/")
        return response
    except Exception:
        logger.warning("work_oauth stage=authorize_failed")
        return _page()


@router.get(BROWSER_CALLBACK_PATH, include_in_schema=False)
async def browser_callback(request: Request):
    try:
        state = _bounded_query(request, "state", 128, required=True)
        code = _bounded_query(request, "code", 4096)
        error = _bounded_query(request, "error", 200)
        session = request.cookies.get(_COOKIE, "")
        if not TOKEN_RE.fullmatch(state) or not TOKEN_RE.fullmatch(session) or not code and not error:
            return _page()
        service = await _resolve_service()
        if service is None:
            return _page(status=503)
        await service.callback(state=state, code=code, browser_session=session, error=error)
        # Keep a session cookie for parallel outstanding flows in this browser;
        # each state is independently one-use and expires within ten minutes.
        return _page(True, 200)
    except WorkError as exc:
        logger.warning("work_oauth stage=callback_failed http=%s code=%s", exc.http_status, exc.business_code)
        return _page()
    except Exception:
        logger.warning("work_oauth stage=callback_failed")
        return _page()
