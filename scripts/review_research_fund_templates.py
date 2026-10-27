"""CI disposition of five byte-pinned, independently inspected Office templates.

The runtime Skill scanner remains unchanged. This CI-only review retains its raw
findings and resolves only the nested-archive finding for these exact deployed
assets, after bounded OOXML inspection. Changed or additional assets fail closed.
See docs/research-fund-production-source-20261027.md.
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
from xml.etree import ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "skills/public/research-fund-settlement"
REVIEWED = {
    "assets/reference-examples/field-rules-demo.xlsx": "4c67f86102bfe0baf3cc27d4476aaabe7d8c6842bad5c92f4d872e94eab173f8",
    "assets/reference-examples/group-company-example.docx": "85dc59c86546a68da6d2173de7662ced2769593e5db88d621a0fd7779b846a51",
    "assets/reference-examples/group-company-standard-input.xlsx": "6f2d1341c7bf304bc4b7bbf44c960a542a441d53947e36bbde3269a15b488b7f",
    "assets/reference-examples/miit-example.docx": "34bc5dd3f8f951e6d6647f35f23f384ce95bb00b7a3a02a640a6a93a313a1b62",
    "assets/reference-examples/national-science-example.docx": "57539ce2de776ba81aa9358230e061cb9f97fb2ca441827f6dd6dae98584c883",
}


def inspect_office(data: bytes, suffix: str) -> None:
    if suffix not in {".docx", ".xlsx"} or len(data) > 4 * 1024 * 1024:
        raise ValueError("UNSUPPORTED_OFFICE_ASSET")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 512 or sum(i.file_size for i in infos) > 32 * 1024 * 1024:
            raise ValueError("OFFICE_ARCHIVE_LIMIT")
        names = set()
        parts = {}
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            if (not name or name.startswith("/") or "\\" in name or ":" in name
                    or any(p in {".", ".."} for p in name.rstrip("/").split("/"))
                    or name.casefold() in names or info.flag_bits & 1
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("UNSAFE_OFFICE_MEMBER")
            names.add(name.casefold())
            if info.is_dir():
                continue
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError("OFFICE_MEMBER_LIMIT")
            with archive.open(info) as stream:
                payload = stream.read(8 * 1024 * 1024 + 1)
            if len(payload) != info.file_size:
                raise ValueError("OFFICE_MEMBER_SIZE")
            extension = ".rels" if path.name == ".rels" else path.suffix.lower()
            if extension in {".png", ".jpg", ".jpeg"}:
                magic = b"\x89PNG\r\n\x1a\n" if extension == ".png" else b"\xff\xd8\xff"
                if not payload.startswith(magic):
                    raise ValueError("OFFICE_IMAGE_MAGIC")
                continue
            if extension not in {".xml", ".rels"}:
                raise ValueError("UNREVIEWED_OFFICE_BINARY")
            upper = payload.replace(b"\x00", b"").upper()
            if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
                raise ValueError("OFFICE_XML_ENTITY")
            tree = ET.fromstring(payload)
            parts[name] = tree
            for node in tree.iter():
                local = node.tag.rsplit("}", 1)[-1].lower()
                if local in {"oleobject", "control", "altchunk", "object", "ddelink", "ddeitem", "externallink", "attachedtemplate"}:
                    raise ValueError("ACTIVE_OFFICE_XML")
                if local in {"instrtext", "fldsimple"}:
                    value = " ".join([node.text or "", *node.attrib.values()])
                    if re.search(r"\b(DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|LINK)\b", value, re.I):
                        raise ValueError("ACTIVE_OFFICE_FIELD")
                if local == "f" and re.search(r"\[|WEBSERVICE\s*\(|HYPERLINK\s*\(|\|", node.text or "", re.I):
                    raise ValueError("EXTERNAL_OFFICE_FORMULA")
                if local == "relationship":
                    target = node.get("Target", "")
                    kind = node.get("Type", "").lower()
                    if (node.get("TargetMode", "").lower() == "external" or ":" in target or "\\" in target
                            or target.startswith("/") or any(x in kind for x in ("oleobject", "vbaproject", "activex", "afchunk", "attachedtemplate", "externallink"))):
                        raise ValueError("EXTERNAL_OR_ACTIVE_OFFICE_RELATIONSHIP")
                    source = posixpath.dirname(posixpath.dirname(name))
                    if posixpath.normpath(posixpath.join(source, target)).startswith("../"):
                        raise ValueError("OFFICE_RELATIONSHIP_ESCAPE")
                content_type = node.get("ContentType", "").lower()
                if any(x in content_type for x in ("macro", "vba", "activex", "oleobject", "html")):
                    raise ValueError("ACTIVE_OFFICE_CONTENT_TYPE")
        required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
        if not {"[Content_Types].xml", "_rels/.rels", required}.issubset(parts):
            raise ValueError("OFFICE_STRUCTURE_MISSING")


def resolve_reviewed_findings(snapshot: dict, facts: dict) -> bool:
    completeness = facts.get("completeness", {})
    if (facts.get("reader_errors") or facts.get("analyzer_errors") or snapshot.get("truncated")
            or not completeness.get("package_enumerated") or not completeness.get("text_content_complete")
            or completeness.get("truncated") or completeness.get("not_assessed")):
        return False
    entries = {entry["path"]: entry for entry in snapshot["files"]}
    try:
        for path, expected in REVIEWED.items():
            entry = entries[path]
            if entry["kind"] != "binary":
                return False
            data = base64.b64decode(entry["content_base64"], validate=True)
            if hashlib.sha256(data).hexdigest() != expected:
                return False
            inspect_office(data, PurePosixPath(path).suffix)
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError, RuntimeError, OSError):
        return False
    resolved = set()
    for finding in facts["findings"]:
        if finding["severity"] in {"info", "warning"}:
            continue
        if (finding["severity"] == "error" and finding["source"] == "skillscan"
                and finding["rule_id"] == "package-nested-archive" and finding["path"] in REVIEWED):
            resolved.add(finding["path"])
        else:
            return False
    return resolved == set(REVIEWED)


def main() -> int:
    sys.path.insert(0, str(ROOT / "backend/packages/harness"))
    from deerflow.skills.review.analyzer import analyze_skill_package
    from deerflow.skills.review.cli import _print_text
    from deerflow.skills.review.readers import LocalDirectoryReader

    snapshot = LocalDirectoryReader(ROOT / PACKAGE).read()
    facts = analyze_skill_package(snapshot)
    _print_text(facts)  # Retain raw findings, including all five archive errors.
    if not resolve_reviewed_findings(snapshot, facts):
        print("[skill-review] Reviewed Office assets did not resolve all blocking findings.")
        return 1
    print("[skill-review] Five unchanged byte-pinned Office assets unpacked and reviewed; raw findings retained. No other errors waived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
