"""Work cloud filename lookup using the current user's search grant."""

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape

from .client import DOC_ID_RE, WORK_FILE_ID_RE, WORK_LEGACY_SEARCH_TITLE_LIMIT, WORK_SEARCH_PAGE_SIZE, WORK_SEARCH_QUERY_LIMIT, WorkError, valid_page_token

SEARCH_JOB_PROTOCOL = 4
SEARCH_SCOPE = "search:docs:read"
LEGACY_SEARCH_SCOPE = "drive:drive:readonly"
_PAGE_LIMIT = 3
_KINDS = {"DOCX": ("docx", "docx"), "SHEET": ("sheet", "sheets"), "BITABLE": ("bitable", "base"), "FILE": ("drive", "file")}
_HIGHLIGHT_TAG = re.compile(r"</?hb?>", re.IGNORECASE)
_BEIJING = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class WorkSearchSettings:
    scope: str = ""

    @classmethod
    def from_env(cls, profile: str):
        if profile not in {"uat", "production"} or os.getenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED") != "true":
            return cls()
        scope = os.getenv(f"DEER_FLOW_WORK_{profile.upper()}_FILE_SEARCH_SCOPE", "").strip()
        return cls(scope if scope in {SEARCH_SCOPE, LEGACY_SEARCH_SCOPE} else "")


@dataclass(frozen=True)
class SearchCandidate:
    title: str
    kind: str
    token: str
    url: str
    updated_at: str = ""


@dataclass(frozen=True)
class SearchResults:
    items: tuple[SearchCandidate, ...]
    complete: bool


def _normalized_title(title: str) -> str:
    return unicodedata.normalize("NFC", title).strip().casefold()


def is_exact_title(candidate: str, requested: str) -> bool:
    return _normalized_title(candidate) == _normalized_title(requested)


def _updated_at(value: object) -> str:
    if type(value) is not int or not 0 < value < 4_102_444_800:
        return ""
    return datetime.fromtimestamp(value, tz=_BEIJING).strftime("%Y-%m-%d %H:%M")


def _candidate(unit: object, host: str) -> SearchCandidate | None:
    meta = unit.get("result_meta") if isinstance(unit, dict) else None
    if not isinstance(meta, dict):
        raise WorkError("file_search_invalid")
    doc_type = meta.get("doc_types")
    if not isinstance(doc_type, str):
        raise WorkError("file_search_invalid")
    if doc_type.upper() not in _KINDS or meta.get("is_cross_tenant") is True:
        return None
    raw_title = unit.get("title")
    if not isinstance(raw_title, str) or not raw_title:
        highlighted = unit.get("title_highlighted")
        raw_title = unescape(_HIGHLIGHT_TAG.sub("", highlighted)) if isinstance(highlighted, str) else ""
    return _make_candidate(raw_title, doc_type, meta.get("token"), host, _updated_at(meta.get("update_time")))


def _make_candidate(raw_title: object, doc_type: str, token: object, host: str, updated_at: str = "") -> SearchCandidate:
    kind, path = _KINDS[doc_type.upper()]
    token_pattern = DOC_ID_RE if kind == "docx" else WORK_FILE_ID_RE
    if not isinstance(token, str) or not token_pattern.fullmatch(token):
        raise WorkError("file_search_invalid")
    title = raw_title.strip() if isinstance(raw_title, str) else ""
    if not title or len(title) > WORK_LEGACY_SEARCH_TITLE_LIMIT or not title.isprintable():
        raise WorkError("file_search_invalid")
    return SearchCandidate(title, kind, token, f"https://{host}/{path}/{token}", updated_at)


def _legacy_candidate(unit: object, host: str, requested: str) -> SearchCandidate | None:
    if not isinstance(unit, dict) or not isinstance(unit.get("docs_type"), str) or not isinstance(unit.get("title"), str):
        raise WorkError("file_search_invalid")
    doc_type = unit["docs_type"].upper()
    title = _normalized_title(unit["title"])
    # The legacy API can also match document content. Only expose title matches.
    if not title or requested not in title:
        return None
    if doc_type == "DOC" and title == requested:
        # The request filter "doc" includes both document formats. A generic
        # response type cannot identify a v2 Docx resource or a safe read path.
        raise WorkError("file_search_invalid")
    if doc_type not in _KINDS:
        return None
    return _make_candidate(unit["title"], doc_type, unit.get("docs_token"), host)


async def search_documents(api, token: str, title: str, *, host: str, guard, scope: str = SEARCH_SCOPE) -> SearchResults:
    """Inspect up to three search pages and keep their typed resource identities."""
    title = title.strip()
    if not title or len(title) > WORK_LEGACY_SEARCH_TITLE_LIMIT or not title.isprintable():
        raise WorkError("invalid_search_title")
    if scope not in {SEARCH_SCOPE, LEGACY_SEARCH_SCOPE}:
        raise WorkError("file_search_unavailable")
    legacy = scope == LEGACY_SEARCH_SCOPE
    requested = _normalized_title(title)
    query = title[:WORK_SEARCH_QUERY_LIMIT].rstrip()
    seen: dict[tuple[str, str], SearchCandidate] = {}
    cursors = set()
    cursor = None
    offset = 0
    previous_page = None
    complete = False
    malformed = False
    for _ in range(_PAGE_LIMIT):
        await guard()
        if legacy:
            response = await api("file_search_legacy", token=token, search_query=title, search_offset=offset)
        else:
            response = await api("file_search", token=token, search_query=query, page_token=cursor)
        data = response.get("data")
        units = data.get("docs_entities" if legacy else "res_units", []) if isinstance(data, dict) else None
        if not isinstance(units, list) or len(units) > WORK_SEARCH_PAGE_SIZE:
            raise WorkError("file_search_invalid")
        if legacy and (units == previous_page or (offset and not units)):
            break
        previous_page = units
        for unit in units:
            try:
                candidate = _legacy_candidate(unit, host, requested) if legacy else _candidate(unit, host)
            except WorkError:
                malformed = True
                continue
            if candidate is not None:
                key = candidate.kind, candidate.token
                previous = seen.get(key)
                if previous is None or (not is_exact_title(previous.title, title) and is_exact_title(candidate.title, title)):
                    seen[key] = candidate
        if data.get("has_more") is False:
            complete = not malformed
            break
        if data.get("has_more") is not True:
            break
        if legacy:
            if not units:
                break
            offset += len(units)
            continue
        cursor = data.get("page_token")
        if not valid_page_token(cursor) or cursor in cursors:
            break
        cursors.add(cursor)
    items = sorted(seen.values(), key=lambda item: not is_exact_title(item.title, title))
    return SearchResults(tuple(items), complete)
