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
    ) -> User:
        self.created_oauth_user_args = {
            "email": email,
            "oauth_provider": oauth_provider,
            "oauth_id": oauth_id,
            "system_role": system_role,
        }
        return User(
            email=email,
            password_hash=None,
            system_role=system_role,
            oauth_provider=oauth_provider,
            oauth_id=oauth_id,
        )

    async def update_user(self, user: User) -> User:
        self.updated_user = user
        return user


def _checked_identity():
    return cnooc.CnoocTokenCheckResult(
        provider="cnooc",
        platform_token_hash="hashed-token",
        user=cnooc.CnoocUser(
            platform_user_id="CNOOC-001",
            deerflow_user_id="cnooc_CNOOC-001",
            name="CNOOC User",
            email="cnooc.user@cnooc.com.cn",
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
        },
        "platform-token",
    )

    assert result.provider == "cnooc"
    assert result.platform_token_hash != "platform-token"
    assert result.user.platform_user_id == "USER-001"
    assert result.user.deerflow_user_id == "cnooc_USER-001"
    assert result.user.email == "zhangsan@cnooc.com.cn"
    assert result.user.name == "Zhang San"


def test_map_user_info_sanitizes_platform_user_id():
    result = cnooc._map_user_info(
        {
            "userid": "user/with spaces",
            "username": "user",
            "ryxm": "User",
        },
        "platform-token",
    )

    assert result.user.deerflow_user_id.startswith("cnooc_user_with_spaces_")


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
    }

    cookie_headers = _set_cookie_headers(response)
    assert any(header.startswith("access_token=") and "httponly" in header.lower() for header in cookie_headers)
    assert any(header.startswith("csrf_token=") for header in cookie_headers)
    assert any(header.startswith("cnooc.platform_token=platform-token") and "httponly" in header.lower() for header in cookie_headers)


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
