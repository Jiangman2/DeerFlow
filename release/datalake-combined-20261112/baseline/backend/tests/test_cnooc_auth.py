import hashlib
import logging

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway.auth import cnooc
from app.gateway.auth.cnooc import CnoocAuthError
from app.gateway.auth.models import User
from app.gateway.routers.auth import router


def _make_cnooc_login_client(monkeypatch, provider):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr("app.gateway.routers.auth.get_local_provider", lambda: provider)
    return TestClient(app)


class _FakeProvider:
    def __init__(self, *, oauth_user: User | None = None, email_user: User | None = None):
        self.oauth_user = oauth_user
        self.email_user = email_user
        self.created_oauth_user_args: dict | None = None
        self.updated_user: User | None = None
        self.bound_token: tuple[str, str] | None = None
        self.bind_error: Exception | None = None

    async def get_user_by_oauth(self, provider: str, oauth_id: str) -> User | None:
        self.last_oauth_lookup = {"provider": provider, "oauth_id": oauth_id}
        return self.oauth_user

    async def get_user_by_email(self, email: str) -> User | None:
        self.last_email_lookup = email
        return self.email_user

    async def create_oauth_user(
        self,
        email: str,
        oauth_provider: str,
        oauth_id: str,
        system_role: str = "user",
        display_name: str | None = None,
        organization_id: str | None = None,
        organization_code: str | None = None,
        organization_name: str | None = None,
    ) -> User:
        self.created_oauth_user_args = {
            "email": email,
            "oauth_provider": oauth_provider,
            "oauth_id": oauth_id,
            "system_role": system_role,
            "display_name": display_name,
            "organization_id": organization_id,
            "organization_code": organization_code,
            "organization_name": organization_name,
        }
        return User(
            email=email,
            password_hash=None,
            system_role=system_role,
            oauth_provider=oauth_provider,
            oauth_id=oauth_id,
            display_name=display_name,
            organization_id=organization_id,
            organization_code=organization_code,
            organization_name=organization_name,
        )

    async def update_user(self, user: User) -> User:
        self.updated_user = user
        return user

    async def bind_cnooc_platform_token(self, user_id: str, token_hash: str) -> User:
        if self.bind_error is not None:
            raise self.bind_error
        user = self.oauth_user or self.email_user
        if user is None:
            user = User(
                email=self.created_oauth_user_args["email"],
                oauth_provider="cnooc",
                oauth_id="CNOOC-001",
            )
        self.bound_token = (user_id, token_hash)
        user.cnooc_platform_token_hash = token_hash
        return user


def _checked_identity():
    return cnooc.CnoocTokenCheckResult(
        provider="cnooc",
        platform_token_hash=hashlib.sha256(b"platform-token").hexdigest(),
        user=cnooc.CnoocUser(
            platform_user_id="CNOOC-001",
            deerflow_user_id="cnooc_CNOOC-001",
            name="CNOOC User",
            email="cnooc.user@cnooc.com.cn",
            organization_id="ORG-001",
            organization_code="0102030",
            organization_name="Exploration Department",
        ),
    )


def _set_cookie_headers(response):
    return response.headers.get_list("set-cookie")


def test_map_user_info_builds_cnooc_identity():
    result = cnooc._map_user_info(
        {
            "userid": "USER-001",
            "username": "ZHANGSAN",
            "ryxm": "Zhang San",
            "bmId": "ORG-001",
            "bmdm": "0102030",
            "bmmc": "Exploration Department",
        },
        "platform-token",
    )

    assert result.provider == "cnooc"
    assert result.platform_token_hash == hashlib.sha256(b"platform-token").hexdigest()
    assert result.user.platform_user_id == "USER-001"
    assert result.user.deerflow_user_id == "cnooc_USER-001"
    assert result.user.email == "zhangsan@cnooc.com.cn"
    assert result.user.name == "Zhang San"
    assert result.user.organization_id == "ORG-001"
    assert result.user.organization_code == "0102030"
    assert result.user.organization_name == "Exploration Department"


def test_map_user_info_sanitizes_platform_user_id():
    result = cnooc._map_user_info(
        {
            "userid": "user/with spaces",
            "username": "user",
            "ryxm": "User",
        },
        "platform-token",
    )

    legacy_suffix = hashlib.sha1(b"user/with spaces").hexdigest()[:10]
    assert result.user.deerflow_user_id == f"cnooc_user_with_spaces_{legacy_suffix}"


@pytest.mark.parametrize(
    "data",
    [
        None,
        {"userid": "", "username": "user", "ryxm": "User"},
        {"userid": "user", "username": "", "ryxm": "User"},
        {"userid": "user", "username": "user", "ryxm": ""},
    ],
)
def test_map_user_info_requires_cnooc_user_fields(data):
    with pytest.raises(CnoocAuthError):
        cnooc._map_user_info(data, "platform-token")


def test_load_config_reports_missing_required_env(monkeypatch):
    for key in (
        "CNOOC_OAUTH_BASE_URL",
        "CNOOC_OAUTH_APP_NAME",
        "CNOOC_OAUTH_APP_ID",
        "CNOOC_OAUTH_TOKEN_CHECK_API_TOKEN",
        "CNOOC_OAUTH_TOKEN_CHECK_LOGIN_USER_CODE",
        "CNOOC_OAUTH_USER_INFO_API_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(CnoocAuthError) as exc_info:
        cnooc._load_config()

    assert "CNOOC_OAUTH_BASE_URL" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_app_code_uses_first_eight_timestamp_digits(monkeypatch):
    captured = {}

    class Client:
        async def get(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"code": 200, "data": "app-code"},
            )

    monkeypatch.setattr(cnooc, "_cached_app_code", None)
    monkeypatch.setattr(cnooc.time, "time", lambda: 1_723_456_789.9)
    config = cnooc.CnoocAuthConfig(
        base_url="http://datalake.internal",
        app_name="deerflow",
        app_id="app-id",
        token_check_api_token="token-check",
        token_check_login_user_code="login-user",
        user_info_api_token="user-info",
    )

    app_code = await cnooc._fetch_app_code(Client(), config)

    assert app_code == "app-code"
    assert captured["params"] == {
        "appName": "deerflow",
        "appId": "app-id",
        "timeStamp": "17234567",
    }


@pytest.mark.asyncio
async def test_cnooc_request_retries_transient_timeout(monkeypatch):
    monkeypatch.setenv("CNOOC_OAUTH_REQUEST_ATTEMPTS", "2")
    monkeypatch.setenv("CNOOC_OAUTH_RETRY_BACKOFF_SECONDS", "0.001")

    class Client:
        calls = 0

        async def get(self, url, **_kwargs):
            self.calls += 1
            request = httpx.Request("GET", url)
            if self.calls == 1:
                raise httpx.ReadTimeout("slow upstream", request=request)
            return httpx.Response(200, request=request, json={"code": 200})

    client = Client()
    response = await cnooc._get_with_retry(
        client,
        "http://sso.internal/token-check",
        label="CNOOC token verification",
        params={"query": "redacted"},
    )

    assert response.status_code == 200
    assert client.calls == 2


@pytest.mark.asyncio
async def test_cnooc_request_maps_exhausted_timeout_to_503(monkeypatch):
    monkeypatch.setenv("CNOOC_OAUTH_REQUEST_ATTEMPTS", "2")
    monkeypatch.setenv("CNOOC_OAUTH_RETRY_BACKOFF_SECONDS", "0.001")

    class Client:
        calls = 0

        async def get(self, url, **_kwargs):
            self.calls += 1
            raise httpx.ReadTimeout("slow upstream", request=httpx.Request("GET", url))

    client = Client()
    with pytest.raises(CnoocAuthError) as exc_info:
        await cnooc._get_with_retry(
            client,
            "http://sso.internal/token-check",
            label="CNOOC token verification",
            params={"query": "redacted"},
        )

    assert exc_info.value.status_code == 503
    assert client.calls == 2


@pytest.mark.asyncio
async def test_data_lake_token_exchange_uses_app_code_and_persisted_challenge():
    captured = {}

    class Client:
        async def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"code": 200, "msg": "success", "data": "issued-token"},
            )

    config = cnooc.CnoocDataLakeExchangeConfig(
        base_url="http://datalake.internal",
        app_name="deerflow",
        app_id="app-id",
        api_token="issue-api-token",
        login_user_code="login-user",
        callback_id="integration-002",
    )

    token = await cnooc._exchange_data_lake_token(Client(), config, "cached-app-code", "A" * 32)

    assert token == "issued-token"
    assert captured == {
        "url": "http://datalake.internal/apigateway/zcshf/zcjksjfw_9725",
        "headers": {
            "appCode": "cached-app-code",
            "loginUserCode": "login-user",
            "apiToken": "issue-api-token",
        },
        "json": {
            "jsonObj": {
                "callbackId": "integration-002",
                "challenge": "A" * 32,
            }
        },
    }


@pytest.mark.asyncio
async def test_data_lake_token_exchange_maps_business_rejection_to_forbidden():
    class Client:
        async def post(self, url, **kwargs):
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "code": 403,
                    "msg": "permission denied",
                    "bizCode": "NO_PERMISSION",
                    "dataSize": 0,
                    "data": "",
                },
            )

    config = cnooc.CnoocDataLakeExchangeConfig(
        base_url="http://datalake.internal",
        app_name="deerflow",
        app_id="app-id",
        api_token="issue-api-token",
        login_user_code="login-user",
        callback_id="integration-002",
    )

    with pytest.raises(CnoocAuthError) as exc_info:
        await cnooc._exchange_data_lake_token(Client(), config, "cached-app-code", "A" * 32)

    assert exc_info.value.status_code == 403


def test_cnooc_login_maps_upstream_unavailable_to_503(monkeypatch):
    async def unavailable(_token: str):
        raise CnoocAuthError("CNOOC token verification temporarily unavailable", 503)

    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", unavailable)
    client = _make_cnooc_login_client(monkeypatch, _FakeProvider())

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 503


def test_cnooc_httpx_log_filter_redacts_query_token():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        'HTTP Request: GET "http://sso.internal/apigateway/tokenCheck?query=%7Bsecret%7D&appCode=ok"',
        (),
        None,
    )

    assert cnooc._CnoocHttpxLogFilter().filter(record) is True
    assert "secret" not in record.getMessage()
    assert "query=<redacted>" in record.getMessage()


def test_cnooc_login_creates_oauth_user_and_sets_session_cookies(monkeypatch):
    async def fake_check_cnooc_token(token: str):
        assert token == "platform-token"
        return _checked_identity()

    provider = _FakeProvider()
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get(
        "/api/v1/auth/cnooc/login?token=platform-token&next=/workspace/chats/new",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/workspace/chats/new"
    assert provider.last_oauth_lookup == {"provider": "cnooc", "oauth_id": "CNOOC-001"}
    assert provider.last_email_lookup == "cnooc.user@cnooc.com.cn"
    assert provider.created_oauth_user_args == {
        "email": "cnooc.user@cnooc.com.cn",
        "oauth_provider": "cnooc",
        "oauth_id": "CNOOC-001",
        "system_role": "user",
        "display_name": "CNOOC User",
        "organization_id": "ORG-001",
        "organization_code": "0102030",
        "organization_name": "Exploration Department",
    }
    assert provider.bound_token is not None
    assert provider.bound_token[1] == hashlib.sha256(b"platform-token").hexdigest()

    cookie_headers = _set_cookie_headers(response)
    assert any(header.startswith("access_token=") and "httponly" in header.lower() for header in cookie_headers)
    assert any(header.startswith("csrf_token=") for header in cookie_headers)
    assert any(header.startswith("cnooc.platform_token=platform-token") and "httponly" in header.lower() for header in cookie_headers)


def test_cnooc_login_does_not_issue_cookies_when_token_binding_fails(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    provider = _FakeProvider()
    provider.bind_error = ValueError("platform token already belongs to another user")
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 409
    assert not _set_cookie_headers(response)


def test_logout_clears_cnooc_platform_token_cookie(monkeypatch):
    client = _make_cnooc_login_client(monkeypatch, _FakeProvider())
    client.cookies.set("access_token", "deerflow-session")
    client.cookies.set("cnooc.platform_token", "platform-token")

    response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    cookie_headers = _set_cookie_headers(response)
    assert any(header.startswith("access_token=") and "max-age=0" in header.lower() for header in cookie_headers)
    assert any(header.startswith("cnooc.platform_token=") and "max-age=0" in header.lower() for header in cookie_headers)


def test_cnooc_login_rejects_open_redirect_next_param(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    provider = _FakeProvider()
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get(
        "/api/v1/auth/cnooc/login?token=platform-token&next=https://evil.example/path",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/workspace/chats/new"


def test_cnooc_login_links_existing_local_account_by_email(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    provider = _FakeProvider(email_user=User(email="cnooc.user@cnooc.com.cn", password_hash="local-hash"))
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 302
    assert provider.updated_user is provider.email_user
    assert provider.updated_user.oauth_provider == "cnooc"
    assert provider.updated_user.oauth_id == "CNOOC-001"
    assert provider.created_oauth_user_args is None


def test_cnooc_login_refreshes_existing_cnooc_email_link(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    provider = _FakeProvider(
        email_user=User(
            email="cnooc.user@cnooc.com.cn",
            password_hash=None,
            oauth_provider="cnooc",
            oauth_id="OLD-CNOOC-ID",
        )
    )
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 302
    assert provider.updated_user is provider.email_user
    assert provider.updated_user.oauth_provider == "cnooc"
    assert provider.updated_user.oauth_id == "CNOOC-001"
    assert provider.created_oauth_user_args is None


def test_cnooc_login_refreshes_existing_user_profile(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    existing = User(
        email="cnooc.user@cnooc.com.cn",
        password_hash=None,
        oauth_provider="cnooc",
        oauth_id="CNOOC-001",
        display_name="Old Name",
        organization_name="Old Department",
    )
    provider = _FakeProvider(oauth_user=existing)
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 302
    assert provider.updated_user is existing
    assert existing.display_name == "CNOOC User"
    assert existing.organization_id == "ORG-001"
    assert existing.organization_code == "0102030"
    assert existing.organization_name == "Exploration Department"


def test_cnooc_login_blocks_email_collision_with_different_oauth_account(monkeypatch):
    async def fake_check_cnooc_token(_token: str):
        return _checked_identity()

    provider = _FakeProvider(
        email_user=User(
            email="cnooc.user@cnooc.com.cn",
            password_hash=None,
            oauth_provider="other",
            oauth_id="OTHER-001",
        )
    )
    monkeypatch.setattr("app.gateway.routers.auth.check_cnooc_token", fake_check_cnooc_token)
    client = _make_cnooc_login_client(monkeypatch, provider)

    response = client.get("/api/v1/auth/cnooc/login?token=platform-token", follow_redirects=False)

    assert response.status_code == 409
    assert provider.updated_user is None
    assert provider.created_oauth_user_args is None
