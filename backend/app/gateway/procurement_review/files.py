"""Bounded reference snapshots and agent-authored Word acceptance."""

import hashlib
import json
import os
import shutil
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from .rules import RISK_BASELINES, RULES, STATUSES

REFERENCE_ROLES = {"policy", "template_research", "template_testing", "template_software", "template_equipment"}


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reference_inventory(root):
    """Manifest lists all originals/full-text companions, never search fragments."""
    root = root.resolve(strict=True)
    manifest = root / "manifest.json"
    if manifest.stat().st_size > 64 * 1024:
        raise ValueError("Reference manifest too large")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data.get("version"), str) or not data["version"]:
        raise ValueError("Reference version required")
    documents = data["documents"]
    if len(documents) != len(REFERENCE_ROLES) or {d["role"] for d in documents} != REFERENCE_ROLES:
        raise ValueError("Policy and four templates required; rules are supplied by the task prompt")
    entries = []
    size = 0
    for doc in documents:
        if not {"original", "text"} <= set(doc):
            raise ValueError("Each reference needs an original and complete UTF-8 text")
        for kind in ("original", "text"):
            item = doc[kind]
            path = (root / item["path"]).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("Reference path escaped root")
            if path.suffix.lower() not in ({".pdf", ".doc", ".docx"} if kind == "original" else {".md", ".txt"}):
                raise ValueError("Unsupported reference file")
            size += path.stat().st_size
            if size > 128 * 1024 * 1024 or not path.stat().st_size:
                raise ValueError("Reference size limit")
            sha = digest(path)
            if sha != item["sha256"]:
                raise ValueError("Reference digest mismatch")
            if kind == "text":
                with path.open(encoding="utf-8") as stream:
                    while stream.read(64 * 1024):
                        pass  # Validate full UTF-8 text with bounded memory.
            entries.append({"role": doc["role"], "format": kind, "path": str(path), "sha256": sha})
    return {"version": data["version"], "manifest_sha256": digest(manifest), "entries": entries}


def snapshot_references(root, uploads, expected_sha):
    inventory = reference_inventory(root)
    if inventory["manifest_sha256"] != expected_sha:
        raise ValueError("References changed after submission")
    files = []
    for i, entry in enumerate(inventory["entries"]):
        source = Path(entry["path"])
        name = f"reference-{i:02d}{source.suffix.lower()}"
        target = uploads / name
        shutil.copyfile(source, target)
        if digest(target) != entry["sha256"]:
            raise ValueError("Reference changed while copying")
        files.append({"id": f"reference-{i:02d}", "name": source.name, "stored": name, "kind": "reference", "role": entry["role"], "format": entry["format"], "sha256": entry["sha256"], "path": "/mnt/user-data/uploads/" + name})
    return files


def validate_report(raw, submitted_names):
    """Check bounded DOCX structure, not semantic truth of the agent's review."""
    if not raw or len(raw) > 5 * 1024 * 1024:
        raise ValueError("Invalid Word report size")

    def xml(data):
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("Unsafe report XML")
        return ElementTree.fromstring(data)

    with zipfile.ZipFile(BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) > 2048 or len(set(names)) != len(names) or sum(i.file_size for i in infos) > 32 * 1024 * 1024:
            raise ValueError("Unbounded or duplicate DOCX parts")
        for info in infos:
            name = info.filename
            if name.startswith("/") or ".." in name.split("/") or "\\" in name or info.flag_bits & 1:
                raise ValueError("Invalid DOCX part")
            if any(part in name.lower() for part in ("vbaproject", "embeddings/", "activex/")):
                raise ValueError("Active report content")
            if name.endswith(".rels"):
                for rel in xml(archive.read(info)):
                    if rel.get("TargetMode") == "External" and not rel.get("Type", "").endswith("/hyperlink"):
                        raise ValueError("External report dependency")
        content_types = xml(archive.read("[Content_Types].xml"))
        if not any(e.get("PartName") == "/word/document.xml" and e.get("ContentType") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml" for e in content_types):
            raise ValueError("Not a plain DOCX report")
        document = xml(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    def text(node):
        return "".join(t.text or "" for t in node.findall(".//w:t", ns)).strip()

    paragraphs = [text(p) for p in document.findall(".//w:p", ns)]
    full = "\n".join(paragraphs)
    required = ["基本信息", "核心审查结果", "补充审查意见与材料覆盖", "审查结论"]
    if any(s not in full for s in required) or any(name not in full for name in submitted_names):
        raise ValueError("Missing report sections or input coverage")
    headers = ["序号", "检查项", "判定状态", "风险等级", "命中内容及材料位置", "基准依据", "修正建议", "提示"]
    tables = []
    for table in document.findall(".//w:tbl", ns):
        rows = [[text(cell) for cell in row.findall("w:tc", ns)] for row in table.findall("w:tr", ns)]
        if rows and rows[0] == headers:
            tables.append(rows)
    if len(tables) != 1 or len(tables[0]) != 9:
        raise ValueError("Exactly eight review rows required")
    statuses = []
    high = False
    for index, cells in enumerate(tables[0][1:], 1):
        key = str(index)
        if len(cells) != 8 or not all(cells) or cells[:2] != [key, RULES[key][0]] or cells[2] not in STATUSES:
            raise ValueError("Incomplete review row")
        status = cells[2]
        risk = RISK_BASELINES[key] if status == "不符合" else {"符合": "未命中", "无法判断": "待核实", "不适用": "不适用"}[status]
        if cells[3].replace("；", "").replace(";", "").replace(" ", "") != "基准：" + RISK_BASELINES[key] + "本次：" + risk:
            raise ValueError("Review risk does not match status")
        high |= status == "不符合" and risk == "高风险"
        statuses.append(status)
    expected = "不通过" if high else "无法判断" if "无法判断" in statuses or set(statuses) == {"不适用"} else "有条件通过" if "不符合" in statuses else "通过"
    conclusions = [p.removesuffix("。") for p in paragraphs if p.startswith("合规评价：")]
    if conclusions != ["合规评价：" + expected]:
        raise ValueError("Report conclusion does not match review rows")
    return {"checks": 8, "conclusion": expected, "scope": "DOCX_STRUCTURE_ONLY_NOT_FACT_VERIFICATION"}


def snapshot_report(path, raw):
    """Preserve the accepted agent artifact byte-for-byte for callback retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    if temporary.is_symlink():
        raise ValueError("Invalid delivery temporary path")
    with temporary.open("wb") as stream:
        stream.write(raw)
    os.replace(temporary, path)
    return hashlib.sha256(raw).hexdigest()
