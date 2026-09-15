"""CRUD API for custom agents."""

import asyncio
import io
import logging
import os
import re
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, field_validator

from app.gateway.deps import require_admin_user
from app.gateway.external_agent_analytics import (
    EXTERNAL_PLATFORM_AGENT_NAMES,
    EXTERNAL_PLATFORM_CLICK_ACTION,
    external_platform_user_id,
    normalize_external_agent_name,
    normalize_external_agent_source,
)
from deerflow.agents.memory.manager import get_memory_manager
from deerflow.config.agents_api_config import get_agents_api_config
from deerflow.config.agents_config import (
    MAX_AGENT_NAME_LENGTH,
    AgentConfig,
    AgentModelSettings,
    is_personal_agent,
    list_custom_agents,
    list_team_agents,
    load_agent_config,
    load_agent_soul,
    preserve_non_managed_fields,
    validate_agent_display_name,
)
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.persistence.agents import AgentExistsError, get_agent_store
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])

AGENT_NAME_PATTERN = re.compile(r"^[\w\-\u3400-\u9fff]+$", re.UNICODE)
EXTERNAL_AGENT_NAMES = set(EXTERNAL_PLATFORM_AGENT_NAMES)
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_DIMENSION = 8192
MAX_DIGITAL_EMPLOYEE_TAGS = 8
MAX_DIGITAL_EMPLOYEE_TAG_LENGTH = 20
DIGITAL_EMPLOYEE_AVATAR_IDS = {
    "exploration-geologist",
    "seismic-processing",
    "reservoir-development",
    "drilling-completion",
    "offshore-engineering",
    "production-operations",
    "hse",
    "intelligent-oilfield-data",
    "report-research",
    "knowledge-service",
    "equipment-maintenance",
    "emergency-response",
    "senior-geologist",
    "rov-pilot",
    "offshore-medic",
    "environmental-analyst",
    "lng-engineer",
    "ai-scientist",
    "procurement-specialist",
    "helicopter-logistics",
    "platform-manager",
    "subsea-inspector",
    "finance-analyst",
    "renewables-engineer",
}
_CATEGORY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_categories_write_lock = asyncio.Lock()
ReasoningEffort = Literal["low", "medium", "high"]
_MODEL_BEHAVIOR_FIELDS = (
    "model",
    "model_settings",
    "thinking_enabled",
    "reasoning_effort",
)


def _normalize_tags(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for raw_tag in value:
        tag = raw_tag.strip()
        if not tag:
            continue
        if len(tag) > MAX_DIGITAL_EMPLOYEE_TAG_LENGTH:
            raise ValueError(f"Each tag must be {MAX_DIGITAL_EMPLOYEE_TAG_LENGTH} characters or fewer")
        folded = tag.casefold()
        if folded not in seen:
            seen.add(folded)
            result.append(tag)
    if len(result) > MAX_DIGITAL_EMPLOYEE_TAGS:
        raise ValueError(f"A digital employee can have at most {MAX_DIGITAL_EMPLOYEE_TAGS} tags")
    return result


def _validate_avatar_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized not in DIGITAL_EMPLOYEE_AVATAR_IDS:
        raise ValueError("Unknown preset avatar")
    return normalized


def _validate_category_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not _CATEGORY_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Category id must use lowercase letters, digits, and hyphens")
    return normalized


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name")
    display_name: str | None = Field(default=None, description="Editable Unicode display name")
    description: str = Field(default="", max_length=500, description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all, []=none)")
    category_id: str | None = Field(default=None, description="Admin-defined digital employee category")
    tags: list[str] = Field(default_factory=list, description="Search and discovery tags")
    avatar_id: str | None = Field(default=None, description="Bundled cartoon avatar identifier")
    avatar_url: str | None = Field(default=None, description="Same-origin custom avatar URL")
    model_settings: AgentModelSettings | None = Field(default=None, description="Per-employee sampling overrides")
    thinking_enabled: bool | None = Field(default=None, description="Per-employee thinking-mode default")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Per-employee reasoning effort")
    soul: str | None = Field(default=None, description="SOUL.md content")
    source: str | None = Field(default=None, description="Agent source: personal, team, or legacy")
    published_by_user_id: str | None = Field(default=None, description="User id that published this team agent")
    published_by_current_user: bool = Field(default=False, description="Whether the current user published this team agent")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(
        ...,
        max_length=MAX_AGENT_NAME_LENGTH,
        description="Agent name (Chinese characters, letters, digits, and hyphens)",
    )
    display_name: str | None = Field(default=None, max_length=80, description="Editable Unicode display name")
    description: str = Field(
        default="",
        max_length=500,
        description="Agent description",
    )
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all enabled, []=none)")
    category_id: str | None = Field(default=None, max_length=64, description="Digital employee category id")
    tags: list[str] = Field(default_factory=list, description="Search and discovery tags")
    avatar_id: str | None = Field(default=None, description="Bundled cartoon avatar identifier")
    model_settings: AgentModelSettings | None = Field(default=None, description="Per-employee sampling overrides")
    thinking_enabled: bool | None = Field(default=None, description="Per-employee thinking-mode default")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Per-employee reasoning effort")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")

    _clean_tags = field_validator("tags")(_normalize_tags)
    _clean_avatar_id = field_validator("avatar_id")(_validate_avatar_id)
    _clean_category_id = field_validator("category_id")(_validate_category_id)
    _clean_display_name = field_validator("display_name")(validate_agent_display_name)


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    display_name: str | None = Field(default=None, max_length=80, description="Updated display name")
    description: str | None = Field(default=None, max_length=500, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Updated skill whitelist (None=all, []=none)")
    category_id: str | None = Field(default=None, max_length=64, description="Updated category id")
    tags: list[str] | None = Field(default=None, description="Updated search and discovery tags")
    avatar_id: str | None = Field(default=None, description="Updated bundled avatar identifier")
    model_settings: AgentModelSettings | None = Field(default=None, description="Updated per-employee sampling overrides")
    thinking_enabled: bool | None = Field(default=None, description="Updated per-employee thinking-mode default")
    reasoning_effort: ReasoningEffort | None = Field(default=None, description="Updated per-employee reasoning effort")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")

    _clean_tags = field_validator("tags")(_normalize_tags)
    _clean_avatar_id = field_validator("avatar_id")(_validate_avatar_id)
    _clean_category_id = field_validator("category_id")(_validate_category_id)
    _clean_display_name = field_validator("display_name")(validate_agent_display_name)


class DigitalEmployeeCategory(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=32)
    description: str = Field(default="", max_length=160)
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _CATEGORY_ID_PATTERN.fullmatch(normalized):
            raise ValueError("Category id must use lowercase letters, digits, and hyphens")
        return normalized

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class DigitalEmployeeCategoriesResponse(BaseModel):
    items: list[DigitalEmployeeCategory]


DEFAULT_DIGITAL_EMPLOYEE_CATEGORIES = DigitalEmployeeCategoriesResponse(
    items=[
        DigitalEmployeeCategory(id="general", name="通用协作", description="跨业务的通用数字员工", sort_order=0),
        DigitalEmployeeCategory(id="exploration", name="勘探研究", description="地质、物探与勘探研究", sort_order=10),
        DigitalEmployeeCategory(id="development", name="开发生产", description="油藏、钻完井与生产运行", sort_order=20),
        DigitalEmployeeCategory(id="engineering", name="工程建设", description="海洋工程与设备设施", sort_order=30),
        DigitalEmployeeCategory(id="safety", name="安全环保", description="HSE、风险与应急管理", sort_order=40),
        DigitalEmployeeCategory(id="digital", name="数智技术", description="数据、算法与智能油田", sort_order=50),
        DigitalEmployeeCategory(id="research", name="研究与报告", description="研究分析、文档和报告编制", sort_order=60),
    ]
)


class AgentPublishRequest(BaseModel):
    """Request body for publishing a personal agent to the team square."""

    overwrite: bool = Field(default=True, description="Whether to replace an existing team agent with the same name")


class ExternalAgentClickRequest(BaseModel):
    agent_name: str = Field(..., max_length=128)


class ExternalPlatformAgentClickRequest(BaseModel):
    user_name: str = Field(default="", max_length=128)
    user_email: str = Field(default="", max_length=320)
    agent_name: str = Field(..., max_length=128)
    source: str = Field(..., max_length=64)


@router.post("/agent-analytics/external-click", status_code=204)
async def record_external_agent_click(body: ExternalAgentClickRequest) -> None:
    """Record a launch click for one of the five built-in external agents."""
    if body.agent_name not in EXTERNAL_AGENT_NAMES:
        raise HTTPException(status_code=422, detail="Unknown external agent")
    session_factory = get_session_factory()
    if session_factory is None:
        return
    async with session_factory() as session:
        session.add(
            AuditLogRow(
                user_id=get_effective_user_id(),
                user_email=None,
                user_role=None,
                action="external_agent.click",
                resource_type="external_agent",
                resource_id=body.agent_name,
                method="POST",
                path="/api/agent-analytics/external-click",
                status_code=204,
                result="success",
                duration_ms=0,
                client_ip=None,
                user_agent=None,
                error=None,
                details_json={},
            )
        )
        await session.commit()


def _external_click_ingest_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() == "bearer":
            return token.strip()
    return request.headers.get("x-external-agent-click-key", "").strip()


@router.post("/agent_click")
async def record_external_platform_agent_click(
    body: ExternalPlatformAgentClickRequest,
    request: Request,
) -> dict[str, str]:
    """Accept authenticated click events from the Haineng and collaboration apps."""
    expected = os.getenv("EXTERNAL_AGENT_CLICK_INGEST_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="External click ingestion is not configured")
    provided = _external_click_ingest_token(request)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid external click ingestion key")

    agent_name = normalize_external_agent_name(body.agent_name)
    if agent_name is None:
        raise HTTPException(status_code=422, detail="Unknown external agent")
    source = normalize_external_agent_source(body.source)
    if source is None:
        raise HTTPException(status_code=422, detail="Unknown external agent source")
    if not body.user_email.strip() and not body.user_name.strip():
        raise HTTPException(status_code=422, detail="user_email or user_name is required")

    session_factory = get_session_factory()
    if session_factory is None:
        raise HTTPException(status_code=503, detail="Persistence is not configured")
    async with session_factory() as session:
        session.add(
            AuditLogRow(
                user_id=external_platform_user_id(
                    source=source,
                    user_email=body.user_email,
                    user_name=body.user_name,
                ),
                user_email=body.user_email.strip().casefold() or None,
                user_role=None,
                action=EXTERNAL_PLATFORM_CLICK_ACTION,
                resource_type="external_agent",
                resource_id=agent_name,
                method="POST",
                path="/api/agent_click",
                status_code=200,
                result="success",
                duration_ms=0,
                client_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                error=None,
                details_json={
                    "source": source,
                    "user_name": body.user_name.strip(),
                    "reported_agent_name": body.agent_name.strip(),
                },
            )
        )
        await session.commit()
    return {"status": "ok"}


def _validate_agent_name(name: str) -> None:
    """Validate agent name against allowed pattern.

    Args:
        name: The agent name to validate.

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    is_safe = bool(name) and len(name) <= MAX_AGENT_NAME_LENGTH and AGENT_NAME_PATTERN.fullmatch(name) and all(char == "-" or unicodedata.category(char)[0] in {"L", "N"} for char in name)
    if not is_safe:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid agent name '{name}'. Use at most {MAX_AGENT_NAME_LENGTH} Chinese characters, letters, digits, and hyphens."),
        )


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name to lowercase for filesystem storage."""
    return name.lower()


def _require_agents_api_enabled() -> None:
    """Reject access unless the custom-agent management API is explicitly enabled."""
    if not get_agents_api_config().enabled:
        raise HTTPException(
            status_code=403,
            detail=("Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP."),
        )


def _validate_model_exists(model: str | None) -> None:
    if not model:
        return
    try:
        app_config = get_app_config()
    except Exception:
        logger.warning(
            "Could not load app config to validate employee model %r; skipping",
            model,
        )
        return
    if app_config.get_model_config(model) is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown model '{model}'. Select a configured model profile.",
        )


def _merge_model_settings_update(
    value: AgentModelSettings,
    existing: AgentModelSettings | None,
) -> dict:
    merged = existing.model_dump(exclude_none=True) if existing is not None else {}
    for field in value.model_fields_set:
        field_value = getattr(value, field)
        if field_value is None:
            merged.pop(field, None)
        else:
            merged[field] = field_value
    return merged


def _apply_model_behavior(
    config_data: dict,
    source: BaseModel,
    existing: AgentConfig | None = None,
) -> None:
    for field in _MODEL_BEHAVIOR_FIELDS:
        value = getattr(source, field) if field in source.model_fields_set else getattr(existing, field, None) if existing is not None else None
        if value is None:
            continue
        if field == "model_settings" and isinstance(value, AgentModelSettings):
            dumped = _merge_model_settings_update(
                value,
                existing.model_settings if existing is not None else None,
            )
            if dumped:
                config_data[field] = dumped
            continue
        config_data[field] = value.model_dump(exclude_none=True) if isinstance(value, BaseModel) else value


def _category_config_path() -> Path:
    return get_paths().base_dir / "digital_employee_categories.yaml"


def _load_digital_employee_categories() -> DigitalEmployeeCategoriesResponse:
    path = _category_config_path()
    if not path.exists():
        return DEFAULT_DIGITAL_EMPLOYEE_CATEGORIES.model_copy(deep=True)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    categories = DigitalEmployeeCategoriesResponse.model_validate(payload)
    categories.items.sort(key=lambda item: (item.sort_order, item.name.casefold()))
    return categories


def _ensure_category_exists(category_id: str | None) -> None:
    if category_id is None:
        return
    configured = {item.id for item in _load_digital_employee_categories().items}
    if category_id not in configured:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown digital employee category '{category_id}'",
        )


def _agent_exists(name: str, user_id: str | None) -> bool:
    """Resolve the configured store and check ownership in one worker call."""
    return get_agent_store().exists(name, user_id=user_id)


def _write_digital_employee_categories(categories: DigitalEmployeeCategoriesResponse) -> None:
    path = _category_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(
            yaml.safe_dump(
                categories.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _find_category_usage(category_ids: set[str]) -> dict[str, list[str]]:
    """Return bounded owner/name references for categories that are still used."""
    usage: dict[str, list[str]] = {category_id: [] for category_id in category_ids}
    for owner_id, config in get_agent_store().list_all():
        if config.category_id in usage and len(usage[config.category_id]) < 10:
            usage[config.category_id].append(f"{owner_id}/{config.name}")
    for config in list_team_agents():
        if config.category_id in usage and len(usage[config.category_id]) < 10:
            usage[config.category_id].append(f"team/{config.name}")
    return {category_id: refs for category_id, refs in usage.items() if refs}


def _avatar_path(agent_dir: Path) -> Path:
    return agent_dir / "avatar.webp"


def _avatar_url(name: str, source: str, agent_dir: Path) -> str | None:
    if not _avatar_path(agent_dir).is_file():
        return None
    encoded_name = quote(name, safe="")
    if source in {"team", "legacy"}:
        return f"/api/team-agents/{encoded_name}/avatar"
    return f"/api/agents/{encoded_name}/avatar"


def _encode_custom_avatar(payload: bytes, destination: Path) -> None:
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            if getattr(image, "is_animated", False):
                raise ValueError("Animated avatars are not supported")
            if max(image.size) > MAX_AVATAR_DIMENSION:
                raise ValueError("Avatar dimensions are too large")
            image = ImageOps.exif_transpose(image)
            image = ImageOps.fit(
                image.convert("RGB"),
                (512, 512),
                method=Image.Resampling.LANCZOS,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
            try:
                image.save(temporary, format="WEBP", quality=86, method=6)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Avatar must be a valid PNG, JPEG, or WebP image") from exc


@router.get(
    "/digital-employee-categories",
    response_model=DigitalEmployeeCategoriesResponse,
    summary="List Digital Employee Categories",
)
async def list_digital_employee_categories() -> DigitalEmployeeCategoriesResponse:
    _require_agents_api_enabled()
    try:
        return await asyncio.to_thread(_load_digital_employee_categories)
    except Exception as exc:
        logger.error("Failed to load digital employee categories", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load digital employee categories: {exc}",
        ) from exc


@router.put(
    "/admin/digital-employee-categories",
    response_model=DigitalEmployeeCategoriesResponse,
    summary="Replace Digital Employee Categories",
)
async def replace_digital_employee_categories(
    request: Request,
    body: DigitalEmployeeCategoriesResponse,
) -> DigitalEmployeeCategoriesResponse:
    _require_agents_api_enabled()
    await require_admin_user(
        request,
        detail="Admin privileges are required to manage digital employee categories",
    )
    ids = [item.id for item in body.items]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="Category ids must be unique")
    names = [item.name.casefold() for item in body.items]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=422, detail="Category names must be unique")
    body.items.sort(key=lambda item: (item.sort_order, item.name.casefold()))
    async with _categories_write_lock:
        current = await asyncio.to_thread(_load_digital_employee_categories)
        removed_ids = {item.id for item in current.items} - set(ids)
        if removed_ids:
            usage = await asyncio.to_thread(_find_category_usage, removed_ids)
            if usage:
                references = "; ".join(f"{category_id}: {', '.join(agent_refs)}" for category_id, agent_refs in sorted(usage.items()))
                raise HTTPException(
                    status_code=409,
                    detail=(f"Cannot remove categories that are assigned to digital employees. Reassign them first ({references})."),
                )
        await asyncio.to_thread(_write_digital_employee_categories, body)
    return body


def _agent_config_to_response(
    agent_cfg: AgentConfig,
    include_soul: bool = False,
    *,
    user_id: str | None = None,
    source: str | None = "personal",
) -> AgentResponse:
    """Convert AgentConfig to AgentResponse."""
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name, user_id=user_id) or ""

    agent_dir = get_paths().user_agent_dir(user_id or get_effective_user_id(), agent_cfg.name)
    return AgentResponse(
        name=agent_cfg.name,
        display_name=agent_cfg.display_name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        category_id=agent_cfg.category_id,
        tags=agent_cfg.tags,
        avatar_id=agent_cfg.avatar_id,
        avatar_url=_avatar_url(agent_cfg.name, source or "personal", agent_dir),
        model_settings=agent_cfg.model_settings,
        thinking_enabled=agent_cfg.thinking_enabled,
        reasoning_effort=agent_cfg.reasoning_effort,
        soul=soul,
        source=source,
    )


def _shared_agent_dir(name: str):
    paths = get_paths()
    team_dir = paths.team_agent_dir(name)
    if (team_dir / "config.yaml").exists():
        return team_dir, "team"
    legacy_dir = paths.agent_dir(name)
    if (legacy_dir / "config.yaml").exists():
        return legacy_dir, "legacy"
    raise FileNotFoundError(name)


def _shared_agent_response(
    name: str,
    *,
    current_user_id: str | None,
) -> AgentResponse:
    agent_dir, source = _shared_agent_dir(name)
    with open(agent_dir / "config.yaml", encoding="utf-8") as file:
        raw_config = yaml.safe_load(file) or {}
    raw_config.setdefault("name", name)
    known_fields = set(AgentConfig.model_fields.keys())
    agent_cfg = AgentConfig(**{key: value for key, value in raw_config.items() if key in known_fields})
    soul_path = agent_dir / "SOUL.md"
    published_by = raw_config.get("published_by_user_id")
    published_by_user_id = str(published_by) if published_by is not None else None
    return AgentResponse(
        name=agent_cfg.name,
        display_name=agent_cfg.display_name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        category_id=agent_cfg.category_id,
        tags=agent_cfg.tags,
        avatar_id=agent_cfg.avatar_id,
        avatar_url=_avatar_url(agent_cfg.name, source, agent_dir),
        model_settings=agent_cfg.model_settings,
        thinking_enabled=agent_cfg.thinking_enabled,
        reasoning_effort=agent_cfg.reasoning_effort,
        soul=soul_path.read_text(encoding="utf-8").strip() if soul_path.exists() else "",
        source=source,
        published_by_user_id=published_by_user_id,
        published_by_current_user=bool(current_user_id and published_by_user_id == current_user_id),
    )


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory, including their soul content.",
)
async def list_agents() -> AgentsListResponse:
    """List all custom agents.

    Returns:
        List of all custom agents with their metadata and soul content.
    """
    _require_agents_api_enabled()

    user_id = get_effective_user_id()

    def _list() -> AgentsListResponse:
        agents = list_custom_agents(user_id=user_id)
        return AgentsListResponse(agents=[_agent_config_to_response(a, include_soul=True, user_id=user_id) for a in agents])

    try:
        return await asyncio.to_thread(_list)
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get(
    "/team-agents",
    response_model=AgentsListResponse,
    summary="List Team Agents",
    description="List the shared enterprise agent square, including legacy shared agents.",
)
async def list_team_agents_endpoint() -> AgentsListResponse:
    """List team-shared agents that are available to every user."""
    _require_agents_api_enabled()
    current_user_id = get_effective_user_id()

    def _list() -> list[AgentResponse]:
        return [_shared_agent_response(agent.name, current_user_id=current_user_id) for agent in list_team_agents()]

    try:
        return AgentsListResponse(agents=await asyncio.to_thread(_list))
    except Exception as e:
        logger.error("Failed to list team agents", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list team agents: {str(e)}",
        )


@router.get(
    "/team-agents/{name}",
    response_model=AgentResponse,
    summary="Get Team Agent",
    description="Retrieve a shared enterprise agent.",
)
async def get_team_agent(name: str) -> AgentResponse:
    """Return one team-shared agent and its SOUL.md content."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    current_user_id = get_effective_user_id()
    try:
        return await asyncio.to_thread(
            _shared_agent_response,
            name,
            current_user_id=current_user_id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Team agent '{name}' not found")
    except Exception as e:
        logger.error("Failed to get team agent '%s'", name, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get team agent: {str(e)}",
        )


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str) -> dict:
    """Check whether an agent name is valid and not yet taken.

    Args:
        name: The agent name to check.

    Returns:
        ``{"available": true/false, "name": "<normalized>"}``

    Raises:
        HTTPException: 422 if the name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    exists = await asyncio.to_thread(_agent_exists, normalized, user_id)
    return {"available": not exists, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent.",
)
async def get_agent(name: str) -> AgentResponse:
    """Get a specific custom agent by name.

    Args:
        name: The agent name.

    Returns:
        Agent details including SOUL.md content.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()

    def _get() -> AgentResponse:
        agent_cfg = load_agent_config(name, user_id=user_id)
        return _agent_config_to_response(agent_cfg, include_soul=True, user_id=user_id)

    try:
        return await asyncio.to_thread(_get)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to get agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get agent: {str(e)}")


@router.get(
    "/agents/{name}/avatar",
    response_class=FileResponse,
    summary="Get Custom Digital Employee Avatar",
)
async def get_agent_avatar(name: str) -> FileResponse:
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    avatar = _avatar_path(get_paths().user_agent_dir(get_effective_user_id(), normalized))
    if not await asyncio.to_thread(avatar.is_file):
        raise HTTPException(status_code=404, detail="Custom avatar not found")
    return FileResponse(
        avatar,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/team-agents/{name}/avatar",
    response_class=FileResponse,
    summary="Get Shared Digital Employee Avatar",
)
async def get_team_agent_avatar(name: str) -> FileResponse:
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    try:
        agent_dir, _source = await asyncio.to_thread(_shared_agent_dir, normalized)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Team agent not found") from exc
    avatar = _avatar_path(agent_dir)
    if not await asyncio.to_thread(avatar.is_file):
        raise HTTPException(status_code=404, detail="Custom avatar not found")
    return FileResponse(
        avatar,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/agents/{name}/avatar",
    response_model=AgentResponse,
    summary="Upload Custom Digital Employee Avatar",
)
async def upload_agent_avatar(
    name: str,
    file: UploadFile = File(...),
) -> AgentResponse:
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    agent_dir = await asyncio.to_thread(lambda: get_paths().user_agent_dir(user_id, normalized))
    if not await asyncio.to_thread(_agent_exists, normalized, user_id):
        raise HTTPException(status_code=404, detail=f"Agent '{normalized}' not found")

    payload = await file.read(MAX_AVATAR_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=422, detail="Avatar image is empty")
    if len(payload) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Avatar image exceeds 5 MB")
    try:
        await asyncio.to_thread(_encode_custom_avatar, payload, _avatar_path(agent_dir))
        agent_cfg = await asyncio.to_thread(load_agent_config, normalized, user_id=user_id)
        return await asyncio.to_thread(
            _agent_config_to_response,
            agent_cfg,
            True,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to store custom avatar for '%s'", normalized, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to store custom avatar") from exc


@router.delete(
    "/agents/{name}/avatar",
    status_code=204,
    summary="Remove Custom Digital Employee Avatar",
)
async def delete_agent_avatar(name: str) -> None:
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    agent_dir = await asyncio.to_thread(lambda: get_paths().user_agent_dir(user_id, normalized))
    if not await asyncio.to_thread(_agent_exists, normalized, user_id):
        raise HTTPException(status_code=404, detail=f"Agent '{normalized}' not found")
    await asyncio.to_thread(_avatar_path(agent_dir).unlink, missing_ok=True)


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(request: AgentCreateRequest) -> AgentResponse:
    """Create a new custom agent.

    Args:
        request: The agent creation request.

    Returns:
        The created agent details.

    Raises:
        HTTPException: 409 if agent already exists, 422 if name is invalid.
    """
    _require_agents_api_enabled()
    _validate_agent_name(request.name)
    await asyncio.to_thread(_validate_model_exists, request.model)
    await asyncio.to_thread(_ensure_category_exists, request.category_id)
    normalized_name = _normalize_agent_name(request.name)
    user_id = get_effective_user_id()
    config_data: dict = {"name": normalized_name}
    if request.display_name:
        config_data["display_name"] = request.display_name.strip()
    if request.description:
        config_data["description"] = request.description
    if request.tool_groups is not None:
        config_data["tool_groups"] = request.tool_groups
    if request.skills is not None:
        config_data["skills"] = request.skills
    if request.category_id:
        config_data["category_id"] = request.category_id
    if request.tags:
        config_data["tags"] = request.tags
    if request.avatar_id:
        config_data["avatar_id"] = request.avatar_id
    _apply_model_behavior(config_data, request)

    def _create_agent() -> AgentResponse:
        store = get_agent_store()
        store.create(
            normalized_name,
            config_data,
            request.soul,
            user_id=user_id,
        )
        agent_cfg = load_agent_config(normalized_name, user_id=user_id)
        logger.info("Created digital employee '%s'", normalized_name)
        return _agent_config_to_response(
            agent_cfg,
            include_soul=True,
            user_id=user_id,
        )

    try:
        return await asyncio.to_thread(_create_agent)
    except AgentExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{normalized_name}' already exists",
        )
    except Exception as e:
        logger.error(f"Failed to create agent '{request.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.post(
    "/agents/{name}/publish",
    response_model=AgentResponse,
    summary="Publish Custom Agent",
    description="Publish a personal custom agent to the enterprise agent square.",
)
async def publish_agent(name: str, request: AgentPublishRequest) -> AgentResponse:
    """Copy a personal agent into the shared team-agent catalog."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    paths = get_paths()

    def _publish() -> AgentResponse | None:
        source_dir = paths.user_agent_dir(user_id, name)
        if not is_personal_agent(name, user_id=user_id):
            raise FileNotFoundError(name)

        team_dir = paths.team_agent_dir(name)
        if team_dir.exists():
            if not request.overwrite:
                return None
            shutil.rmtree(team_dir)
        team_dir.mkdir(parents=True, exist_ok=False)

        personal = load_agent_config(name, user_id=user_id)
        config_data = personal.model_dump(exclude_none=True)
        config_data["name"] = name
        config_data["published_by_user_id"] = user_id
        with open(team_dir / "config.yaml", "w", encoding="utf-8") as file:
            yaml.safe_dump(config_data, file, default_flow_style=False, allow_unicode=True)

        (team_dir / "SOUL.md").write_text(
            load_agent_soul(name, user_id=user_id) or "",
            encoding="utf-8",
        )
        custom_avatar = _avatar_path(source_dir)
        if custom_avatar.is_file():
            shutil.copy2(custom_avatar, _avatar_path(team_dir))
        return _shared_agent_response(name, current_user_id=user_id)

    try:
        response = await asyncio.to_thread(_publish)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")
    except Exception as e:
        logger.error("Failed to publish agent '%s'", name, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to publish agent: {str(e)}",
        )

    if response is None:
        raise HTTPException(status_code=409, detail=f"Team agent '{name}' already exists")
    return response


@router.delete(
    "/team-agents/{name}",
    status_code=204,
    summary="Unpublish Team Agent",
    description="Remove a team-published agent from the enterprise agent square.",
)
async def unpublish_team_agent(name: str) -> None:
    """Remove a shared agent, limited to the user who published it."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    paths = get_paths()

    def _unpublish() -> str:
        team_dir = paths.team_agent_dir(name)
        config_file = team_dir / "config.yaml"
        if not config_file.exists():
            return "legacy" if (paths.agent_dir(name) / "config.yaml").exists() else "missing"
        with open(config_file, encoding="utf-8") as file:
            config_data = yaml.safe_load(file) or {}
        published_by = config_data.get("published_by_user_id")
        if published_by is not None and str(published_by) != user_id:
            return "forbidden"
        shutil.rmtree(team_dir)
        return "deleted"

    try:
        outcome = await asyncio.to_thread(_unpublish)
    except Exception as e:
        logger.error("Failed to unpublish team agent '%s'", name, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to unpublish team agent: {str(e)}",
        )

    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"Team agent '{name}' not found")
    if outcome == "legacy":
        raise HTTPException(
            status_code=409,
            detail="Legacy shared agents cannot be unpublished through the team square.",
        )
    if outcome == "forbidden":
        raise HTTPException(
            status_code=403,
            detail=f"Only the publisher can unpublish team agent '{name}'",
        )


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(name: str, request: AgentUpdateRequest) -> AgentResponse:
    """Update an existing custom agent.

    Args:
        name: The agent name.
        request: The update request (all fields optional).

    Returns:
        The updated agent details.

    Raises:
        HTTPException: 404 if agent not found.
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()

    try:
        agent_cfg = await asyncio.to_thread(
            load_agent_config,
            name,
            user_id=user_id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    if not await asyncio.to_thread(is_personal_agent, name, user_id=user_id):
        paths = get_paths()
        if await asyncio.to_thread((paths.team_agent_dir(name) / "config.yaml").is_file):
            raise HTTPException(
                status_code=409,
                detail=(f"Agent '{name}' exists in the team square but is not scoped to the current user. Create a personal copy before updating it."),
            )
        if await asyncio.to_thread((paths.agent_dir(name) / "config.yaml").is_file):
            raise HTTPException(
                status_code=409,
                detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before updating."),
            )

    if "model" in request.model_fields_set:
        await asyncio.to_thread(_validate_model_exists, request.model)
    if "category_id" in request.model_fields_set:
        await asyncio.to_thread(_ensure_category_exists, request.category_id)

    try:
        # Update config if any config fields changed
        # Use model_fields_set to distinguish "field omitted" from "explicitly set to null".
        # This is critical for skills where None means "inherit all" (not "don't change").
        fields_set = request.model_fields_set
        managed_fields = {
            "display_name",
            "description",
            "model",
            "tool_groups",
            "skills",
            "category_id",
            "tags",
            "avatar_id",
            "model_settings",
            "thinking_enabled",
            "reasoning_effort",
        }
        config_changed = bool(fields_set & managed_fields)

        updated: dict | None = None
        if config_changed:
            updated = {"name": agent_cfg.name}
            for field_name in managed_fields - set(_MODEL_BEHAVIOR_FIELDS):
                value = getattr(request, field_name) if field_name in fields_set else getattr(agent_cfg, field_name)
                if value is not None:
                    updated[field_name] = value
            _apply_model_behavior(updated, request, existing=agent_cfg)

            # Carry forward every top-level AgentConfig field this route does
            # not manage (currently ``github:``, plus any future field added
            # to :class:`AgentConfig`). The harness ``update_agent`` tool uses
            # the same helper, so an operator editing the agent description
            # from the Web UI does not silently strip a hand-authored
            # ``github:`` binding — which would otherwise leave the next
            # webhook delivery unable to find the agent in the registry and
            # silently no-op.
            for key, value in preserve_non_managed_fields(agent_cfg).items():
                updated.setdefault(key, value)

        if updated is not None or request.soul is not None:

            def _update_agent_store() -> None:
                get_agent_store().update(
                    name,
                    updated,
                    request.soul,
                    user_id=user_id,
                )

            await asyncio.to_thread(_update_agent_store)

        logger.info(f"Updated agent '{name}'")

        def _refresh() -> AgentResponse:
            refreshed_cfg = load_agent_config(name, user_id=user_id)
            return _agent_config_to_response(
                refreshed_cfg,
                include_soul=True,
                user_id=user_id,
            )

        return await asyncio.to_thread(_refresh)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents.",
)
async def get_user_profile() -> UserProfileResponse:
    """Return the current USER.md content.

    Returns:
        UserProfileResponse with content=None if USER.md does not exist yet.
    """
    _require_agents_api_enabled()

    try:
        user_md_path = get_paths().user_md_file
        if not user_md_path.exists():
            return UserProfileResponse(content=None)
        raw = user_md_path.read_text(encoding="utf-8").strip()
        return UserProfileResponse(content=raw or None)
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read user profile: {str(e)}")


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents.",
)
async def update_user_profile(request: UserProfileUpdateRequest) -> UserProfileResponse:
    """Create or overwrite the global USER.md.

    Args:
        request: The update request with the new USER.md content.

    Returns:
        UserProfileResponse with the saved content.
    """
    _require_agents_api_enabled()

    try:
        paths = get_paths()
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        paths.user_md_file.write_text(request.content, encoding="utf-8")
        logger.info(f"Updated USER.md at {paths.user_md_file}")
        return UserProfileResponse(content=request.content or None)
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update user profile: {str(e)}")


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, SOUL.md, memory).",
)
async def delete_agent(name: str) -> None:
    """Delete a custom agent.

    Args:
        name: The agent name.

    Raises:
        HTTPException: 404 if no per-user copy exists; 409 if only a legacy
            shared copy exists (suggesting the migration script).
    """
    _require_agents_api_enabled()
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    user_id = get_effective_user_id()
    try:
        if not await asyncio.to_thread(is_personal_agent, name, user_id=user_id):
            paths = get_paths()
            if await asyncio.to_thread((paths.team_agent_dir(name) / "config.yaml").is_file):
                outcome = "team"
            elif await asyncio.to_thread((paths.agent_dir(name) / "config.yaml").is_file):
                outcome = "legacy"
            else:
                outcome = "missing"
        else:
            outcome = await asyncio.to_thread(
                _delete_agent_with_memory_cancel,
                name,
                user_id,
            )
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")

    if outcome == "legacy":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' only exists in the legacy shared layout and is not scoped to a user. Run scripts/migrate_user_isolation.py to move legacy agents into the per-user layout before deleting."),
        )
    if outcome == "team":
        raise HTTPException(
            status_code=409,
            detail=(f"Agent '{name}' exists in the team square and cannot be deleted as a personal agent."),
        )
    if outcome == "missing":
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    logger.info("Deleted digital employee '%s'", name)


def _delete_agent_with_memory_cancel(name: str, user_id: str | None) -> str:
    """Cancel queued memory before and after deleting a digital employee."""
    store = get_agent_store()
    _cancel_pending_memory_for_agent(name, user_id)
    outcome = store.delete(name, user_id=user_id)
    if outcome == "deleted":
        _cancel_pending_memory_for_agent(name, user_id)
    return outcome


def _cancel_pending_memory_for_agent(name: str, user_id: str | None) -> None:
    """Best-effort scoped cancellation; memory failure must not block deletion."""
    try:
        cancelled = get_memory_manager().cancel_by_agent(name, user_id=user_id)
        if cancelled:
            logger.info(
                "Cancelled %d pending memory update(s) for digital employee %r",
                cancelled,
                name,
            )
    except Exception:
        logger.warning(
            "Failed to cancel pending memory for digital employee %r (non-fatal)",
            name,
            exc_info=True,
        )
