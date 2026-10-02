"""Enterprise SSO-only authentication boundary tests."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.config import AuthConfig
from app.gateway.routers.auth import router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def sso_only_config() -> AuthConfig:
    return AuthConfig(jwt_secret="test-secret", sso_only=True)


@pytest.mark.parametrize(
    ("path", "body", "content_type"),
    [
        (
            "/api/v1/auth/login/local",
            "username=user%40example.com&password=Secret123%21",
            "application/x-www-form-urlencoded",
        ),
        (
            "/api/v1/auth/register",
            '{"email":"user@example.com","password":"Secret123!"}',
            "application/json",
        ),
        (
            "/api/v1/auth/initialize",
            '{"email":"admin@example.com","password":"Secret123!"}',
            "application/json",
        ),
    ],
)
def test_sso_only_mode_rejects_local_credential_entrypoints(
    client: TestClient,
    sso_only_config: AuthConfig,
    path: str,
    body: str,
    content_type: str,
) -> None:
    with patch(
        "app.gateway.routers.auth.get_auth_config",
        return_value=sso_only_config,
    ):
        response = client.post(
            path,
            content=body,
            headers={"content-type": content_type},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "local_auth_disabled",
        "message": "Local authentication is disabled. Sign in through CNOOC DataLake SSO.",
    }


def test_sso_only_mode_rejects_generic_oidc_login(
    client: TestClient,
    sso_only_config: AuthConfig,
) -> None:
    with patch(
        "app.gateway.routers.auth.get_auth_config",
        return_value=sso_only_config,
    ):
        response = client.get("/api/v1/auth/oauth/github")

    assert response.status_code == 403


def test_sso_only_mode_hides_generic_oidc_providers(
    client: TestClient,
    sso_only_config: AuthConfig,
) -> None:
    with patch(
        "app.gateway.routers.auth.get_auth_config",
        return_value=sso_only_config,
    ), patch(
        "deerflow.config.app_config.get_app_config",
        return_value=SimpleNamespace(
            auth=SimpleNamespace(
                iam=SimpleNamespace(enabled=False),
                oidc=SimpleNamespace(enabled=True, providers={"github": SimpleNamespace(display_name="GitHub")}),
            )
        ),
    ):
        response = client.get("/api/v1/auth/providers")

    assert response.status_code == 200
    assert response.json() == {"providers": [], "sso_only": True}


def test_sso_only_mode_preserves_enabled_iam_provider(
    client: TestClient,
    sso_only_config: AuthConfig,
) -> None:
    with patch(
        "app.gateway.routers.auth.get_auth_config",
        return_value=sso_only_config,
    ), patch(
        "deerflow.config.app_config.get_app_config",
        return_value=SimpleNamespace(
            auth=SimpleNamespace(
                iam=SimpleNamespace(enabled=True),
                oidc=SimpleNamespace(enabled=True, providers={"github": SimpleNamespace(display_name="GitHub")}),
            )
        ),
    ):
        response = client.get("/api/v1/auth/providers")

    assert response.status_code == 200
    assert response.json() == {"providers": [{"id": "iam", "display_name": "CNOOC IAM", "type": "iam"}], "sso_only": True}
