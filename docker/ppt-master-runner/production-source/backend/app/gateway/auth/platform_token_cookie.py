"""Shared HttpOnly cookie contract for the CNOOC Data Lake platform token."""

from fastapi import Request, Response

from app.gateway.csrf_middleware import is_secure_request

CNOOC_PLATFORM_TOKEN_COOKIE_NAME = "cnooc.platform_token"


def set_cnooc_platform_token_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        key=CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=is_secure_request(request),
        samesite="lax",
        max_age=7 * 24 * 3600,
    )


def clear_cnooc_platform_token_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=CNOOC_PLATFORM_TOKEN_COOKIE_NAME,
        secure=is_secure_request(request),
        samesite="lax",
        path="/",
    )
