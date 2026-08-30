import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.gateway.auth import cnooc
from app.gateway.auth.models import User
from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
from app.gateway.auth_middleware import _is_public
from app.gateway.csrf_middleware import should_check_csrf
from app.gateway.routers import data_lake_auth
from deerflow.persistence.base import Base
from deerflow.persistence.data_lake_auth import (
    CallbackResolution,
    DataLakeAuthChallengeRepository,
    DataLakeAuthChallengeRow,
)
from deerflow.persistence.user.model import UserRow


class _ChallengeRepository:
    def __init__(self, resolution: CallbackResolution | None = None):
        self.created = None
        self.resolution = resolution or CallbackResolution("accepted", "user-1")

    async def create(self, **kwargs):
        self.created = kwargs

    async def accept_callback(self, **kwargs):
        self.callback = kwargs
        return self.resolution


class _Provider:
    def __init__(self, user: User | None):
        self.user = user
        self.bound_token_hash: str | None = None

    async def get_user(self, user_id: str):
        self.requested_user_id = user_id
        return self.user

    async def bind_cnooc_platform_token(self, user_id: str, token_hash: str) -> User:
        assert self.user is not None
        assert str(self.user.id) == user_id
        self.bound_token_hash = token_hash
        self.user.cnooc_platform_token_hash = token_hash
        return self.user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _checked_identity(user: User, token: str) -> cnooc.CnoocTokenCheckResult:
    return cnooc.CnoocTokenCheckResult(
        provider="cnooc",
        platform_token_hash=_token_hash(token),
        user=cnooc.CnoocUser(
            platform_user_id=user.oauth_id or "CNOOC-001",
            deerflow_user_id="cnooc_CNOOC-001",
            name=user.display_name or "CNOOC User",
            email=str(user.email),
        ),
    )


def _client(repository: _ChallengeRepository, user: User):
    app = FastAPI()
    app.include_router(data_lake_auth.router)
    app.dependency_overrides[data_lake_auth.get_data_lake_challenge_repository] = lambda: repository
    app.dependency_overrides[data_lake_auth.get_current_user_from_request] = lambda: user
    return TestClient(app)


def test_data_lake_authorization_reuses_existing_http_only_cookie(monkeypatch):
    repository = _ChallengeRepository()
    user = User(
        email="user@example.com",
        cnooc_platform_token_hash=_token_hash("existing-token"),
    )

    async def should_not_exchange(_challenge: str):
        raise AssertionError("exchange must not run when the cookie already exists")

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", should_not_exchange)
    client = _client(repository, user)
    client.cookies.set("cnooc.platform_token", "existing-token")

    response = client.post("/api/v1/data-lake/auth/token")

    assert response.status_code == 200
    assert response.json() == {"code": "already_authorized"}
    assert repository.created is None


def test_data_lake_authorization_does_not_trust_another_users_cookie(monkeypatch):
    repository = _ChallengeRepository()
    user = User(
        email="user@example.com",
        oauth_provider="cnooc",
        oauth_id="CNOOC-001",
        cnooc_platform_token_hash=_token_hash("current-users-token"),
    )
    provider = _Provider(user)
    exchanged = {"count": 0}

    async def exchange(_challenge: str):
        exchanged["count"] += 1
        return "fresh-token"

    async def check(token: str):
        return _checked_identity(user, token)

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", check)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)
    client = _client(repository, user)
    client.cookies.set("cnooc.platform_token", "another-users-token")

    response = client.post("/api/v1/data-lake/auth/token")

    assert response.status_code == 200
    assert response.json() == {"code": "authorized"}
    assert exchanged["count"] == 1
    assert provider.bound_token_hash == _token_hash("fresh-token")
    assert "cnooc.platform_token=fresh-token" in response.headers["set-cookie"]


def test_data_lake_authorization_persists_60_second_challenge_and_sets_cookie(
    monkeypatch,
):
    repository = _ChallengeRepository()
    user = User(
        email="user@example.com",
        oauth_provider="cnooc",
        oauth_id="CNOOC-001",
    )
    provider = _Provider(user)
    captured = {}

    async def exchange(challenge: str):
        captured["challenge"] = challenge
        return "issued-platform-token"

    async def check(token: str):
        return _checked_identity(user, token)

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", check)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)
    before = datetime.now(UTC)
    response = _client(repository, user).post("/api/v1/data-lake/auth/token")
    after = datetime.now(UTC)

    assert response.status_code == 200
    assert response.json() == {"code": "authorized"}
    assert "issued-platform-token" not in response.text
    assert repository.created["user_id"] == str(user.id)
    assert repository.created["challenge_hash"] == data_lake_auth._challenge_hash(captured["challenge"])
    assert before.timestamp() + 60 <= repository.created["expires_at"].timestamp() <= after.timestamp() + 60
    cookie = response.headers["set-cookie"].lower()
    assert "cnooc.platform_token=issued-platform-token" in cookie
    assert "httponly" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert provider.bound_token_hash == _token_hash("issued-platform-token")


def test_data_lake_authorization_allows_matching_iam_corporate_email(monkeypatch):
    repository = _ChallengeRepository()
    user = User(
        email="Employee.One@cnooc.com.cn",
        oauth_provider="iam",
        oauth_id="IAM-001",
    )
    provider = _Provider(user)

    async def exchange(_challenge: str):
        return "iam-users-token"

    async def check(token: str):
        return cnooc.CnoocTokenCheckResult(
            provider="cnooc",
            platform_token_hash=_token_hash(token),
            user=cnooc.CnoocUser(
                platform_user_id="CNOOC-001",
                deerflow_user_id="cnooc_CNOOC-001",
                name="Employee One",
                email="employee.one@cnooc.com.cn",
            ),
        )

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", check)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)

    response = _client(repository, user).post("/api/v1/data-lake/auth/token")

    assert response.status_code == 200
    assert response.json() == {"code": "authorized"}
    assert provider.bound_token_hash == _token_hash("iam-users-token")


@pytest.mark.parametrize(
    ("oauth_provider", "oauth_id", "current_email"),
    [
        ("iam", "IAM-001", "different.user@cnooc.com.cn"),
        ("iam", None, "employee.one@cnooc.com.cn"),
        ("iam", "IAM-001", "employee.one@example.com"),
        ("github", "GITHUB-001", "employee.one@cnooc.com.cn"),
        (None, None, "employee.one@cnooc.com.cn"),
    ],
)
def test_data_lake_authorization_rejects_untrusted_email_linkage(
    monkeypatch,
    oauth_provider,
    oauth_id,
    current_email,
):
    repository = _ChallengeRepository()
    user = User(
        email=current_email,
        oauth_provider=oauth_provider,
        oauth_id=oauth_id,
    )
    provider = _Provider(user)

    async def exchange(_challenge: str):
        return "untrusted-users-token"

    async def check(token: str):
        return cnooc.CnoocTokenCheckResult(
            provider="cnooc",
            platform_token_hash=_token_hash(token),
            user=cnooc.CnoocUser(
                platform_user_id="CNOOC-001",
                deerflow_user_id="cnooc_CNOOC-001",
                name="Employee One",
                email="employee.one@cnooc.com.cn",
            ),
        )

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", check)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)

    response = _client(repository, user).post("/api/v1/data-lake/auth/token")

    assert response.status_code == 403
    assert response.json()["code"] == "data_lake_identity_mismatch"
    assert provider.bound_token_hash is None
    assert "untrusted-users-token" not in response.headers.get("set-cookie", "")


def test_data_lake_authorization_returns_permission_denied_code(monkeypatch):
    repository = _ChallengeRepository()
    user = User(email="user@example.com")

    async def reject_permission(_challenge: str):
        raise cnooc.CnoocAuthError("permission denied", 403)

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", reject_permission)

    response = _client(repository, user).post("/api/v1/data-lake/auth/token")

    assert response.status_code == 403
    assert response.json() == {
        "code": "data_lake_permission_denied",
        "message": "The current account does not have Data Lake access.",
    }
    assert "cnooc.platform_token" not in response.headers.get("set-cookie", "")


def test_data_lake_authorization_returns_login_required_for_invalid_platform_token(
    monkeypatch,
):
    repository = _ChallengeRepository()
    user = User(
        email="user@example.com",
        oauth_provider="cnooc",
        oauth_id="CNOOC-001",
    )

    async def exchange(_challenge: str):
        return "invalid-platform-token"

    async def reject_invalid_token(_token: str):
        raise cnooc.CnoocAuthError("token verification failed", 401)

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", reject_invalid_token)

    response = _client(repository, user).post("/api/v1/data-lake/auth/token")

    assert response.status_code == 401
    assert response.json() == {
        "code": "data_lake_login_required",
        "message": "Please sign in to Data Lake first.",
    }
    assert "cnooc.platform_token" not in response.headers.get("set-cookie", "")


def test_data_lake_authorization_rejects_exchanged_token_for_different_user(
    monkeypatch,
):
    repository = _ChallengeRepository()
    user = User(
        email="user@example.com",
        oauth_provider="cnooc",
        oauth_id="CNOOC-001",
    )
    provider = _Provider(user)

    async def exchange(_challenge: str):
        return "different-users-token"

    async def check(token: str):
        other_user = User(
            email="other@example.com",
            oauth_provider="cnooc",
            oauth_id="CNOOC-002",
        )
        return _checked_identity(other_user, token)

    monkeypatch.setattr(data_lake_auth, "issue_data_lake_token", exchange)
    monkeypatch.setattr(data_lake_auth, "check_cnooc_token", check)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)

    response = _client(repository, user).post("/api/v1/data-lake/auth/token")

    assert response.status_code == 403
    assert response.json()["code"] == "data_lake_identity_mismatch"
    assert provider.bound_token_hash is None
    assert "different-users-token" not in response.headers.get("set-cookie", "")


def test_data_lake_session_checks_cookie_binding_without_returning_token():
    token = "current-users-token"
    user = User(
        email="user@example.com",
        cnooc_platform_token_hash=_token_hash(token),
    )
    client = _client(_ChallengeRepository(), user)
    client.cookies.set("cnooc.platform_token", token)

    response = client.get("/api/v1/data-lake/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authorized": True}
    assert token not in response.text


def test_data_lake_session_rejects_mismatched_cookie_and_clears_it():
    user = User(
        email="user@example.com",
        cnooc_platform_token_hash=_token_hash("current-users-token"),
    )
    client = _client(_ChallengeRepository(), user)
    client.cookies.set("cnooc.platform_token", "another-users-token")

    response = client.get("/api/v1/data-lake/auth/session")

    assert response.status_code == 401
    assert response.json() == {"authorized": False}
    cookie = response.headers["set-cookie"].lower()
    assert "cnooc.platform_token=" in cookie
    assert "max-age=0" in cookie


def test_data_lake_callback_returns_bound_user_and_supports_idempotent_payload(
    monkeypatch,
):
    repository = _ChallengeRepository()
    user = User(
        email="zhangsan@cnooc.com.cn",
        oauth_provider="cnooc",
        oauth_id="USER-001",
        display_name="张三",
    )
    provider = _Provider(user)
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: provider)
    client = _client(repository, user)
    challenge = "A" * 32
    payload = {"challenge": challenge, "callback_id": "cb-1", "timestamp": 123}

    first = client.post("/api/v1/data-lake/auth/callback", json=payload)
    second = client.post("/api/v1/data-lake/auth/callback", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json() == {
        "code": "accepted",
        "challenge": challenge,
        "user_info": {
            "subject": "USER-001",
            "account": "zhangsan",
            "email": "zhangsan@cnooc.com.cn",
            "display_name": "张三",
        },
    }
    assert repository.callback["challenge_hash"] == data_lake_auth._challenge_hash(challenge)
    assert first.headers["referrer-policy"] == "no-referrer"
    assert first.headers["x-request-id"]


def test_data_lake_callback_maps_expired_and_conflicting_challenges(monkeypatch):
    user = User(email="user@example.com")
    monkeypatch.setattr(data_lake_auth, "get_local_provider", lambda: _Provider(user))
    challenge = "B" * 32

    expired = _client(_ChallengeRepository(CallbackResolution("invalid_or_expired")), user).post("/api/v1/data-lake/auth/callback", json={"challenge": challenge})
    replayed = _client(_ChallengeRepository(CallbackResolution("replayed")), user).post("/api/v1/data-lake/auth/callback", json={"challenge": challenge})
    mismatch = _client(_ChallengeRepository(CallbackResolution("payload_mismatch")), user).post("/api/v1/data-lake/auth/callback", json={"challenge": challenge})

    assert expired.status_code == replayed.status_code == mismatch.status_code == 409
    assert expired.json()["code"] == "invalid_or_expired_challenge"
    assert replayed.json()["code"] == "callback_replayed"
    assert mismatch.json()["code"] == "callback_payload_mismatch"


def test_data_lake_callback_rejects_unknown_fields_and_oversized_body():
    user = User(email="user@example.com")
    client = _client(_ChallengeRepository(), user)

    invalid = client.post(
        "/api/v1/data-lake/auth/callback",
        json={"challenge": "C" * 32, "unexpected": True},
    )
    oversized = client.post(
        "/api/v1/data-lake/auth/callback",
        content=b"{" + b"x" * (16 * 1024) + b"}",
        headers={"content-type": "application/json"},
    )
    out_of_range_timestamp = client.post(
        "/api/v1/data-lake/auth/callback",
        json={"challenge": "E" * 32, "timestamp": 2**63},
    )

    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_request"
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "request_too_large"
    assert out_of_range_timestamp.status_code == 400
    assert out_of_range_timestamp.json()["code"] == "invalid_request"


def test_data_lake_callback_is_public_and_csrf_exempt():
    assert _is_public("/api/v1/data-lake/auth/callback") is True
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/data-lake/auth/callback",
            "raw_path": b"/api/v1/data-lake/auth/callback",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("example.com", 443),
        }
    )
    assert should_check_csrf(request) is False


def test_data_lake_callback_openapi_contract_matches_provider_operation():
    schema = _client(_ChallengeRepository(), User(email="user@example.com")).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/data-lake/auth/callback"]["post"]

    assert operation["operationId"] == "receiveDataLakeAuthCallback"
    assert set(operation["responses"]) >= {"200", "400", "409", "413", "429", "500"}
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    request_schema = request_body["content"]["application/json"]["schema"]
    assert request_schema["required"] == ["challenge"]
    assert set(request_schema["properties"]) == {
        "challenge",
        "callback_id",
        "timestamp",
    }
    assert request_schema["properties"]["timestamp"]["format"] == "int64"


def test_callback_payload_hash_is_order_independent():
    first = data_lake_auth.DataLakeCallbackRequest(
        challenge="D" * 32,
        callback_id="callback",
        timestamp=123,
    )
    second = data_lake_auth.DataLakeCallbackRequest.model_validate({"timestamp": 123, "challenge": "D" * 32, "callback_id": "callback"})

    assert data_lake_auth._callback_payload_hash(first) == data_lake_auth._callback_payload_hash(second)


async def _create_repository(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[UserRow.__table__, DataLakeAuthChallengeRow.__table__],
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(UserRow(id="user-1", email="user@example.com", password_hash=None))
        await session.commit()
    return engine, DataLakeAuthChallengeRepository(session_factory)


@pytest.mark.asyncio
async def test_persisted_challenge_is_one_time_and_identical_retry_is_idempotent(
    tmp_path,
):
    engine, repository = await _create_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        await repository.create(
            challenge_hash="a" * 64,
            user_id="user-1",
            expires_at=now + timedelta(seconds=60),
        )
        accepted = await repository.accept_callback(
            challenge_hash="a" * 64,
            callback_id="cb-1",
            callback_timestamp=123,
            payload_hash="b" * 64,
            now=now,
        )
        idempotent = await repository.accept_callback(
            challenge_hash="a" * 64,
            callback_id="cb-1",
            callback_timestamp=123,
            payload_hash="b" * 64,
            now=now + timedelta(minutes=2),
        )
        mismatch = await repository.accept_callback(
            challenge_hash="a" * 64,
            callback_id="cb-1",
            callback_timestamp=124,
            payload_hash="c" * 64,
            now=now + timedelta(seconds=1),
        )
    finally:
        await engine.dispose()

    assert accepted == idempotent == CallbackResolution("accepted", "user-1")
    assert mismatch == CallbackResolution("payload_mismatch")


@pytest.mark.asyncio
async def test_unconsumed_challenge_expires_after_60_seconds(tmp_path):
    engine, repository = await _create_repository(tmp_path)
    now = datetime.now(UTC)
    try:
        await repository.create(
            challenge_hash="d" * 64,
            user_id="user-1",
            expires_at=now + timedelta(seconds=60),
        )
        expired = await repository.accept_callback(
            challenge_hash="d" * 64,
            callback_id=None,
            callback_timestamp=None,
            payload_hash="e" * 64,
            now=now + timedelta(seconds=61),
        )
    finally:
        await engine.dispose()

    assert expired == CallbackResolution("invalid_or_expired")


@pytest.mark.asyncio
async def test_user_repository_rejects_a_platform_token_bound_to_another_user(
    tmp_path,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[UserRow.__table__],
            )
        )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all(
            [
                UserRow(
                    id="11111111-1111-4111-8111-111111111111",
                    email="one@example.com",
                    password_hash=None,
                ),
                UserRow(
                    id="22222222-2222-4222-8222-222222222222",
                    email="two@example.com",
                    password_hash=None,
                ),
            ]
        )
        await session.commit()

    repository = SQLiteUserRepository(session_factory)
    token_hash = _token_hash("permanent-platform-token")
    try:
        bound = await repository.bind_cnooc_platform_token("11111111-1111-4111-8111-111111111111", token_hash)
        with pytest.raises(ValueError, match="already bound"):
            await repository.bind_cnooc_platform_token("22222222-2222-4222-8222-222222222222", token_hash)
        stale_profile = bound.model_copy(
            update={
                "cnooc_platform_token_hash": None,
                "display_name": "Updated profile",
            }
        )
        await repository.update_user(stale_profile)
        unchanged = await repository.get_user_by_id("11111111-1111-4111-8111-111111111111")
    finally:
        await engine.dispose()

    assert bound.cnooc_platform_token_hash == token_hash
    assert unchanged is not None
    assert unchanged.cnooc_platform_token_hash == token_hash
    assert unchanged.display_name == "Updated profile"
