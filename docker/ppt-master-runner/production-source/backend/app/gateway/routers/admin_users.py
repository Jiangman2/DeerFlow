"""Admin user-management endpoints."""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.gateway.auth.password import hash_password_async
from app.gateway.deps import require_admin_user
from deerflow.config.paths import get_paths
from deerflow.persistence.channel_connections.model import ChannelConnectionRow, ChannelConversationRow, ChannelCredentialRow, ChannelOAuthStateRow
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import OperationDispositionRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

router = APIRouter(prefix="/api/admin/users", tags=["users"])

_ADMIN_REQUIRED = "Admin privileges are required to manage users"


class ManagedUser(BaseModel):
    id: str
    email: str
    system_role: Literal["admin", "user"]
    needs_setup: bool
    is_disabled: bool
    oauth_provider: str | None = None
    created_at: datetime
    token_version: int
    thread_count: int = 0
    run_count: int = 0


class UserListResponse(BaseModel):
    items: list[ManagedUser]
    total: int


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    system_role: Literal["admin", "user"] = "user"
    needs_setup: bool = True


class CreateUserResponse(BaseModel):
    item: ManagedUser
    temporary_password: str | None = None


class UpdateUserRequest(BaseModel):
    email: EmailStr | None = None
    system_role: Literal["admin", "user"] | None = None
    needs_setup: bool | None = None
    is_disabled: bool | None = None


class UserResponse(BaseModel):
    item: ManagedUser


class ResetPasswordRequest(BaseModel):
    new_password: str | None = Field(default=None, min_length=8)
    needs_setup: bool = True


class ResetPasswordResponse(BaseModel):
    item: ManagedUser
    temporary_password: str | None = None


class DeleteUserRequest(BaseModel):
    delete_file_data: bool = False


class DeleteUserResponse(BaseModel):
    success: bool
    deleted_file_data: bool = False


def _session_factory():
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="User database is not available")
    return sf


def _current_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user_id)


def _row_to_response(row: UserRow, *, thread_count: int = 0, run_count: int = 0) -> ManagedUser:
    return ManagedUser(
        id=row.id,
        email=row.email,
        system_role=row.system_role,  # type: ignore[arg-type]
        needs_setup=row.needs_setup,
        is_disabled=row.is_disabled,
        oauth_provider=row.oauth_provider,
        created_at=row.created_at,
        token_version=row.token_version,
        thread_count=thread_count,
        run_count=run_count,
    )


def _generated_password() -> str:
    return secrets.token_urlsafe(18)


async def _active_admin_count(session) -> int:
    stmt = select(func.count()).select_from(UserRow).where(UserRow.system_role == "admin", UserRow.is_disabled.is_(False))
    return await session.scalar(stmt) or 0


async def _ensure_not_last_active_admin(session, row: UserRow, *, next_role: str | None = None, next_disabled: bool | None = None) -> None:
    role = next_role if next_role is not None else row.system_role
    disabled = next_disabled if next_disabled is not None else row.is_disabled
    remains_active_admin = role == "admin" and not disabled
    if row.system_role == "admin" and not row.is_disabled and not remains_active_admin and await _active_admin_count(session) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove or disable the last active admin")


async def _load_counts(session, user_ids: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    if not user_ids:
        return {}, {}

    thread_rows = await session.execute(select(ThreadMetaRow.user_id, func.count()).where(ThreadMetaRow.user_id.in_(user_ids)).group_by(ThreadMetaRow.user_id))
    run_rows = await session.execute(select(RunRow.user_id, func.count()).where(RunRow.user_id.in_(user_ids)).group_by(RunRow.user_id))
    return (
        {str(user_id): count for user_id, count in thread_rows.all() if user_id is not None},
        {str(user_id): count for user_id, count in run_rows.all() if user_id is not None},
    )


def _delete_user_files(user_id: str) -> bool:
    paths = get_paths()
    users_root = (paths.base_dir / "users").resolve()
    target = paths.user_dir(user_id).resolve()
    try:
        target.relative_to(users_root)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Resolved user data path is outside users root") from exc

    if not target.exists():
        return False
    if not target.is_dir():
        raise HTTPException(status_code=500, detail="Resolved user data path is not a directory")
    shutil.rmtree(target)
    return True


@router.get("", response_model=UserListResponse)
async def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=320),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> UserListResponse:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    sf = _session_factory()
    async with sf() as session:
        stmt = select(UserRow)
        count_stmt = select(func.count()).select_from(UserRow)
        if q:
            pattern = f"%{q.strip()}%"
            stmt = stmt.where(UserRow.email.ilike(pattern))
            count_stmt = count_stmt.where(UserRow.email.ilike(pattern))
        stmt = stmt.order_by(UserRow.created_at.desc(), UserRow.email.asc()).limit(limit).offset(offset)
        rows = list((await session.execute(stmt)).scalars().all())
        total = await session.scalar(count_stmt) or 0
        thread_counts, run_counts = await _load_counts(session, [row.id for row in rows])

    return UserListResponse(
        items=[_row_to_response(row, thread_count=thread_counts.get(row.id, 0), run_count=run_counts.get(row.id, 0)) for row in rows],
        total=total,
    )


@router.post("", response_model=CreateUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(request: Request, body: CreateUserRequest) -> CreateUserResponse:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    password = body.password or _generated_password()
    sf = _session_factory()
    async with sf() as session:
        row = UserRow(
            id=str(uuid4()),
            email=str(body.email),
            password_hash=await hash_password_async(password),
            system_role=body.system_role,
            needs_setup=body.needs_setup,
            is_disabled=False,
            token_version=0,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail="Email already registered") from exc
        await session.refresh(row)
    return CreateUserResponse(item=_row_to_response(row), temporary_password=None if body.password else password)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(request: Request, user_id: str, body: UpdateUserRequest) -> UserResponse:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    current_user_id = _current_user_id(request)
    sf = _session_factory()
    async with sf() as session:
        row = await session.get(UserRow, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")

        next_role = body.system_role if body.system_role is not None else row.system_role
        next_disabled = body.is_disabled if body.is_disabled is not None else row.is_disabled
        if row.id == current_user_id and next_disabled:
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        if row.id == current_user_id and next_role != "admin":
            raise HTTPException(status_code=400, detail="You cannot remove your own admin role")
        await _ensure_not_last_active_admin(session, row, next_role=next_role, next_disabled=next_disabled)

        if body.email is not None:
            row.email = str(body.email)
        if body.system_role is not None:
            row.system_role = body.system_role
        if body.needs_setup is not None:
            row.needs_setup = body.needs_setup
        if body.is_disabled is not None and row.is_disabled != body.is_disabled:
            row.is_disabled = body.is_disabled
            row.token_version += 1

        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(status_code=400, detail="Email already registered") from exc
        await session.refresh(row)
        thread_counts, run_counts = await _load_counts(session, [row.id])
    return UserResponse(item=_row_to_response(row, thread_count=thread_counts.get(row.id, 0), run_count=run_counts.get(row.id, 0)))


@router.post("/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_user_password(request: Request, user_id: str, body: ResetPasswordRequest) -> ResetPasswordResponse:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    password = body.new_password or _generated_password()
    sf = _session_factory()
    async with sf() as session:
        row = await session.get(UserRow, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        row.password_hash = await hash_password_async(password)
        row.needs_setup = body.needs_setup
        row.token_version += 1
        await session.commit()
        await session.refresh(row)
        thread_counts, run_counts = await _load_counts(session, [row.id])
    return ResetPasswordResponse(
        item=_row_to_response(row, thread_count=thread_counts.get(row.id, 0), run_count=run_counts.get(row.id, 0)),
        temporary_password=None if body.new_password else password,
    )


@router.delete("/{user_id}", response_model=DeleteUserResponse)
async def delete_user(request: Request, user_id: str, body: DeleteUserRequest | None = None) -> DeleteUserResponse:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    if user_id == _current_user_id(request):
        raise HTTPException(status_code=400, detail="You cannot delete your own account")

    body = body or DeleteUserRequest()
    deleted_file_data = False
    sf = _session_factory()
    async with sf() as session:
        row = await session.get(UserRow, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        await _ensure_not_last_active_admin(session, row, next_role="user", next_disabled=True)

        connection_ids = select(ChannelConnectionRow.id).where(ChannelConnectionRow.owner_user_id == user_id)
        await session.execute(delete(ChannelConversationRow).where(ChannelConversationRow.owner_user_id == user_id))
        await session.execute(delete(ChannelOAuthStateRow).where(ChannelOAuthStateRow.owner_user_id == user_id))
        await session.execute(delete(ChannelCredentialRow).where(ChannelCredentialRow.connection_id.in_(connection_ids)))
        await session.execute(delete(ChannelConnectionRow).where(ChannelConnectionRow.owner_user_id == user_id))
        await session.execute(delete(OperationDispositionRow).where(OperationDispositionRow.user_id == user_id))
        await session.execute(delete(FeedbackRow).where(FeedbackRow.user_id == user_id))
        await session.execute(delete(RunEventRow).where(RunEventRow.user_id == user_id))
        await session.execute(delete(RunRow).where(RunRow.user_id == user_id))
        await session.execute(delete(ThreadMetaRow).where(ThreadMetaRow.user_id == user_id))
        await session.delete(row)
        await session.commit()

    if body.delete_file_data:
        deleted_file_data = _delete_user_files(user_id)
    return DeleteUserResponse(success=True, deleted_file_data=deleted_file_data)
