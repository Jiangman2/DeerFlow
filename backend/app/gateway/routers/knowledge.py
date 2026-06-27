"""User-private knowledge base APIs."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from deerflow.knowledge import (
    KnowledgeFile,
    KnowledgeFolder,
    KnowledgeSearchResult,
    copy_knowledge_file,
    create_knowledge_folder,
    delete_knowledge_file,
    delete_knowledge_folder,
    index_knowledge_file,
    list_knowledge_files,
    list_knowledge_folders,
    move_knowledge_file,
    move_knowledge_folder,
    search_knowledge,
)
from deerflow.knowledge import (
    reindex_knowledge_file as reindex_user_knowledge_file,
)
from deerflow.knowledge import (
    rename_knowledge_folder as rename_user_knowledge_folder,
)
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

UPLOAD_CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_FILES = 200
DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024


class KnowledgeFileResponse(BaseModel):
    id: str
    filename: str
    folder_path: str = ""
    display_path: str
    original_filename: str
    content_type: str | None = None
    size: int
    sha256: str
    status: str
    error: str | None = None
    chunk_count: int
    created_at: str
    updated_at: str


class KnowledgeFolderResponse(BaseModel):
    id: str
    name: str
    path: str
    parent_path: str = ""
    created_at: str
    updated_at: str


class KnowledgeListResponse(BaseModel):
    files: list[KnowledgeFileResponse]
    folders: list[KnowledgeFolderResponse] = Field(default_factory=list)
    count: int


class KnowledgeUploadResponse(BaseModel):
    success: bool
    files: list[KnowledgeFileResponse]
    message: str


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    scope_paths: list[str] = Field(default_factory=list)


class KnowledgeFolderCreateRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


class KnowledgeFileMoveRequest(BaseModel):
    target_folder_path: str = Field(default="", max_length=1000)
    target_filename: str | None = Field(default=None, max_length=180)


class KnowledgeFileCopyRequest(BaseModel):
    target_folder_path: str = Field(default="", max_length=1000)
    target_filename: str | None = Field(default=None, max_length=180)


class KnowledgeFolderRenameRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=180)


class KnowledgeFolderMoveRequest(BaseModel):
    target_path: str = Field(..., min_length=1, max_length=1000)


class KnowledgeSearchResultResponse(BaseModel):
    file_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    heading: str | None = None
    snippet: str
    score: float


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResultResponse]
    count: int


class KnowledgeFolderDeleteResponse(BaseModel):
    success: bool
    deleted_count: int
    message: str


def _file_response(file: KnowledgeFile) -> KnowledgeFileResponse:
    return KnowledgeFileResponse(**asdict(file))


def _folder_response(folder: KnowledgeFolder) -> KnowledgeFolderResponse:
    return KnowledgeFolderResponse(**asdict(folder))


def _search_response(result: KnowledgeSearchResult) -> KnowledgeSearchResultResponse:
    return KnowledgeSearchResultResponse(**asdict(result))


async def _write_temp_upload(file: UploadFile) -> tuple[Path, int]:
    suffix = Path(file.filename or "upload").suffix
    fd, raw_path = tempfile.mkstemp(prefix="deerflow-kb-", suffix=suffix)
    size = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                size += len(chunk)
                if size > DEFAULT_MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail=f"File too large: {file.filename}")
                fh.write(chunk)
    except Exception:
        try:
            Path(raw_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return Path(raw_path), size


@router.post("/files", response_model=KnowledgeUploadResponse)
@require_permission("threads", "write")
async def upload_knowledge_files(request: Request, files: list[UploadFile] = File(...)) -> KnowledgeUploadResponse:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > DEFAULT_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"Too many files: maximum is {DEFAULT_MAX_FILES}")

    user_id = get_effective_user_id()
    indexed: list[KnowledgeFileResponse] = []
    temp_paths: list[Path] = []
    try:
        for file in files:
            if not file.filename:
                continue
            temp_path, _ = await _write_temp_upload(file)
            temp_paths.append(temp_path)
            knowledge_file = await index_knowledge_file(
                user_id=user_id,
                source_path=temp_path,
                original_filename=file.filename,
                content_type=file.content_type,
            )
            indexed.append(_file_response(knowledge_file))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        for path in temp_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    indexed_count = sum(1 for file in indexed if file.status == "indexed")
    failed_count = sum(1 for file in indexed if file.status == "failed")
    chunk_count = sum(file.chunk_count for file in indexed)
    return KnowledgeUploadResponse(
        success=failed_count == 0,
        files=indexed,
        message=(
            f"Uploaded {len(indexed)} file(s); "
            f"indexed {indexed_count}, failed {failed_count}, chunks {chunk_count}"
        ),
    )


@router.get("/files", response_model=KnowledgeListResponse)
@require_permission("threads", "read")
async def list_knowledge(request: Request) -> KnowledgeListResponse:
    try:
        files = await list_knowledge_files(get_effective_user_id())
        folders = await list_knowledge_folders(get_effective_user_id())
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    responses = [_file_response(file) for file in files]
    folder_responses = [_folder_response(folder) for folder in folders]
    return KnowledgeListResponse(files=responses, folders=folder_responses, count=len(responses))


@router.post("/folders", response_model=KnowledgeFolderResponse)
@require_permission("threads", "write")
async def create_knowledge_folder_endpoint(
    body: KnowledgeFolderCreateRequest,
    request: Request,
) -> KnowledgeFolderResponse:
    try:
        folder = await create_knowledge_folder(get_effective_user_id(), body.path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _folder_response(folder)


@router.patch("/files/{file_id}", response_model=KnowledgeFileResponse)
@require_permission("threads", "write")
async def move_knowledge_file_endpoint(
    file_id: str,
    body: KnowledgeFileMoveRequest,
    request: Request,
) -> KnowledgeFileResponse:
    try:
        file = await move_knowledge_file(
            get_effective_user_id(),
            file_id,
            target_folder_path=body.target_folder_path,
            target_filename=body.target_filename,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if file is None:
        raise HTTPException(status_code=404, detail=f"Knowledge file {file_id!r} not found")
    return _file_response(file)


@router.post("/files/{file_id}/copy", response_model=KnowledgeFileResponse)
@require_permission("threads", "write")
async def copy_knowledge_file_endpoint(
    file_id: str,
    body: KnowledgeFileCopyRequest,
    request: Request,
) -> KnowledgeFileResponse:
    try:
        file = await copy_knowledge_file(
            get_effective_user_id(),
            file_id,
            target_folder_path=body.target_folder_path,
            target_filename=body.target_filename,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if file is None:
        raise HTTPException(status_code=404, detail=f"Knowledge file {file_id!r} not found")
    return _file_response(file)


@router.patch("/folders/{folder_path:path}/rename", response_model=KnowledgeFolderResponse)
@require_permission("threads", "write")
async def rename_knowledge_folder_endpoint(
    folder_path: str,
    body: KnowledgeFolderRenameRequest,
    request: Request,
) -> KnowledgeFolderResponse:
    try:
        folder = await rename_user_knowledge_folder(get_effective_user_id(), folder_path, body.new_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if folder is None:
        raise HTTPException(status_code=404, detail=f"Knowledge folder {folder_path!r} not found")
    return _folder_response(folder)


@router.patch("/folders/{folder_path:path}/move", response_model=KnowledgeFolderResponse)
@require_permission("threads", "write")
async def move_knowledge_folder_endpoint(
    folder_path: str,
    body: KnowledgeFolderMoveRequest,
    request: Request,
) -> KnowledgeFolderResponse:
    try:
        folder = await move_knowledge_folder(get_effective_user_id(), folder_path, body.target_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if folder is None:
        raise HTTPException(status_code=404, detail=f"Knowledge folder {folder_path!r} not found")
    return _folder_response(folder)


@router.delete("/files/{file_id}")
@require_permission("threads", "delete")
async def delete_knowledge(file_id: str, request: Request) -> dict:
    try:
        deleted = await delete_knowledge_file(get_effective_user_id(), file_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Knowledge file {file_id!r} not found")
    return {"success": True, "message": "Knowledge file deleted"}


@router.delete("/folders/{folder_path:path}", response_model=KnowledgeFolderDeleteResponse)
@require_permission("threads", "delete")
async def delete_knowledge_folder_endpoint(folder_path: str, request: Request) -> KnowledgeFolderDeleteResponse:
    try:
        deleted_count = await delete_knowledge_folder(get_effective_user_id(), folder_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return KnowledgeFolderDeleteResponse(
        success=True,
        deleted_count=deleted_count,
        message=f"Deleted {deleted_count} knowledge file(s)",
    )


@router.post("/files/{file_id}/reindex", response_model=KnowledgeFileResponse)
@require_permission("threads", "write")
async def reindex_knowledge_file(file_id: str, request: Request) -> KnowledgeFileResponse:
    try:
        file = await reindex_user_knowledge_file(get_effective_user_id(), file_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if file is None:
        raise HTTPException(status_code=404, detail=f"Knowledge file {file_id!r} not found")
    return _file_response(file)


@router.post("/search", response_model=KnowledgeSearchResponse)
@require_permission("threads", "read")
async def search_knowledge_endpoint(body: KnowledgeSearchRequest, request: Request) -> KnowledgeSearchResponse:
    try:
        results = await search_knowledge(
            get_effective_user_id(),
            body.query,
            limit=body.limit,
            scope_paths=body.scope_paths,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    responses = [_search_response(result) for result in results]
    return KnowledgeSearchResponse(results=responses, count=len(responses))
