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


def _near_title_parts(title: str) -> tuple[str, str]:
    value = unicodedata.normalize("NFKC", title).strip().casefold()
    suffix = re.search(r"\.(docx|xlsx|pdf)\Z", value)
    return (value[: suffix.start()], suffix[1]) if suffix else (value, "")


def _near_title_key(title: str) -> str:
    # This key admits candidates only. Never use it for automatic reading.
    value, _ = _near_title_parts(title)
    value = "".join(char for char in value if not char.isspace())
    value = re.sub(r"(?<!\d)((?:19|20)\d{2})年", r"\1", value)
    return "".join(char for char in value if not unicodedata.category(char).startswith("P"))


def _title_references(title: str) -> tuple:
    value, _ = _near_title_parts(title)
    numbers = tuple(re.findall(r"\d+(?:[._-]\d+)*", value))
    ordinals = tuple(re.findall(r"第[零〇一二两三四五六七八九十百千]+(?:版|期|号|章|册|卷)", value))
    # Short Latin segments commonly identify projects, revisions or versions.
    # Protect them rather than interpreting A/B or V1a/V1b as spelling errors.
    identifiers = tuple(part for part in re.findall(r"[a-z]+", value) if len(part) <= 3)
    return numbers, ordinals, identifiers


def _bounded_distance(left: str, right: str, limit: int) -> int:
    """Levenshtein distance capped at 1/2, over validated <=240-char titles."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, 1):
        current = [limit + 1] * (len(right) + 1)
        current[0] = index
        for column in range(max(1, index - limit), min(len(right), index + limit) + 1):
            current[column] = min(current[column - 1] + 1, previous[column] + 1, previous[column - 1] + (char != right[column - 1]))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _literal_title_contains(candidate: str, requested: str) -> bool:
    """Preserve containment without matching part of a longer number/version.

    References outside the matched title are allowed: a backup suffix such as
    '备份2' is not a reason to discard an otherwise literal title match.
    """
    numbers = tuple(re.finditer(r"\d+(?:[._-]\d+)*", requested))
    for match in re.finditer(re.escape(requested), candidate):
        valid = True
        for number in numbers:
            start, end = match.start() + number.start(), match.start() + number.end()
            if (start and candidate[start - 1].isdigit()) or (end < len(candidate) and candidate[end].isdigit()):
                valid = False
            if start >= 2 and candidate[start - 1] in "._-" and candidate[start - 2].isdigit():
                valid = False
            if end + 1 < len(candidate) and candidate[end] in "._-" and candidate[end + 1].isdigit():
                valid = False
            version = number.start() > 0 and requested[number.start() - 1].isascii() and requested[number.start() - 1].isalpha()
            if version and end < len(candidate) and candidate[end].isascii() and candidate[end].isalpha():
                valid = False
        if valid:
            return True
    return False


def _title_match_rank(candidate: str, requested: str) -> tuple[int, int] | None:
    """Keep literal matches and narrowly related titles, never content matches."""
    literal, query = _normalized_title(candidate), _normalized_title(requested)
    if literal == query:
        return 0, 0
    near, key = _near_title_key(candidate), _near_title_key(requested)
    _, candidate_suffix = _near_title_parts(candidate)
    _, requested_suffix = _near_title_parts(requested)
    same_references = _title_references(candidate) == _title_references(requested) and not (candidate_suffix and requested_suffix and candidate_suffix != requested_suffix)
    if key and near == key and same_references:
        return 1, 0
    if _literal_title_contains(literal, query):
        return 2, 0
    if not key or not near or not same_references:
        return None
    if key in near:
        return 3, 0
    # Numeric prefixes must not make an unrelated short word look similar.
    # Short titles may normalize/contain one another, but never use distance.
    if min(len(key), len(near)) <= 3 or max(len(key), len(near)) > WORK_LEGACY_SEARCH_TITLE_LIMIT or min(sum(char.isalpha() for char in key), sum(char.isalpha() for char in near)) < 4:
        return None
    limit = 2 if min(len(key), len(near)) >= 12 else 1
    distance = _bounded_distance(key, near, limit)
    return (4, distance) if distance <= limit else None


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
    if not unit["title"].isprintable() or len(unit["title"]) > WORK_LEGACY_SEARCH_TITLE_LIMIT:
        raise WorkError("file_search_invalid")
    doc_type = unit["docs_type"].upper()
    title = _normalized_title(unit["title"])
    # Legacy search can also match content. Admit only bounded title-related
    # results; normalized/near matches still require an explicit user choice.
    if not title or _title_match_rank(unit["title"], requested) is None:
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
        data = response.get("data") if isinstance(response, dict) else None
        units = data.get("docs_entities" if legacy else "res_units") if isinstance(data, dict) else None
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
                if previous is None or (_title_match_rank(candidate.title, title) or (5, 0)) < (_title_match_rank(previous.title, title) or (5, 0)):
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
    items = sorted(seen.values(), key=lambda item: _title_match_rank(item.title, title) or (5, 0))
    return SearchResults(tuple(items), complete)
