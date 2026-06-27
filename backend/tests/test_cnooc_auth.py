import pytest

from app.gateway.auth import cnooc
from app.gateway.auth.cnooc import CnoocAuthError


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
