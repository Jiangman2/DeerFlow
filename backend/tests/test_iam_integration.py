"""Focused unit coverage for the CNOOC IAM protocol boundary."""

import asyncio
from types import SimpleNamespace

import pytest

from starlette.requests import Request

from app.gateway.auth.iam import account_email, is_legacy_account_email, legacy_account_email
from app.gateway.auth.iam_codec import (
    canonicalize_signed_fields,
    decrypt_json_request,
    encrypt_json_response,
    sm3_hexdigest,
)
from app.gateway.auth.models import User
from app.gateway.auth.oidc_state import _callback_path
from app.gateway.auth_middleware import _is_public
from app.gateway.csrf_middleware import should_check_csrf
from app.gateway.routers.iam_account_sync import _find_or_migrate_sync_user


def test_iam_sm4_response_round_trip_and_sm3_signature_material():
    key = "0123456789abcdef"
    payload = {"bimRequestId": "request-1", "__ENABLE__": False, "fullName": "张三"}

    encrypted = encrypt_json_response(payload, key)

    assert decrypt_json_request(encrypted, key) == payload
    assert canonicalize_signed_fields({"b": False, "signature": "ignored", "a": "x"}) == "a=x&b=false"
    assert len(sm3_hexdigest("a=x&b=false")) == 64


def test_iam_account_mapping_is_corporate_and_legacy_address_is_recognized(monkeypatch):
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: SimpleNamespace(auth=SimpleNamespace(iam=SimpleNamespace(redirect_uri=""))))
    assert account_email("AD.User") == "ad.user@cnooc.com.cn"
    legacy = legacy_account_email("AD.User")
    assert is_legacy_account_email(legacy)
    assert _callback_path("iam") == "/api/v1/auth/iam/callback"


@pytest.mark.parametrize("path", ["/api/v1/auth/iam/callback", "/api/v1/auth/callback/iam"])
def test_iam_callback_cookie_path_respects_configured_registration(monkeypatch, path):
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: SimpleNamespace(auth=SimpleNamespace(iam=SimpleNamespace(redirect_uri=f"https://example.invalid{path}"))))
    assert _callback_path("iam") == path


def test_bim_sync_migrates_legacy_iam_identity_in_place():
    legacy = User(email=legacy_account_email("ad.user"), oauth_provider="iam")

    class Provider:
        async def get_user_by_email(self, email: str):
            return legacy if email == legacy.email else None

        async def update_user(self, user: User):
            return user

    migrated = asyncio.run(_find_or_migrate_sync_user(Provider(), "ad.user", "ad.user@cnooc.com.cn"))

    assert migrated.id == legacy.id
    assert migrated.email == "ad.user@cnooc.com.cn"
    assert migrated.oauth_provider == "iam"


def test_bim_base_route_is_public_only_to_its_protocol_guards():
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/iam/account-sync", "headers": []})

    assert _is_public("/api/v1/iam/account-sync")
    assert _is_public("/api/v1/iam/account-sync/UserCreateService")
    assert not should_check_csrf(request)
