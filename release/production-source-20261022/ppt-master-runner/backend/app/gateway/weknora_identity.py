"""DeerFlow-to-WeKnora identity bridge.

DeerFlow owns browser authentication. WeKnora owns document ingestion,
tenant/RBAC, and RAG. This module maps the current DeerFlow user to a WeKnora
user with the same email and a deterministic service-managed password, then
returns a normal WeKnora login response. Downstream calls can use the returned
Bearer token so WeKnora enforces per-user tenant and share permissions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT_S = 300.0
_DEFAULT_BUILTIN_TENANT_ID = 10000
_DEFAULT_CHAT_MODEL_ID = "deerflow-qwen-plus"
_DEFAULT_DEEPSEEK_MODEL_ID = "deerflow-deepseek-v4-pro"
_DEFAULT_CNOOC_DEEPSEEK_MODEL_ID = "deerflow-cnooc-deepseek-v4-flash"
_DEFAULT_CNOOC_DEEPSEEK_MODEL_NAME = "DeepSeek-V4-Flash"
_DEFAULT_CNOOC_DEEPSEEK_BASE_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
_DEFAULT_EMBEDDING_MODEL_ID = "deerflow-dashscope-embedding"
_DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_EMBEDDING_BASE_URL = "http://gateway:8001/api/weknora/bailian/v1"
_DEFAULT_RERANK_MODEL_ID = "deerflow-dashscope-rerank"
_DEFAULT_RERANK_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
_DEFAULT_CNOOC_EMBEDDING_MODEL_ID = "deerflow-cnooc-qwen3-embedding-8b"
_DEFAULT_CNOOC_EMBEDDING_MODEL_NAME = "Qwen3-Embedding-8B"
_DEFAULT_CNOOC_RERANK_MODEL_ID = "deerflow-cnooc-qwen3-reranker-8b"
_DEFAULT_CNOOC_RERANK_MODEL_NAME = "Qwen3-Reranker-8B"
_DEFAULT_CNOOC_RETRIEVAL_BASE_URL = "http://gateway:8001/api/weknora/internal-models/v1"
_DEFAULT_VISION_MODEL_ID = "deerflow-dashscope-vlm"
_DEFAULT_ASR_MODEL_ID = "deerflow-dashscope-asr"
_DEFAULT_ASR_BASE_URL = "http://gateway:8001/api/weknora/bailian/v1"
_DEFAULT_MODELS_MANAGED_BY = "deerflow-sso"

_default_models_lock = asyncio.Lock()
_default_models_seeded = False
_session_cache_lock = asyncio.Lock()
_session_cache: dict[str, tuple[float, Any]] = {}

_WEKNORA_STANDARD_NAVIGATION = [
    "/platform/creatChat",
    "/platform/knowledge-bases",
    "/platform/organizations",
    "/platform/agents",
]


@dataclass(frozen=True)
class WeKnoraSession:
    token: str
    refresh_token: str
    user: dict[str, Any]
    tenant: dict[str, Any]
    memberships: list[dict[str, Any]]

    def as_login_response(self, *, ui_policy: dict[str, Any] | None = None) -> dict[str, Any]:
        response = {
            "success": True,
            "user": self.user,
            "tenant": self.tenant,
            "active_tenant": self.tenant,
            "memberships": self.memberships,
            "token": self.token,
            "refresh_token": self.refresh_token,
        }
        if ui_policy is not None:
            response["ui_policy"] = ui_policy
        return response


@dataclass(frozen=True)
class _BuiltinModel:
    id: str
    tenant_id: int
    name: str
    type: str
    source: str
    description: str
    parameters: dict[str, Any]
    is_default: bool = True


def build_weknora_ui_policy(user: Any) -> dict[str, Any]:
    """Return the DeerFlow-owned presentation policy for native WeKnora.

    WeKnora remains the authorization source of truth. This policy only
    controls which native navigation and settings surfaces are presented in
    the DeerFlow embedding. The deployment separately disables ordinary
    self-service tenant creation in WeKnora itself.
    """

    is_admin = str(getattr(user, "system_role", "user") or "user").lower() == "admin"
    if is_admin:
        return {
            "mode": "system_admin",
            "role_label": "系统管理员",
            "full_ui": True,
            "can_create_workspace": True,
            "default_path": "/platform/knowledge-bases",
            "allowed_navigation": ["*"],
        }
    return {
        "mode": "personal_workspace_owner",
        "role_label": "个人工作空间所有者",
        "full_ui": False,
        "can_create_workspace": False,
        "default_path": "/platform/knowledge-bases",
        "allowed_navigation": list(_WEKNORA_STANDARD_NAVIGATION),
    }


_WEKNORA_CONFIG_TOOL_NAMES = (
    "weknora_search",
    "weknora_ask",
    "weknora_list_knowledge_bases",
    "weknora_get_knowledge_base",
    "weknora_list_documents",
    "weknora_get_document",
    "weknora_list_document_chunks",
    "weknora_read_document",
    "weknora_list_agents",
    "weknora_invoke_agent",
)


def _tool_extra(tool_name: str = "weknora_search") -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return {}
    extra = config.model_extra or {}
    return extra if isinstance(extra, dict) else {}


def _string_config(key: str, env_name: str | None = None, default: str | None = None) -> str | None:
    for tool_name in _WEKNORA_CONFIG_TOOL_NAMES:
        value = _tool_extra(tool_name).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if env_name:
        env_value = os.getenv(env_name)
        if env_value and env_value.strip():
            return env_value.strip()
    return default


def weknora_api_url() -> str:
    return (_string_config("api_url", "WEKNORA_API_URL", _DEFAULT_API_URL) or _DEFAULT_API_URL).rstrip("/")


def weknora_timeout() -> float:
    raw = os.getenv("WEKNORA_TIMEOUT_SECONDS")
    for tool_name in _WEKNORA_CONFIG_TOOL_NAMES:
        value = _tool_extra(tool_name).get("timeout_seconds")
        if value is not None:
            raw = value
            break
    try:
        return max(1.0, float(raw)) if raw is not None else _DEFAULT_TIMEOUT_S
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def weknora_endpoint(path: str) -> str:
    base = weknora_api_url()
    if base.endswith("/api/v1"):
        return f"{base}{path}"
    return f"{base}/api/v1{path}"


def _sso_secret() -> str:
    value = os.getenv("WEKNORA_SSO_SECRET") or os.getenv("AUTH_JWT_SECRET") or os.getenv("WEKNORA_API_KEY") or os.getenv("INTERNAL_API_TOKEN")
    if value and value.strip():
        return value.strip()
    raise HTTPException(status_code=503, detail="WeKnora SSO secret is not configured.")


def _username(email: str, user_id: str) -> str:
    prefix = email.split("@", 1)[0].lower()
    prefix = re.sub(r"[^a-z0-9_-]+", "-", prefix).strip("-_") or "deerflow"
    suffix = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{suffix}"[:50]


def _legacy_password(user_id: str, email: str) -> str:
    """Return the credential used before WeKnora enforced its 32-char limit."""

    digest = hmac.new(_sso_secret().encode(), f"{user_id}:{email}".encode(), hashlib.sha256).digest()
    return "Df!" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _password(user_id: str, email: str) -> str:
    """Return a deterministic credential accepted by WeKnora 0.7 and 0.8.

    WeKnora 0.8 validates new registrations against an 8-32 character
    password policy.  The fixed prefix guarantees every required character
    class while the scoped HMAC keeps credentials stable and user-specific.
    """

    digest = hmac.new(
        _sso_secret().encode(),
        f"weknora-password-v2:{user_id}:{email}".encode(),
        hashlib.sha256,
    ).digest()
    return "Df9!" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:28]


def _password_candidates(user_id: str, email: str) -> tuple[str, ...]:
    current = _password(user_id, email)
    legacy = _legacy_password(user_id, email)
    return (current,) if current == legacy else (current, legacy)


def _normalise_login_response(data: dict[str, Any]) -> WeKnoraSession:
    if not data.get("success", True):
        raise HTTPException(status_code=502, detail=data.get("message") or "WeKnora login failed.")

    token = str(data.get("token") or "")
    refresh_token = str(data.get("refresh_token") or "")
    user = data.get("user")
    tenant = data.get("active_tenant") or data.get("tenant")
    memberships = data.get("memberships") or []
    if not token or not isinstance(user, dict) or not isinstance(tenant, dict):
        raise HTTPException(status_code=502, detail="Unexpected WeKnora login response.")
    return WeKnoraSession(
        token=token,
        refresh_token=refresh_token,
        user=user,
        tenant=tenant,
        memberships=memberships if isinstance(memberships, list) else [],
    )


def _truthy_env(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Ignoring invalid integer value for %s", name)
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Ignoring invalid float value for %s", name)
        return default


def _model_env(name: str, default: str | None = None, *fallback_names: str) -> str:
    for env_name in (name, *fallback_names):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
    return default or ""


def _weknora_default_models() -> list[_BuiltinModel]:
    tenant_id = _int_env("WEKNORA_BUILTIN_MODELS_TENANT_ID", _DEFAULT_BUILTIN_TENANT_ID)
    internal_only = _truthy_env("WEKNORA_ENTERPRISE_INTERNAL_MODELS_ONLY", False)
    llm_api_key = _model_env("LLM_API_KEY", "", "DASHSCOPE_API_KEY")
    deepseek_api_key = _model_env("WEKNORA_DEEPSEEK_API_KEY", "", "LLM_API_KEY", "DASHSCOPE_API_KEY")
    cnooc_deepseek_api_key = _model_env("CNOOC_DEEPSEEK_API_KEY", "", "DeepSeek_V4_API_KEY")
    prefer_cnooc_deepseek = bool(cnooc_deepseek_api_key) and _truthy_env("WEKNORA_PREFER_CNOOC_DEEPSEEK", True)
    embedding_api_key = _model_env("EMBEDDING_API_KEY", "", "DASHSCOPE_API_KEY", "LLM_API_KEY")
    rerank_api_key = _model_env("RERANK_API_KEY", "", "LLM_API_KEY", "DASHSCOPE_API_KEY")
    vision_api_key = _model_env("VLM_API_KEY", "", "LLM_API_KEY", "DASHSCOPE_API_KEY")
    asr_api_key = _model_env("ASR_API_KEY", "", "LLM_API_KEY", "DASHSCOPE_API_KEY")
    internal_relay_key = _model_env("WEKNORA_INTERNAL_MODEL_RELAY_KEY")
    if internal_only and (not cnooc_deepseek_api_key or not internal_relay_key or not vision_api_key or not asr_api_key):
        logger.warning("Skipping WeKnora enterprise model seed because an internal DeepSeek, internal relay, VLM, or ASR credential is missing.")
        return []
    if not internal_only and (not llm_api_key or not embedding_api_key):
        logger.warning("Skipping WeKnora default model seed because LLM_API_KEY or EMBEDDING_API_KEY is missing.")
        return []

    models: list[_BuiltinModel] = []
    if not internal_only:
        models.extend(
            [
                _BuiltinModel(
                    id=os.getenv("WEKNORA_DEFAULT_SUMMARY_MODEL_ID", _DEFAULT_CHAT_MODEL_ID),
                    tenant_id=tenant_id,
                    name=_model_env("LLM_MODEL_NAME", "qwen-plus"),
                    type="KnowledgeQA",
                    source="remote",
                    description="DeerFlow WeKnora chat model",
                    parameters={
                        "base_url": _model_env("LLM_BASE_URL", _DEFAULT_DASHSCOPE_BASE_URL),
                        "api_key": llm_api_key,
                        "provider": _model_env("LLM_PROVIDER", "aliyun"),
                    },
                    is_default=not prefer_cnooc_deepseek,
                ),
                _BuiltinModel(
                    id=os.getenv("WEKNORA_DEEPSEEK_MODEL_ID", _DEFAULT_DEEPSEEK_MODEL_ID),
                    tenant_id=tenant_id,
                    name=_model_env("DEEPSEEK_MODEL_NAME", "deepseek-v4-pro"),
                    type="KnowledgeQA",
                    source="remote",
                    description="DeerFlow WeKnora DeepSeek chat model",
                    parameters={
                        "base_url": _model_env("DEEPSEEK_BASE_URL", _DEFAULT_DASHSCOPE_BASE_URL),
                        "api_key": deepseek_api_key,
                        "provider": _model_env("DEEPSEEK_PROVIDER", "aliyun"),
                    },
                    is_default=False,
                ),
            ]
        )
    if cnooc_deepseek_api_key:
        models.append(
            _BuiltinModel(
                id=os.getenv("WEKNORA_CNOOC_DEEPSEEK_MODEL_ID", _DEFAULT_CNOOC_DEEPSEEK_MODEL_ID),
                tenant_id=tenant_id,
                name=_model_env("CNOOC_DEEPSEEK_MODEL_NAME", _DEFAULT_CNOOC_DEEPSEEK_MODEL_NAME),
                type="KnowledgeQA",
                source="remote",
                description="DeerFlow WeKnora CNOOC DeepSeek chat model",
                parameters={
                    "base_url": _model_env("CNOOC_DEEPSEEK_BASE_URL", _DEFAULT_CNOOC_DEEPSEEK_BASE_URL),
                    "api_key": cnooc_deepseek_api_key,
                    "provider": _model_env("CNOOC_DEEPSEEK_PROVIDER", "generic"),
                },
                is_default=internal_only or prefer_cnooc_deepseek,
            )
        )
    if internal_only:
        embedding_model = _BuiltinModel(
            id=os.getenv("WEKNORA_DEFAULT_EMBEDDING_MODEL_ID", _DEFAULT_CNOOC_EMBEDDING_MODEL_ID),
            tenant_id=tenant_id,
            name=_model_env("CNOOC_EMBEDDING_MODEL_NAME", _DEFAULT_CNOOC_EMBEDDING_MODEL_NAME),
            type="Embedding",
            source="remote",
            description="CNOOC internal Qwen3 embedding model via DeerFlow Gateway",
            parameters={
                "base_url": _model_env(
                    "CNOOC_RETRIEVAL_RELAY_BASE_URL",
                    _DEFAULT_CNOOC_RETRIEVAL_BASE_URL,
                ),
                "api_key": internal_relay_key,
                "provider": "generic",
                "embedding_parameters": {
                    "dimension": _int_env("CNOOC_EMBEDDING_DIMENSION", 1024),
                    "truncate_prompt_tokens": _int_env("CNOOC_EMBEDDING_TRUNCATE_PROMPT_TOKENS", 0),
                },
            },
        )
        rerank_model = _BuiltinModel(
            id=os.getenv("WEKNORA_DEFAULT_RERANK_MODEL_ID", _DEFAULT_CNOOC_RERANK_MODEL_ID),
            tenant_id=tenant_id,
            name=_model_env("CNOOC_RERANK_MODEL_NAME", _DEFAULT_CNOOC_RERANK_MODEL_NAME),
            type="Rerank",
            source="remote",
            description="CNOOC internal Qwen3 rerank model via DeerFlow Gateway",
            parameters={
                "base_url": _model_env(
                    "CNOOC_RETRIEVAL_RELAY_BASE_URL",
                    _DEFAULT_CNOOC_RETRIEVAL_BASE_URL,
                ),
                "api_key": internal_relay_key,
                "provider": "generic",
            },
        )
    else:
        embedding_model = _BuiltinModel(
            id=os.getenv("WEKNORA_DEFAULT_EMBEDDING_MODEL_ID", _DEFAULT_EMBEDDING_MODEL_ID),
            tenant_id=tenant_id,
            name=_model_env("EMBEDDING_MODEL_NAME", "text-embedding-v4"),
            type="Embedding",
            source="remote",
            description="DeerFlow WeKnora embedding model",
            parameters={
                "base_url": _model_env("EMBEDDING_BASE_URL", _DEFAULT_EMBEDDING_BASE_URL),
                "api_key": embedding_api_key,
                "provider": _model_env("EMBEDDING_PROVIDER", "generic"),
                "embedding_parameters": {
                    "dimension": _int_env("EMBEDDING_DIMENSION", 1024),
                    "truncate_prompt_tokens": _int_env("EMBEDDING_TRUNCATE_PROMPT_TOKENS", 0),
                },
            },
        )
        rerank_model = _BuiltinModel(
            id=os.getenv("WEKNORA_DEFAULT_RERANK_MODEL_ID", _DEFAULT_RERANK_MODEL_ID),
            tenant_id=tenant_id,
            name=_model_env("RERANK_MODEL_NAME", "qwen3-rerank"),
            type="Rerank",
            source="remote",
            description="DeerFlow WeKnora rerank model",
            parameters={
                "base_url": _model_env("RERANK_BASE_URL", _DEFAULT_RERANK_BASE_URL),
                "api_key": rerank_api_key,
                "provider": _model_env("RERANK_PROVIDER", "aliyun"),
            },
        )
    models.extend(
        [
            embedding_model,
            rerank_model,
            _BuiltinModel(
                id=os.getenv("WEKNORA_DEFAULT_VISION_MODEL_ID", _DEFAULT_VISION_MODEL_ID),
                tenant_id=tenant_id,
                name=_model_env("VLM_MODEL_NAME", "qwen3-vl-plus"),
                type="VLLM",
                source="remote",
                description="DeerFlow WeKnora vision model",
                parameters={
                    "base_url": _model_env("VLM_BASE_URL", _DEFAULT_DASHSCOPE_BASE_URL),
                    "api_key": vision_api_key,
                    "provider": _model_env("VLM_PROVIDER", "aliyun"),
                },
            ),
            _BuiltinModel(
                id=os.getenv("WEKNORA_DEFAULT_ASR_MODEL_ID", _DEFAULT_ASR_MODEL_ID),
                tenant_id=tenant_id,
                name=_model_env("ASR_MODEL_NAME", "qwen3-asr-flash"),
                type="ASR",
                source="remote",
                description="DeerFlow WeKnora speech recognition model",
                parameters={
                    "base_url": _model_env("ASR_BASE_URL", _DEFAULT_ASR_BASE_URL),
                    "api_key": asr_api_key,
                    "provider": _model_env("ASR_PROVIDER", "generic"),
                },
            ),
        ]
    )
    return models


def _weknora_database_is_configured() -> bool:
    return bool(os.getenv("WEKNORA_DATABASE_URL") or os.getenv("WEKNORA_DB_PASSWORD"))


async def _connect_weknora_database() -> Any:
    import asyncpg

    timeout = max(1.0, _float_env("WEKNORA_DB_CONNECT_TIMEOUT_SECONDS", 3.0))
    database_url = os.getenv("WEKNORA_DATABASE_URL")
    if database_url:
        return await asyncpg.connect(database_url, timeout=timeout)

    return await asyncpg.connect(
        host=os.getenv("WEKNORA_DB_HOST", "weknora-postgres"),
        port=_int_env("WEKNORA_DB_PORT", 5432),
        user=os.getenv("WEKNORA_DB_USER", "postgres"),
        password=os.getenv("WEKNORA_DB_PASSWORD"),
        database=os.getenv("WEKNORA_DB_NAME", "WeKnora"),
        timeout=timeout,
    )


async def sync_weknora_role(user: Any, session: WeKnoraSession) -> WeKnoraSession:
    """Mirror DeerFlow's system role onto the matching WeKnora user."""

    if not _weknora_database_is_configured():
        return session

    is_admin = str(getattr(user, "system_role", "user") or "user").lower() == "admin"
    user_id = str(session.user.get("id") or "")
    email = str(session.user.get("email") or getattr(user, "email", "") or "").strip().lower()
    if not user_id or not email:
        return session

    try:
        connection = await _connect_weknora_database()
    except Exception:
        logger.warning("Failed to connect to WeKnora database while syncing the DeerFlow role.", exc_info=True)
        return session

    try:
        await connection.execute(
            """
            UPDATE users
            SET is_system_admin = $1,
                can_access_all_tenants = $1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $2 OR lower(email) = $3
            """,
            is_admin,
            user_id,
            email,
        )
    except Exception:
        logger.warning("Failed to sync DeerFlow role for WeKnora user %s.", user_id, exc_info=True)
        return session
    finally:
        await connection.close()

    synced_user = {
        **session.user,
        "is_system_admin": is_admin,
        "can_access_all_tenants": is_admin,
    }
    return WeKnoraSession(
        token=session.token,
        refresh_token=session.refresh_token,
        user=synced_user,
        tenant=session.tenant,
        memberships=session.memberships,
    )


async def ensure_weknora_default_models() -> None:
    """Ensure DeerFlow's platform built-in WeKnora models exist.

    WeKnora treats rows with ``is_builtin=true`` as platform models visible to
    every tenant. The SSO bridge calls this best-effort seed path so a restored
    or migrated WeKnora database does not strand new tenants without the default
    chat and embedding models.
    """

    global _default_models_seeded
    if _default_models_seeded or not _truthy_env("WEKNORA_DEFAULT_MODELS_AUTO_SEED", True):
        return
    if not _weknora_database_is_configured():
        logger.warning("Skipping WeKnora default model seed because WEKNORA_DATABASE_URL/WEKNORA_DB_PASSWORD is not configured.")
        return

    async with _default_models_lock:
        if _default_models_seeded:
            return
        models = _weknora_default_models()
        if not models:
            return

        try:
            connection = await _connect_weknora_database()
        except Exception:
            logger.warning("Failed to connect to WeKnora database while seeding default models.", exc_info=True)
            return

        try:
            async with connection.transaction():
                for model in models:
                    await connection.execute(
                        """
                        INSERT INTO models (
                            id, tenant_id, name, display_name, type, source,
                            description, parameters, is_default, status,
                            is_builtin, managed_by, updated_at, deleted_at
                        )
                        VALUES (
                            $1, $2, $3, '', $4, $5, $6, $7::jsonb,
                            $8, 'active', true, $9, CURRENT_TIMESTAMP, NULL
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            tenant_id = EXCLUDED.tenant_id,
                            name = EXCLUDED.name,
                            type = EXCLUDED.type,
                            source = EXCLUDED.source,
                            description = EXCLUDED.description,
                            parameters = EXCLUDED.parameters,
                            is_default = EXCLUDED.is_default,
                            status = 'active',
                            is_builtin = true,
                            managed_by = EXCLUDED.managed_by,
                            updated_at = CURRENT_TIMESTAMP,
                            deleted_at = NULL
                        """,
                        model.id,
                        model.tenant_id,
                        model.name,
                        model.type,
                        model.source,
                        model.description,
                        json.dumps(model.parameters, ensure_ascii=False),
                        model.is_default,
                        os.getenv("WEKNORA_DEFAULT_MODELS_MANAGED_BY", _DEFAULT_MODELS_MANAGED_BY),
                    )
            _default_models_seeded = True
            logger.info("Ensured WeKnora built-in default models are present.")
        except Exception:
            logger.warning("Failed to seed WeKnora built-in default models.", exc_info=True)
        finally:
            await connection.close()


async def ensure_weknora_default_mineru(session: WeKnoraSession) -> None:
    """Persist the deployment-managed MinerU adapter endpoint for owned spaces.

    The endpoint stored in WeKnora is the in-cluster compatibility adapter, not
    the enterprise API or its Bearer token. Existing parser settings are read
    first and preserved when the endpoint is added or refreshed.
    """

    endpoint = os.getenv("WEKNORA_MINERU_ENDPOINT", "").strip().rstrip("/")
    if not endpoint or not _truthy_env("WEKNORA_MINERU_AUTO_CONFIGURE", True):
        return

    managed_roles = {"owner", "admin"}
    tenant_ids = {str(item.get("tenant_id")) for item in session.memberships if isinstance(item, dict) and item.get("tenant_id") is not None and str(item.get("role") or "").strip().lower() in managed_roles}
    active_tenant_id = session.tenant.get("id") if isinstance(session.tenant, dict) else None
    if active_tenant_id is not None and not session.memberships:
        tenant_ids.add(str(active_tenant_id))
    if not tenant_ids:
        return

    url = weknora_endpoint("/tenants/kv/parser-engine-config")
    async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
        for tenant_id in sorted(tenant_ids):
            headers = {
                "Authorization": f"Bearer {session.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Tenant-ID": tenant_id,
            }
            try:
                response = await client.get(url, headers=headers)
                if response.status_code >= 400:
                    logger.warning(
                        "Unable to read WeKnora parser defaults for tenant %s: HTTP %s",
                        tenant_id,
                        response.status_code,
                    )
                    continue
                payload = response.json()
                current = payload.get("data") if isinstance(payload, dict) else None
                config = dict(current) if isinstance(current, dict) else {}
                if str(config.get("mineru_endpoint") or "").rstrip("/") == endpoint:
                    continue
                config["mineru_endpoint"] = endpoint
                update = await client.put(url, headers=headers, json=config)
                if update.status_code >= 400:
                    logger.warning(
                        "Unable to set WeKnora MinerU default for tenant %s: HTTP %s",
                        tenant_id,
                        update.status_code,
                    )
            except (httpx.HTTPError, ValueError):
                logger.warning(
                    "Failed to configure the WeKnora MinerU default for tenant %s.",
                    tenant_id,
                    exc_info=True,
                )


async def ensure_weknora_default_web_search(session: WeKnoraSession) -> None:
    """Ensure owned WeKnora workspaces use DeerFlow's Tavily search provider.

    The API key remains a runtime secret. It is sent only to WeKnora's native
    provider API, which owns credential storage, and is never returned to the
    browser or committed in deployment manifests.
    """

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key or not _truthy_env("WEKNORA_TAVILY_AUTO_CONFIGURE", True):
        return

    managed_roles = {"owner", "admin"}
    tenant_ids = {str(item.get("tenant_id")) for item in session.memberships if isinstance(item, dict) and item.get("tenant_id") is not None and str(item.get("role") or "").strip().lower() in managed_roles}
    active_tenant_id = session.tenant.get("id") if isinstance(session.tenant, dict) else None
    if active_tenant_id is not None and not session.memberships:
        tenant_ids.add(str(active_tenant_id))
    if not tenant_ids:
        return

    url = weknora_endpoint("/web-search-providers")
    async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
        for tenant_id in sorted(tenant_ids):
            headers = {
                "Authorization": f"Bearer {session.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Tenant-ID": tenant_id,
            }
            try:
                response = await client.get(url, headers=headers)
                if response.status_code >= 400:
                    logger.warning(
                        "Unable to read WeKnora web-search providers for tenant %s: HTTP %s",
                        tenant_id,
                        response.status_code,
                    )
                    continue
                response_payload = response.json()
                providers = response_payload.get("data") if isinstance(response_payload, dict) else None
                existing = next(
                    (provider for provider in providers or [] if isinstance(provider, dict) and str(provider.get("provider") or "").strip().lower() == "tavily"),
                    None,
                )
                if existing is not None:
                    provider_id = str(existing.get("id") or "").strip()
                    if not provider_id:
                        continue
                    parameters = existing.get("parameters")
                    parameters = parameters if isinstance(parameters, dict) else {}
                    update_payload = {
                        "name": str(existing.get("name") or "DeerFlow Tavily"),
                        "provider": "tavily",
                        "description": str(existing.get("description") or "DeerFlow enterprise default web search"),
                        "parameters": {
                            **{key: parameters[key] for key in ("engine_id", "base_url", "proxy_url") if parameters.get(key)},
                            "api_key": api_key,
                        },
                        "is_default": True,
                    }
                    update = await client.put(f"{url}/{provider_id}", headers=headers, json=update_payload)
                    if update.status_code >= 400:
                        logger.warning(
                            "Unable to synchronize the Tavily web-search provider for tenant %s: HTTP %s",
                            tenant_id,
                            update.status_code,
                        )
                    continue

                create_payload = {
                    "name": "DeerFlow Tavily",
                    "provider": "tavily",
                    "description": "DeerFlow enterprise default web search",
                    "parameters": {"api_key": api_key},
                    "is_default": True,
                }
                create = await client.post(url, headers=headers, json=create_payload)
                if create.status_code >= 400:
                    logger.warning(
                        "Unable to create the default WeKnora Tavily provider for tenant %s: HTTP %s",
                        tenant_id,
                        create.status_code,
                    )
            except (httpx.HTTPError, ValueError):
                logger.warning(
                    "Failed to configure the default WeKnora Tavily provider for tenant %s.",
                    tenant_id,
                    exc_info=True,
                )


async def _create_weknora_session(user: Any) -> WeKnoraSession:
    """Create/reuse the matching WeKnora account and return a user JWT."""

    user_id = str(getattr(user, "id", "") or "")
    email = str(getattr(user, "email", "") or "").strip().lower()
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Authenticated DeerFlow user is missing id or email.")

    username = _username(email, user_id)
    password = _password(user_id, email)
    register_payload = {"username": username, "email": email, "password": password}

    async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
        # Registration is idempotent for DeerFlow-provisioned users. WeKnora
        # returns 400 when the email already exists; in that case we simply try
        # login with the deterministic password.
        try:
            register_response = await client.post(weknora_endpoint("/auth/register"), json=register_payload)
            if register_response.status_code >= 400:
                logger.info("WeKnora register returned HTTP %s for DeerFlow user %s", register_response.status_code, user_id)
        except httpx.RequestError as exc:
            logger.warning("WeKnora registration request failed for user %s", user_id, exc_info=True)
            raise HTTPException(status_code=502, detail=f"WeKnora is unavailable: {exc}") from exc

        response: httpx.Response | None = None
        try:
            for candidate in _password_candidates(user_id, email):
                response = await client.post(
                    weknora_endpoint("/auth/login"),
                    json={"email": email, "password": candidate},
                )
                if response.status_code != 401:
                    break
        except httpx.RequestError as exc:
            logger.warning("WeKnora login request failed for user %s", user_id, exc_info=True)
            raise HTTPException(status_code=502, detail=f"WeKnora is unavailable: {exc}") from exc

    if response is None:
        raise HTTPException(status_code=502, detail="WeKnora login produced no response.")

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="WeKnora returned a non-JSON login response.") from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=409,
            detail=("A WeKnora account with this DeerFlow email already exists but was not provisioned by DeerFlow. Use a different DeerFlow email or reset/link the WeKnora account."),
        )
    if response.status_code >= 400:
        detail = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(status_code=response.status_code, detail=detail or data)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Unexpected WeKnora login response shape.")
    session = _normalise_login_response(data)
    session = await sync_weknora_role(user, session)
    await ensure_weknora_default_models()
    await ensure_weknora_default_mineru(session)
    await ensure_weknora_default_web_search(session)
    return session


def _session_cache_ttl() -> float:
    try:
        return max(0.0, float(os.getenv("WEKNORA_SESSION_CACHE_TTL_S", "900")))
    except ValueError:
        return 900.0


async def ensure_weknora_session_for_deerflow_user(user: Any) -> WeKnoraSession:
    """Return a cached WeKnora session, provisioning it only when needed."""

    user_id = str(getattr(user, "id", "") or "")
    email = str(getattr(user, "email", "") or "").strip().lower()
    role = str(getattr(user, "system_role", "user") or "user").lower()
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Authenticated DeerFlow user is missing id or email.")

    cache_key = f"{user_id}:{email}:{role}"
    now = time.monotonic()
    cached = _session_cache.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]

    async with _session_cache_lock:
        now = time.monotonic()
        cached = _session_cache.get(cache_key)
        if cached is not None and cached[0] > now:
            return cached[1]
        session = await _create_weknora_session(user)
        ttl = _session_cache_ttl()
        if ttl > 0:
            _session_cache[cache_key] = (now + ttl, session)
        return session
