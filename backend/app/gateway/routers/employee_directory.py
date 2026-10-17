"""Curated discovery metadata, separate from user-editable agent tags/config."""

import asyncio
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select

from app.gateway.deps import get_current_user_from_request, require_admin_user
from app.gateway.employee_featured_store import EmployeeIdentity, FeaturedConflict, FeaturedStore, FeaturedUnavailable
from app.gateway.external_agent_analytics import EXTERNAL_PLATFORM_AGENT_NAMES
from app.gateway.routers.agents import _require_agents_api_enabled, _shared_agent_dir
from deerflow.config.agents_config import list_custom_agents, list_team_agents
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.user.model import UserRow

router = APIRouter(prefix="/api/employee-directory", tags=["agents"])
SharedSource = Literal["external", "team", "legacy"]

# Identity allowlist only; URLs remain in the existing production catalog.
# A parity test pins these names against both frontend source catalogs.
EXTERNAL_EMPLOYEE_NAMES = frozenset(EXTERNAL_PLATFORM_AGENT_NAMES) | {
    "储小亮",
    "研究总院水驱可采储量计算专家",
    "海油发展井控全周期智能管家",
    "海油发展新能源生产运维智能助手",
    "设备全周期健康管理专员",
    "工程建造项目承揽助手",
    "中联公司录井与完井报告审核助手",
    "智安咨询硅基员工",
    "数字化项目审查AI助手",
    "海油发展智能配方优选管家",
    "ERP风险预警专家",
    "合同合规审核专家",
    "科研项目全周期管理助手",
    "业财一体化智能助手",
    "采购供应链智能助手",
    "气电集团物流监管智能决策助手-e安达",
    "物料拍图智能搜索助手",
    "钻井安全智能决策助手",
}


class EmployeeMetadata(BaseModel):
    source: str
    name: str
    is_hot: bool = False
    version: int = 0
    identity_token: str
    creator_name: str | None = None


class DirectoryResponse(BaseModel):
    employees: list[EmployeeMetadata]


class HotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_hot: StrictBool
    expected_version: int = Field(ge=0, strict=True)
    expected_identity: str = Field(pattern=r"^[a-f0-9]{64}$")


def _catalog(user_id: str, *, include_personal: bool = True) -> list[EmployeeIdentity]:
    identities = [EmployeeIdentity("external", name, "") for name in sorted(EXTERNAL_EMPLOYEE_NAMES)]
    for config in list_team_agents():
        agent_dir, source = _shared_agent_dir(config.name)
        with (agent_dir / "config.yaml").open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
        identities.append(EmployeeIdentity(source, config.name, str(raw.get("published_by_user_id") or ""), str(raw.get("publication_id") or "")))
    if include_personal:
        identities.extend(EmployeeIdentity("personal", agent.name, user_id) for agent in list_custom_agents(user_id=user_id))
    return identities


def _sessions():
    sessions = get_session_factory()
    if sessions is None:
        raise HTTPException(503, "Employee discovery metadata requires persistent storage")
    return sessions


@router.get("", response_model=DirectoryResponse)
async def list_employee_metadata(request: Request, response: Response) -> DirectoryResponse:
    user = await get_current_user_from_request(request)
    _require_agents_api_enabled()
    sessions = _sessions()
    identities = await asyncio.to_thread(_catalog, user.id)
    try:
        snapshot = await asyncio.to_thread(lambda: FeaturedStore().snapshot())
    except FeaturedUnavailable:
        raise HTTPException(503, "Employee hot configuration is unavailable; contact an administrator") from None
    response.headers["Cache-Control"] = "no-store"
    owner_ids = sorted({entry.owner_id for entry in identities if entry.owner_id})
    async with sessions() as session:
        names: dict[str, str] = {}
        # Batch only public display names; never email/password or per-card N+1.
        for offset in range(0, len(owner_ids), 500):
            result = await session.execute(select(UserRow.id, UserRow.display_name).where(UserRow.id.in_(owner_ids[offset : offset + 500])))
            names.update({uid: name.strip() for uid, name in result if name and name.strip()})
    entries = []
    for identity in identities:
        is_hot, version = snapshot.flag(identity)
        entries.append(
            EmployeeMetadata(
                source=identity.source,
                name=identity.name,
                is_hot=is_hot,
                version=version,
                identity_token=identity.token,
                creator_name=names.get(identity.owner_id),
            )
        )
    return DirectoryResponse(employees=entries)


@router.put("/{source}/{name}/hot", response_model=EmployeeMetadata)
async def set_employee_hot(source: SharedSource, name: str, body: HotUpdate, request: Request) -> EmployeeMetadata:
    await require_admin_user(request, detail="Only administrators can manage hot employees")
    user = await get_current_user_from_request(request)
    _require_agents_api_enabled()
    identities = await asyncio.to_thread(_catalog, user.id)
    identity = next((entry for entry in identities if entry.source == source and entry.name == name), None)
    if identity is None:
        raise HTTPException(404, "Shared employee not found")
    if body.expected_identity != identity.token:
        raise HTTPException(409, "Employee identity changed; reload before saving")
    try:
        version = await asyncio.to_thread(lambda: FeaturedStore().set_hot(identity, body.is_hot, expected_version=body.expected_version, actor=user.id))
    except FeaturedConflict:
        raise HTTPException(409, "Employee selection changed; reload before saving") from None
    except FeaturedUnavailable:
        raise HTTPException(503, "Employee hot configuration could not be saved; reload before retrying") from None
    return EmployeeMetadata(source=source, name=name, is_hot=body.is_hot, version=version, identity_token=identity.token)
