from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, desc, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError

from deerflow.config import get_app_config
from deerflow.config.knowledge_config import KnowledgeConfig
from deerflow.config.paths import get_paths
from deerflow.persistence.engine import get_engine, get_session_factory
from deerflow.persistence.knowledge.model import KnowledgeChunkRow, KnowledgeFileRow, KnowledgeFolderRow
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".log",
}
MAX_CHUNK_CHARS = 2800
CHUNK_OVERLAP_CHARS = 250
MAX_QUERY_TERMS = 12
MAX_SNIPPET_CHARS = 700
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_SCOPE_PATHS = 50
_PGVECTOR_SCHEMA_READY: set[int] = set()
_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


@dataclass(slots=True)
class KnowledgeFile:
    id: str
    filename: str
    folder_path: str
    display_path: str
    original_filename: str
    content_type: str | None
    size: int
    sha256: str
    status: str
    error: str | None
    chunk_count: int
    created_at: str
    updated_at: str


@dataclass(slots=True)
class KnowledgeFolder:
    id: str
    name: str
    path: str
    parent_path: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class KnowledgeSearchResult:
    file_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    heading: str | None
    snippet: str
    score: float


@dataclass(slots=True)
class KnowledgeDocumentContext:
    file_id: str
    filename: str
    chunk_count: int
    content: str


@dataclass(slots=True)
class _KnowledgeCandidate:
    file_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    heading: str | None
    text: str
    semantic_score: float = 0.0
    keyword_score: float = 0.0


def _require_session_factory():
    sf = get_session_factory()
    if sf is None:
        raise RuntimeError("Knowledge base requires sqlite or postgres persistence; database.backend=memory is not supported.")
    return sf


def _get_knowledge_config() -> KnowledgeConfig:
    try:
        return get_app_config().knowledge
    except Exception:
        logger.debug("Falling back to default knowledge config", exc_info=True)
        return KnowledgeConfig()


def _semantic_enabled(config: KnowledgeConfig | None = None) -> bool:
    config = config or _get_knowledge_config()
    return bool(config.semantic_search_enabled and config.embedding_api_key and config.embedding_model)


def _embedding_text(filename: str, heading: str | None, text: str) -> str:
    parts = [filename]
    if heading:
        parts.append(heading)
    parts.append(text)
    return "\n".join(parts)


def _embedding_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _vector_to_json(vector: list[float]) -> str:
    return json.dumps(vector, separators=(",", ":"), ensure_ascii=False)


def _vector_literal(vector: list[float]) -> str:
    values = []
    for item in vector:
        value = float(item)
        if not math.isfinite(value):
            raise ValueError("Embedding vector contains a non-finite value")
        values.append(f"{value:.9g}")
    return "[" + ",".join(values) + "]"


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _normalize_embedding(raw: Any, expected_dim: int) -> list[float]:
    if not isinstance(raw, list | tuple):
        raise ValueError("Embedding response item is not a vector")
    vector = [float(item) for item in raw]
    if len(vector) != expected_dim:
        raise ValueError(f"Embedding dimension mismatch: expected {expected_dim}, got {len(vector)}")
    if any(not math.isfinite(item) for item in vector):
        raise ValueError("Embedding vector contains a non-finite value")
    return vector


async def _embed_texts(texts: list[str], config: KnowledgeConfig | None = None) -> list[list[float]] | None:
    config = config or _get_knowledge_config()
    if not _semantic_enabled(config) or not texts:
        return None

    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.warning("OpenAI SDK is unavailable; private knowledge semantic retrieval disabled")
        return None

    client = AsyncOpenAI(api_key=config.embedding_api_key, base_url=config.embedding_base_url)
    vectors: list[list[float]] = []
    batch_size = max(1, int(config.embedding_batch_size or 1))
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            response = await client.embeddings.create(
                model=config.embedding_model,
                input=batch,
                dimensions=config.embedding_dimensions,
            )
        except Exception:
            logger.warning("Knowledge embedding request failed; falling back to keyword retrieval", exc_info=True)
            return None
        vectors.extend(_normalize_embedding(item.embedding, config.embedding_dimensions) for item in response.data)

    if len(vectors) != len(texts):
        logger.warning("Knowledge embedding response count mismatch; falling back to keyword retrieval")
        return None
    return vectors


async def _ensure_pgvector_schema(session, config: KnowledgeConfig) -> bool:
    engine = get_engine()
    if engine is None or engine.dialect.name != "postgresql":
        return False

    dim = int(config.embedding_dimensions)
    if dim in _PGVECTOR_SCHEMA_READY:
        return True

    try:
        await session.execute(sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
        await session.execute(sql_text(f"ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding vector({dim})"))
        await session.execute(
            sql_text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw "
                "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
            )
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("pgvector schema is unavailable; private knowledge semantic retrieval will use fallback", exc_info=True)
        return False

    _PGVECTOR_SCHEMA_READY.add(dim)
    return True


async def _store_pgvector_embeddings(session, chunk_ids: list[str], embeddings: list[list[float]]) -> None:
    for chunk_id, embedding in zip(chunk_ids, embeddings, strict=True):
        await session.execute(
            sql_text("UPDATE knowledge_chunks SET embedding = CAST(:embedding AS vector) WHERE id = :chunk_id"),
            {"embedding": _vector_literal(embedding), "chunk_id": chunk_id},
        )


def _safe_filename(filename: str) -> str:
    name = Path(filename or "upload").name.strip()
    if not name:
        name = "upload"
    name = re.sub(r"[\x00-\x1f]+", "", name)
    name = re.sub(r"[\\/]+", "_", name)
    if name in {".", ".."}:
        name = "upload"
    return name[:180]


def _safe_relative_path(filename: str) -> tuple[str, str]:
    raw = (filename or "upload").replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        item = part.strip()
        if not item or item in {".", ".."}:
            continue
        item = re.sub(r"[\x00-\x1f]+", "", item)
        item = re.sub(r"[:*?\"<>|]+", "_", item)
        if item:
            parts.append(item[:180])
    if not parts:
        return "", "upload"
    filename_part = _safe_filename(parts[-1])
    folder_path = "/".join(parts[:-1])[:1000]
    return folder_path, filename_part


def _safe_folder_path(folder_path: str) -> str:
    folder, filename = _safe_relative_path(f"{folder_path.rstrip('/')}/placeholder")
    if filename != "placeholder":
        return folder
    return folder


def _normalize_scope_path(path: str) -> str:
    raw = (path or "").replace("\\", "/")
    parts: list[str] = []
    for part in raw.split("/"):
        item = part.strip()
        if not item or item in {".", ".."}:
            continue
        item = re.sub(r"[\x00-\x1f]+", "", item)
        item = re.sub(r"[:*?\"<>|]+", "_", item)
        if item:
            parts.append(item[:180])
    return "/".join(parts)[:1000]


def _normalize_scope_paths(scope_paths: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in scope_paths or []:
        if not isinstance(raw, str):
            continue
        path = _normalize_scope_path(raw)
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)
        if len(normalized) >= MAX_SCOPE_PATHS:
            break
    return normalized


def _split_display_path(path: str) -> tuple[str, str]:
    normalized = _normalize_scope_path(path)
    if "/" not in normalized:
        return "", normalized
    folder, filename = normalized.rsplit("/", 1)
    return folder, filename


def _scope_filter(scope_paths: list[str]):
    if not scope_paths:
        return None
    conditions = []
    for scope in scope_paths:
        folder, filename = _split_display_path(scope)
        conditions.append(KnowledgeFileRow.folder_path == scope)
        conditions.append(KnowledgeFileRow.folder_path.like(f"{scope}/%"))
        if filename:
            conditions.append(and_(KnowledgeFileRow.folder_path == folder, KnowledgeFileRow.filename == filename))
    return or_(*conditions)


def _scope_sql_filter(alias: str, scope_paths: list[str]) -> tuple[str, dict[str, str]]:
    if not scope_paths:
        return "", {}
    clauses: list[str] = []
    params: dict[str, str] = {}
    for index, scope in enumerate(scope_paths):
        folder, filename = _split_display_path(scope)
        params[f"scope_{index}"] = scope
        params[f"scope_prefix_{index}"] = f"{scope}/%"
        params[f"scope_file_folder_{index}"] = folder
        params[f"scope_file_name_{index}"] = filename
        clauses.append(
            f"({alias}.folder_path = :scope_{index} "
            f"OR {alias}.folder_path LIKE :scope_prefix_{index} "
            f"OR ({alias}.folder_path = :scope_file_folder_{index} AND {alias}.filename = :scope_file_name_{index}))"
        )
    return " AND (" + " OR ".join(clauses) + ")", params


def _display_path(folder_path: str, filename: str) -> str:
    folder = (folder_path or "").strip("/")
    return f"{folder}/{filename}" if folder else filename


def _folder_name(path: str) -> str:
    normalized = _safe_folder_path(path)
    return normalized.rsplit("/", 1)[-1] if normalized else ""


def _parent_folder(path: str) -> str:
    normalized = _safe_folder_path(path)
    if "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0]


def _row_to_folder(row: KnowledgeFolderRow) -> KnowledgeFolder:
    return KnowledgeFolder(
        id=row.id,
        name=row.name,
        path=row.path,
        parent_path=row.parent_path or "",
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _row_to_file(row: KnowledgeFileRow) -> KnowledgeFile:
    folder_path = row.folder_path or ""
    return KnowledgeFile(
        id=row.id,
        filename=row.filename,
        folder_path=folder_path,
        display_path=_display_path(folder_path, row.filename),
        original_filename=row.original_filename,
        content_type=row.content_type,
        size=row.size,
        sha256=row.sha256,
        status=row.status,
        error=row.error,
        chunk_count=row.chunk_count,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


async def _ensure_folder_rows(session, user_id: str, folder_path: str) -> None:
    folder = _safe_folder_path(folder_path)
    if not folder:
        return
    current = ""
    for part in folder.split("/"):
        current = f"{current}/{part}" if current else part
        existing = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    KnowledgeFolderRow.path == current,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                KnowledgeFolderRow(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    path=current,
                    name=part,
                    parent_path=_parent_folder(current),
                )
            )


def _update_chunk_display_path(chunk: KnowledgeChunkRow, display_path: str) -> None:
    search_text = chunk.search_text or ""
    _old_first, separator, rest = search_text.partition("\n")
    chunk.search_text = f"{display_path}\n{rest}" if separator else display_path


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "") if m.group(0).strip()]


def _extract_text(path: Path) -> tuple[str, Path | None]:
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="replace"), None
    if ext in CONVERTIBLE_EXTENSIONS:
        md_path = path.with_suffix(".md")
        if not md_path.exists():
            raise RuntimeError("Document conversion did not produce markdown.")
        return md_path.read_text(encoding="utf-8", errors="replace"), md_path
    raise RuntimeError(f"Unsupported knowledge file type: {ext or '(no extension)'}")


async def _convert_if_needed(path: Path) -> Path | None:
    if path.suffix.lower() in CONVERTIBLE_EXTENSIONS:
        return await convert_file_to_markdown(path)
    return None


def _split_text(text: str) -> list[tuple[str | None, str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[tuple[str | None, str]] = []
    heading: str | None = None
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buf, size
        body = "\n".join(buf).strip()
        if body:
            chunks.extend(_split_large_chunk(heading, body))
        buf = []
        size = 0

    for line in normalized.split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            if buf:
                flush()
            heading = match.group(2).strip()[:512]
            buf.append(line)
            size = len(line)
            continue
        if size + len(line) + 1 > MAX_CHUNK_CHARS and buf:
            flush()
        buf.append(line)
        size += len(line) + 1
    flush()
    return chunks


def _split_large_chunk(heading: str | None, text: str) -> list[tuple[str | None, str]]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [(heading, text)]
    chunks: list[tuple[str | None, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + MAX_CHUNK_CHARS)
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind("。", start, end), text.rfind(".", start, end))
            if boundary > start + MAX_CHUNK_CHARS // 2:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append((heading, chunk))
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP_CHARS, start + 1)
    return chunks


def _score_chunk(query: str, query_terms: list[str], row: KnowledgeChunkRow, filename: str) -> float:
    haystack = row.search_text.lower()
    filename_l = filename.lower()
    heading_l = (row.heading or "").lower()
    score = 0.0
    if query and query.lower() in haystack:
        score += 6.0
    if query and query.lower() in filename_l:
        score += 4.0
    if query and query.lower() in heading_l:
        score += 3.0
    for term in query_terms:
        if not term:
            continue
        count = haystack.count(term)
        if count:
            score += 1.0 + math.log1p(count)
        if term in filename_l:
            score += 2.0
        if term in heading_l:
            score += 1.5
    return score


def _snippet(text: str, query_terms: list[str]) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= MAX_SNIPPET_CHARS:
        return compact
    lower = compact.lower()
    positions = [lower.find(term) for term in query_terms if term and lower.find(term) >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - 160)
    end = min(len(compact), start + MAX_SNIPPET_CHARS)
    start = max(0, end - MAX_SNIPPET_CHARS)
    prefix = "..." if start else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


async def index_knowledge_file(
    *,
    user_id: str,
    source_path: Path,
    original_filename: str,
    content_type: str | None = None,
) -> KnowledgeFile:
    sf = _require_session_factory()
    paths = get_paths()
    files_dir = paths.user_knowledge_files_dir(user_id)
    markdown_dir = paths.user_knowledge_markdown_dir(user_id)
    files_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)

    folder_path, original = _safe_relative_path(original_filename)
    original_filename = _display_path(folder_path, original)[:255]
    digest = hashlib.sha256()
    size = 0
    with source_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(UPLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    sha256 = digest.hexdigest()

    async with sf() as session:
        existing = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.sha256 == sha256,
                    KnowledgeFileRow.folder_path == folder_path,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _row_to_file(existing)

    file_id = uuid.uuid4().hex
    suffix = Path(original).suffix
    stored_name = f"{file_id}{suffix}"
    stored_path = files_dir / stored_name
    shutil.copyfile(source_path, stored_path)
    try:
        os.chmod(stored_path, 0o644)
    except OSError:
        pass

    status = "indexed"
    error: str | None = None
    markdown_path: Path | None = None
    chunks: list[tuple[str | None, str]] = []
    try:
        converted = await _convert_if_needed(stored_path)
        if converted is not None:
            markdown_path = markdown_dir / f"{file_id}.md"
            shutil.copyfile(converted, markdown_path)
        text, direct_md = _extract_text(markdown_path or stored_path)
        markdown_path = markdown_path or direct_md
        chunks = _split_text(text)
        if not chunks:
            status = "failed"
            error = "No readable text could be extracted."
    except Exception as exc:
        status = "failed"
        error = str(exc)[:2000]

    config = _get_knowledge_config()
    embedding_texts = [_embedding_text(original, heading, text) for heading, text in chunks] if status == "indexed" else []
    embeddings = await _embed_texts(embedding_texts, config) if embedding_texts else None

    row = KnowledgeFileRow(
        id=file_id,
        user_id=user_id,
        filename=original,
        folder_path=folder_path,
        original_filename=original_filename,
        content_type=content_type,
        size=size,
        sha256=sha256,
        status=status,
        error=error,
        source_path=str(stored_path),
        markdown_path=str(markdown_path) if markdown_path else None,
        chunk_count=len(chunks),
    )

    async with sf() as session:
        pgvector_ready = await _ensure_pgvector_schema(session, config) if embeddings else False
        await _ensure_folder_rows(session, user_id, folder_path)
        session.add(row)
        chunk_ids: list[str] = []
        for idx, (heading, text) in enumerate(chunks):
            chunk_id = f"{file_id}:{idx}"
            embedding_text = embedding_texts[idx] if embeddings else ""
            embedding = embeddings[idx] if embeddings else None
            chunk_ids.append(chunk_id)
            session.add(
                KnowledgeChunkRow(
                    id=chunk_id,
                    file_id=file_id,
                    user_id=user_id,
                    chunk_index=idx,
                    heading=heading,
                    text=text,
                    search_text=f"{original_filename}\n{heading or ''}\n{text}".lower(),
                    token_count=max(1, len(text) // 4),
                    embedding_model=config.embedding_model if embedding else None,
                    embedding_dim=config.embedding_dimensions if embedding else None,
                    embedding_text_hash=_embedding_hash(embedding_text) if embedding else None,
                    embedding_json=_vector_to_json(embedding) if embedding else None,
                )
            )
        if embeddings and pgvector_ready:
            await session.flush()
            await _store_pgvector_embeddings(session, chunk_ids, embeddings)
        await session.commit()
        await session.refresh(row)
        return _row_to_file(row)


async def list_knowledge_files(user_id: str) -> list[KnowledgeFile]:
    sf = _require_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                select(KnowledgeFileRow)
                .where(KnowledgeFileRow.user_id == user_id)
                .order_by(KnowledgeFileRow.folder_path, KnowledgeFileRow.filename, desc(KnowledgeFileRow.created_at))
            )
        ).scalars()
        return [_row_to_file(row) for row in rows]


async def list_knowledge_folders(user_id: str) -> list[KnowledgeFolder]:
    sf = _require_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                select(KnowledgeFolderRow)
                .where(KnowledgeFolderRow.user_id == user_id)
                .order_by(KnowledgeFolderRow.path)
            )
        ).scalars()
        return [_row_to_folder(row) for row in rows]


async def create_knowledge_folder(user_id: str, folder_path: str) -> KnowledgeFolder:
    folder = _safe_folder_path(folder_path)
    if not folder:
        raise RuntimeError("Folder name is required.")
    sf = _require_session_factory()
    async with sf() as session:
        await _ensure_folder_rows(session, user_id, folder)
        await session.commit()
        row = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    KnowledgeFolderRow.path == folder,
                )
            )
        ).scalar_one()
        return _row_to_folder(row)


async def move_knowledge_file(
    user_id: str,
    file_id: str,
    *,
    target_folder_path: str | None = None,
    target_filename: str | None = None,
) -> KnowledgeFile | None:
    target_folder = _safe_folder_path(target_folder_path or "")
    new_filename = _safe_filename(target_filename or "")
    sf = _require_session_factory()
    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.id == file_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        filename = new_filename if target_filename is not None else row.filename
        if not filename:
            raise RuntimeError("Filename is required.")
        duplicate_name = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.folder_path == target_folder,
                    KnowledgeFileRow.filename == filename,
                    KnowledgeFileRow.id != file_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate_name is not None:
            raise RuntimeError(f"A knowledge file named {filename!r} already exists in this folder.")
        duplicate_content = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.folder_path == target_folder,
                    KnowledgeFileRow.sha256 == row.sha256,
                    KnowledgeFileRow.id != file_id,
                )
            )
        ).scalar_one_or_none()
        if duplicate_content is not None:
            raise RuntimeError("The target folder already contains this file content.")

        await _ensure_folder_rows(session, user_id, target_folder)
        row.folder_path = target_folder
        row.filename = filename
        row.original_filename = _display_path(target_folder, filename)[:255]
        display_path = _display_path(target_folder, filename).lower()
        chunks = (
            await session.execute(
                select(KnowledgeChunkRow).where(
                    KnowledgeChunkRow.user_id == user_id,
                    KnowledgeChunkRow.file_id == file_id,
                )
            )
        ).scalars().all()
        for chunk in chunks:
            _update_chunk_display_path(chunk, display_path)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RuntimeError("Could not move the knowledge file because the target conflicts with an existing file.") from exc
        await session.refresh(row)
        return _row_to_file(row)


async def copy_knowledge_file(
    user_id: str,
    file_id: str,
    *,
    target_folder_path: str,
    target_filename: str | None = None,
) -> KnowledgeFile | None:
    target_folder = _safe_folder_path(target_folder_path or "")
    sf = _require_session_factory()
    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.id == file_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        filename = _safe_filename(target_filename or row.filename)
        if not filename:
            raise RuntimeError("Filename is required.")
        if row.folder_path == target_folder:
            raise RuntimeError("Copying a knowledge file into the same folder is not supported.")
        existing = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.folder_path == target_folder,
                    or_(KnowledgeFileRow.filename == filename, KnowledgeFileRow.sha256 == row.sha256),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError("The target folder already contains a matching knowledge file.")
        source_path = Path(row.source_path)
        content_type = row.content_type

    if not source_path.is_file():
        raise RuntimeError("Stored knowledge file is missing.")
    return await index_knowledge_file(
        user_id=user_id,
        source_path=source_path,
        original_filename=_display_path(target_folder, filename),
        content_type=content_type,
    )


async def rename_knowledge_folder(user_id: str, folder_path: str, new_name: str) -> KnowledgeFolder | None:
    old_path = _safe_folder_path(folder_path)
    safe_name = _safe_filename(new_name)
    if not old_path or not safe_name:
        raise RuntimeError("Folder path and name are required.")
    parent = _parent_folder(old_path)
    return await move_knowledge_folder(user_id, old_path, _display_path(parent, safe_name) if parent else safe_name)


async def move_knowledge_folder(user_id: str, folder_path: str, target_path: str) -> KnowledgeFolder | None:
    old_path = _safe_folder_path(folder_path)
    new_path = _safe_folder_path(target_path)
    if not old_path or not new_path:
        raise RuntimeError("Folder path is required.")
    if new_path == old_path:
        return await create_knowledge_folder(user_id, old_path)
    if new_path.startswith(f"{old_path}/"):
        raise RuntimeError("A folder cannot be moved into itself.")

    sf = _require_session_factory()
    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    KnowledgeFolderRow.path == old_path,
                )
            )
        ).scalar_one_or_none()
        has_files = (
            await session.execute(
                select(KnowledgeFileRow.id).where(
                    KnowledgeFileRow.user_id == user_id,
                    or_(
                        KnowledgeFileRow.folder_path == old_path,
                        KnowledgeFileRow.folder_path.like(f"{old_path}/%"),
                    ),
                )
            )
        ).first()
        if row is None and has_files is None:
            return None
        conflict = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    KnowledgeFolderRow.path == new_path,
                )
            )
        ).scalar_one_or_none()
        if conflict is not None:
            raise RuntimeError(f"Folder {new_path!r} already exists.")

        await _ensure_folder_rows(session, user_id, _parent_folder(new_path))
        folder_rows = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    or_(
                        KnowledgeFolderRow.path == old_path,
                        KnowledgeFolderRow.path.like(f"{old_path}/%"),
                    ),
                )
            )
        ).scalars().all()
        if row is None:
            row = KnowledgeFolderRow(
                id=uuid.uuid4().hex,
                user_id=user_id,
                path=old_path,
                name=_folder_name(old_path),
                parent_path=_parent_folder(old_path),
            )
            session.add(row)
            folder_rows.append(row)

        for folder_row in folder_rows:
            suffix = folder_row.path[len(old_path) :].lstrip("/")
            updated_path = f"{new_path}/{suffix}" if suffix else new_path
            folder_row.path = updated_path
            folder_row.name = _folder_name(updated_path)
            folder_row.parent_path = _parent_folder(updated_path)

        file_rows = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    or_(
                        KnowledgeFileRow.folder_path == old_path,
                        KnowledgeFileRow.folder_path.like(f"{old_path}/%"),
                    ),
                )
            )
        ).scalars().all()
        for file_row in file_rows:
            suffix = file_row.folder_path[len(old_path) :].lstrip("/")
            updated_folder = f"{new_path}/{suffix}" if suffix else new_path
            file_row.folder_path = updated_folder
            file_row.original_filename = _display_path(updated_folder, file_row.filename)[:255]
            chunks = (
                await session.execute(
                    select(KnowledgeChunkRow).where(
                        KnowledgeChunkRow.user_id == user_id,
                        KnowledgeChunkRow.file_id == file_row.id,
                    )
                )
            ).scalars().all()
            display_path = _display_path(updated_folder, file_row.filename).lower()
            for chunk in chunks:
                _update_chunk_display_path(chunk, display_path)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise RuntimeError("Could not move the folder because the target conflicts with existing data.") from exc
        moved = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    KnowledgeFolderRow.path == new_path,
                )
            )
        ).scalar_one()
        return _row_to_folder(moved)


async def get_knowledge_file(user_id: str, file_id: str) -> KnowledgeFile | None:
    sf = _require_session_factory()
    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.id == file_id,
                )
            )
        ).scalar_one_or_none()
        return _row_to_file(row) if row else None


async def reindex_knowledge_file(user_id: str, file_id: str) -> KnowledgeFile | None:
    sf = _require_session_factory()
    paths = get_paths()
    markdown_dir = paths.user_knowledge_markdown_dir(user_id)
    markdown_dir.mkdir(parents=True, exist_ok=True)

    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.id == file_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        stored_path = Path(row.source_path)
        if not stored_path.is_file():
            row.status = "failed"
            row.error = "Stored knowledge file is missing."
            row.chunk_count = 0
            await session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.file_id == file_id))
            await session.commit()
            await session.refresh(row)
            return _row_to_file(row)

        status = "indexed"
        error: str | None = None
        markdown_path: Path | None = None
        chunks: list[tuple[str | None, str]] = []
        try:
            converted = await _convert_if_needed(stored_path)
            if converted is not None:
                markdown_path = markdown_dir / f"{file_id}.md"
                shutil.copyfile(converted, markdown_path)
            elif row.markdown_path:
                markdown_path = Path(row.markdown_path)
            text, direct_md = _extract_text(markdown_path or stored_path)
            markdown_path = markdown_path or direct_md
            chunks = _split_text(text)
            if not chunks:
                status = "failed"
                error = "No readable text could be extracted."
        except Exception as exc:
            status = "failed"
            error = str(exc)[:2000]

        config = _get_knowledge_config()
        display_path = _display_path(row.folder_path or "", row.filename)
        embedding_texts = [_embedding_text(display_path, heading, text) for heading, text in chunks] if status == "indexed" else []
        embeddings = await _embed_texts(embedding_texts, config) if embedding_texts else None
        pgvector_ready = await _ensure_pgvector_schema(session, config) if embeddings else False

        await session.execute(delete(KnowledgeChunkRow).where(KnowledgeChunkRow.file_id == file_id))
        row.status = status
        row.error = error
        row.markdown_path = str(markdown_path) if markdown_path else None
        row.chunk_count = len(chunks)
        chunk_ids: list[str] = []
        for idx, (heading, text) in enumerate(chunks):
            chunk_id = f"{file_id}:{idx}"
            embedding_text = embedding_texts[idx] if embeddings else ""
            embedding = embeddings[idx] if embeddings else None
            chunk_ids.append(chunk_id)
            session.add(
                KnowledgeChunkRow(
                    id=chunk_id,
                    file_id=file_id,
                    user_id=user_id,
                    chunk_index=idx,
                    heading=heading,
                    text=text,
                    search_text=f"{display_path}\n{heading or ''}\n{text}".lower(),
                    token_count=max(1, len(text) // 4),
                    embedding_model=config.embedding_model if embedding else None,
                    embedding_dim=config.embedding_dimensions if embedding else None,
                    embedding_text_hash=_embedding_hash(embedding_text) if embedding else None,
                    embedding_json=_vector_to_json(embedding) if embedding else None,
                )
            )
        if embeddings and pgvector_ready:
            await session.flush()
            await _store_pgvector_embeddings(session, chunk_ids, embeddings)
        await session.commit()
        await session.refresh(row)
        return _row_to_file(row)


async def delete_knowledge_file(user_id: str, file_id: str) -> bool:
    sf = _require_session_factory()
    async with sf() as session:
        row = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    KnowledgeFileRow.id == file_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        paths_to_delete = [row.source_path, row.markdown_path]
        await session.delete(row)
        await session.commit()
    for raw in paths_to_delete:
        if not raw:
            continue
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass
    return True


async def delete_knowledge_folder(user_id: str, folder_path: str) -> int:
    folder = _safe_folder_path(folder_path)
    if not folder:
        return 0

    sf = _require_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(
                select(KnowledgeFileRow).where(
                    KnowledgeFileRow.user_id == user_id,
                    or_(
                        KnowledgeFileRow.folder_path == folder,
                        KnowledgeFileRow.folder_path.like(f"{folder}/%"),
                    ),
                )
            )
        ).scalars().all()
        if not rows:
            folder_rows = (
                await session.execute(
                    select(KnowledgeFolderRow).where(
                        KnowledgeFolderRow.user_id == user_id,
                        or_(
                            KnowledgeFolderRow.path == folder,
                            KnowledgeFolderRow.path.like(f"{folder}/%"),
                        ),
                    )
                )
            ).scalars().all()
            if not folder_rows:
                return 0
            for folder_row in folder_rows:
                await session.delete(folder_row)
            await session.commit()
            return 0
        paths_to_delete = [(row.source_path, row.markdown_path) for row in rows]
        for row in rows:
            await session.delete(row)
        folder_rows = (
            await session.execute(
                select(KnowledgeFolderRow).where(
                    KnowledgeFolderRow.user_id == user_id,
                    or_(
                        KnowledgeFolderRow.path == folder,
                        KnowledgeFolderRow.path.like(f"{folder}/%"),
                    ),
                )
            )
        ).scalars().all()
        for folder_row in folder_rows:
            await session.delete(folder_row)
        await session.commit()

    for source_path, markdown_path in paths_to_delete:
        for raw in (source_path, markdown_path):
            if not raw:
                continue
            try:
                Path(raw).unlink(missing_ok=True)
            except OSError:
                pass
    return len(rows)


async def _keyword_candidates(
    session,
    user_id: str,
    query: str,
    terms: list[str],
    *,
    limit: int,
    scope_paths: list[str] | None = None,
) -> list[_KnowledgeCandidate]:
    if not terms:
        return []
    conditions = [KnowledgeChunkRow.search_text.ilike(f"%{term}%") for term in terms]
    stmt = (
        select(KnowledgeChunkRow, KnowledgeFileRow)
        .join(KnowledgeFileRow, KnowledgeFileRow.id == KnowledgeChunkRow.file_id)
        .where(KnowledgeChunkRow.user_id == user_id, KnowledgeFileRow.status == "indexed")
        .where(or_(*conditions))
        .limit(limit)
    )
    scope_condition = _scope_filter(scope_paths or [])
    if scope_condition is not None:
        stmt = stmt.where(scope_condition)
    rows = (await session.execute(stmt)).all()

    candidates: list[_KnowledgeCandidate] = []
    for chunk, file_row in rows:
        display_path = _display_path(file_row.folder_path or "", file_row.filename)
        score = _score_chunk(query, terms, chunk, display_path)
        if score <= 0:
            continue
        candidates.append(
            _KnowledgeCandidate(
                file_id=file_row.id,
                filename=display_path,
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                heading=chunk.heading,
                text=chunk.text,
                keyword_score=score,
            )
        )
    return candidates


async def _semantic_candidates_postgres(
    session,
    user_id: str,
    query_embedding: list[float],
    config: KnowledgeConfig,
    *,
    limit: int,
    scope_paths: list[str] | None = None,
) -> list[_KnowledgeCandidate]:
    if not await _ensure_pgvector_schema(session, config):
        return []

    scope_filter_sql, scope_params = _scope_sql_filter("kf", scope_paths or [])
    result = await session.execute(
        sql_text(
            f"""
            SELECT
              kc.id AS chunk_id,
              kc.file_id AS file_id,
              kc.chunk_index AS chunk_index,
              kc.heading AS heading,
              kc.text AS chunk_text,
              kf.filename AS filename,
              kf.folder_path AS folder_path,
              1 - (kc.embedding <=> CAST(:query_embedding AS vector)) AS semantic_score
            FROM knowledge_chunks kc
            JOIN knowledge_files kf ON kf.id = kc.file_id
            WHERE kc.user_id = :user_id
              AND kf.status = 'indexed'
              AND kc.embedding IS NOT NULL
              AND kc.embedding_model = :embedding_model
              AND kc.embedding_dim = :embedding_dim
              {scope_filter_sql}
            ORDER BY kc.embedding <=> CAST(:query_embedding AS vector)
            LIMIT :limit
            """
        ),
        {
            "user_id": user_id,
            "query_embedding": _vector_literal(query_embedding),
            "embedding_model": config.embedding_model,
            "embedding_dim": config.embedding_dimensions,
            "limit": limit,
            **scope_params,
        },
    )
    rows = result.mappings().all()
    return [
        _KnowledgeCandidate(
            file_id=str(row["file_id"]),
            filename=_display_path(str(row["folder_path"] or ""), str(row["filename"])),
            chunk_id=str(row["chunk_id"]),
            chunk_index=int(row["chunk_index"]),
            heading=row["heading"],
            text=str(row["chunk_text"]),
            semantic_score=max(0.0, float(row["semantic_score"] or 0.0)),
        )
        for row in rows
    ]


async def _semantic_candidates_sqlite(
    session,
    user_id: str,
    query_embedding: list[float],
    config: KnowledgeConfig,
    *,
    limit: int,
    scope_paths: list[str] | None = None,
) -> list[_KnowledgeCandidate]:
    stmt = (
        select(KnowledgeChunkRow, KnowledgeFileRow)
        .join(KnowledgeFileRow, KnowledgeFileRow.id == KnowledgeChunkRow.file_id)
        .where(
            KnowledgeChunkRow.user_id == user_id,
            KnowledgeFileRow.status == "indexed",
            KnowledgeChunkRow.embedding_json.is_not(None),
            KnowledgeChunkRow.embedding_model == config.embedding_model,
            KnowledgeChunkRow.embedding_dim == config.embedding_dimensions,
        )
    )
    scope_condition = _scope_filter(scope_paths or [])
    if scope_condition is not None:
        stmt = stmt.where(scope_condition)
    rows = (await session.execute(stmt)).all()
    candidates: list[_KnowledgeCandidate] = []
    for chunk, file_row in rows:
        try:
            chunk_embedding = json.loads(chunk.embedding_json or "[]")
            semantic_score = _cosine_similarity(query_embedding, [float(item) for item in chunk_embedding])
        except Exception:
            continue
        if semantic_score <= 0:
            continue
        candidates.append(
            _KnowledgeCandidate(
                file_id=file_row.id,
                filename=_display_path(file_row.folder_path or "", file_row.filename),
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                heading=chunk.heading,
                text=chunk.text,
                semantic_score=semantic_score,
            )
        )
    candidates.sort(key=lambda item: item.semantic_score, reverse=True)
    return candidates[:limit]


async def _semantic_candidates(
    session,
    user_id: str,
    query: str,
    config: KnowledgeConfig,
    *,
    scope_paths: list[str] | None = None,
) -> list[_KnowledgeCandidate]:
    query_embeddings = await _embed_texts([query], config)
    if not query_embeddings:
        return []
    query_embedding = query_embeddings[0]
    engine = get_engine()
    if engine is not None and engine.dialect.name == "postgresql":
        return await _semantic_candidates_postgres(
            session,
            user_id,
            query_embedding,
            config,
            limit=config.semantic_candidates,
            scope_paths=scope_paths,
        )
    return await _semantic_candidates_sqlite(
        session,
        user_id,
        query_embedding,
        config,
        limit=config.semantic_candidates,
        scope_paths=scope_paths,
    )


def _merge_candidates(
    semantic_candidates: list[_KnowledgeCandidate],
    keyword_candidates: list[_KnowledgeCandidate],
    config: KnowledgeConfig,
) -> list[tuple[float, _KnowledgeCandidate]]:
    by_id: dict[str, _KnowledgeCandidate] = {}
    for candidate in semantic_candidates:
        by_id[candidate.chunk_id] = candidate
    for candidate in keyword_candidates:
        existing = by_id.get(candidate.chunk_id)
        if existing is None:
            by_id[candidate.chunk_id] = candidate
        else:
            existing.keyword_score = max(existing.keyword_score, candidate.keyword_score)

    max_keyword = max((candidate.keyword_score for candidate in by_id.values()), default=0.0)
    ranked: list[tuple[float, _KnowledgeCandidate]] = []
    for candidate in by_id.values():
        semantic_component = max(0.0, min(candidate.semantic_score, 1.0))
        keyword_component = candidate.keyword_score / max_keyword if max_keyword > 0 else 0.0
        if semantic_candidates:
            score = config.semantic_weight * semantic_component + config.keyword_weight * keyword_component
        else:
            score = keyword_component
        if score > 0:
            ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


async def search_knowledge(
    user_id: str,
    query: str,
    *,
    limit: int = 5,
    scope_paths: list[str] | tuple[str, ...] | None = None,
) -> list[KnowledgeSearchResult]:
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 5), 20))
    terms = []
    seen: set[str] = set()
    for term in _tokenize(query):
        if term not in seen:
            terms.append(term)
            seen.add(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break

    config = _get_knowledge_config()
    normalized_scope_paths = _normalize_scope_paths(scope_paths)
    sf = _require_session_factory()
    async with sf() as session:
        semantic = (
            await _semantic_candidates(session, user_id, query, config, scope_paths=normalized_scope_paths)
            if _semantic_enabled(config)
            else []
        )
        keyword_limit = max(config.keyword_candidates, limit)
        keyword = await _keyword_candidates(
            session,
            user_id,
            query,
            terms,
            limit=keyword_limit,
            scope_paths=normalized_scope_paths,
        )

    if not semantic and not config.fallback_to_keyword:
        return []

    ranked = _merge_candidates(semantic, keyword, config)
    return [
        KnowledgeSearchResult(
            file_id=candidate.file_id,
            filename=candidate.filename,
            chunk_id=candidate.chunk_id,
            chunk_index=candidate.chunk_index,
            heading=candidate.heading,
            snippet=_snippet(candidate.text, terms),
            score=round(score, 3),
        )
        for score, candidate in ranked[:limit]
    ]


async def load_knowledge_documents(
    user_id: str,
    scope_paths: list[str] | tuple[str, ...] | None,
) -> list[KnowledgeDocumentContext]:
    normalized_scope_paths = _normalize_scope_paths(scope_paths)
    if not normalized_scope_paths:
        return []

    sf = _require_session_factory()
    async with sf() as session:
        stmt = (
            select(KnowledgeFileRow, KnowledgeChunkRow)
            .join(KnowledgeChunkRow, KnowledgeChunkRow.file_id == KnowledgeFileRow.id)
            .where(KnowledgeFileRow.user_id == user_id, KnowledgeFileRow.status == "indexed")
            .order_by(KnowledgeFileRow.folder_path, KnowledgeFileRow.filename, KnowledgeChunkRow.chunk_index)
        )
        scope_condition = _scope_filter(normalized_scope_paths)
        if scope_condition is not None:
            stmt = stmt.where(scope_condition)
        rows = (await session.execute(stmt)).all()

    documents: dict[str, KnowledgeDocumentContext] = {}
    parts_by_file: dict[str, list[str]] = {}
    for file_row, chunk in rows:
        display_path = _display_path(file_row.folder_path or "", file_row.filename)
        if file_row.id not in documents:
            documents[file_row.id] = KnowledgeDocumentContext(
                file_id=file_row.id,
                filename=display_path,
                chunk_count=0,
                content="",
            )
            parts_by_file[file_row.id] = []

        text = chunk.text.strip()
        if chunk.heading:
            text = f"{chunk.heading}\n{text}" if text else chunk.heading
        if text:
            parts_by_file[file_row.id].append(text)
        documents[file_row.id].chunk_count += 1

    for file_id, document in documents.items():
        document.content = "\n\n".join(parts_by_file[file_id]).strip()

    return [document for document in documents.values() if document.content]


async def _legacy_search_knowledge(user_id: str, query: str, *, limit: int = 5) -> list[KnowledgeSearchResult]:
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 5), 20))
    terms = []
    seen: set[str] = set()
    for term in _tokenize(query):
        if term not in seen:
            terms.append(term)
            seen.add(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    if not terms:
        return []

    sf = _require_session_factory()
    async with sf() as session:
        candidates = await _keyword_candidates(session, user_id, query, terms, limit=300)
    candidates.sort(key=lambda item: item.keyword_score, reverse=True)
    return [
        KnowledgeSearchResult(
            file_id=candidate.file_id,
            filename=candidate.filename,
            chunk_id=candidate.chunk_id,
            chunk_index=candidate.chunk_index,
            heading=candidate.heading,
            snippet=_snippet(candidate.text, terms),
            score=round(score, 3),
        )
        for candidate in candidates[:limit]
        if (score := candidate.keyword_score) > 0
    ]
