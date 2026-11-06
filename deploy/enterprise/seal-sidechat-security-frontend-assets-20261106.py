"""Seal bounded assets from the fixed local sidechat image, without network or production.

This supplements the immutable historical browser summary; it neither reruns the
browser nor manufactures a structured Playwright report from the line reporter.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile

MIB = 1024 * 1024
TAG = "deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1"
MANIFEST = "sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3"
CONFIG = "sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821"
ARCHIVE_SHA = "402aa5df479bc57be3923555a32240dedf614a239c58ca999c5ec2998590b61c"
ARCHIVE_BYTES = 127456768
BASE_MANIFEST = "sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d"
BASE_SOURCE_SHA = "bda551c495341f3b1ffbfe9f772317c3168b07200f4e3d5f39255772056198b4"
SIDECAR = "frontend/src/components/workspace/sidecar/sidecar-panel.tsx"
BEFORE_SIDECAR = "34cc753938dbf2b606b8dd7fd110d05a70f1dac156d7242216dbcf9a1773a1da"
AFTER_SIDECAR = "f89a2b08ab9da34503a0f7f0d9018e907051cbbb46a90dac0efe37a8fd1e56f1"
BUILD_PINS = {
    "RESULT.json": "344494a8a1b9513a5816266a070dfea536523c5ed42688b8c16cdc9324849aa8",
    "SOURCE.json": "6af8cd3cb41f08dda3719d73076f160f6c9715d66a72c83c778a88272aea1b3a",
    "SOURCE-CHANGE.json": "138fd398515b9fe80501edc9368865a59c74167f6c2b96b26d3181a897740dd9",
    "RUNTIME.json": "13f54b6284a9313118acf51c913c4e66d8e164bc115ce1d95a35f8d6c367aa17",
}
MARKERS = {'new_chat_default_model': 'deepseek-v4.1-flash-1017', 'history_label': '历史对话', 'history_english': 'Chat history', 'section_runs': '运行诊断', 'section_users': '用户与员工', 'section_knowledge': '知识库', 'section_models': '模型监控', 'section_oa': 'OA 接口', 'failure_range': '最新 1000 条范围内筛选', 'responsive_navigation': 'sm:grid-cols-6', 'custom_range': '自定义区间', 'date_bounds': '/dashboard/date-bounds', 'failure_preview_bound': '失败运行列表过大，已停止加载以保护页面', 'personal_heatmap': 'activity-heatmap', 'weekly_heatmap': 'data-week-start', 'account_activity': '/console/activity', 'model_api': '/admin/model-monitor', 'oa_monitor': 'oa-policy-flow-monitor', 'web_opt_in': '已开启联网搜索', 'multiline_draft': 'height:72px!important', 'single_material_row': 'max-height:48px', 'sidecar_panel_opaque': 'bg-background flex size-full min-h-0 flex-col', 'sidecar_draft_full_width': 'min-h-14 w-full min-w-0 px-3 py-2.5 sm:min-h-16 sm:py-3', 'sidecar_draft_left_scroll': 'max-h-36 min-h-6! w-full min-w-0 overflow-y-auto p-0! text-left text-sm leading-6!', 'sidecar_composer_opaque': "bg-background rounded-2xl border *:data-[slot='input-group']:rounded-2xl", 'sidecar_footer_opaque': 'bg-background shrink-0 px-3 pt-3 pb-4 sm:px-4', 'sidecar_attachment_button': 'sidecar-add-attachments-button'}
PROBE = "\nconst fs=require('fs'),crypto=require('crypto');\nconst root='/app/frontend', markers=JSON.parse(process.argv[1]);\nconst found=Object.fromEntries(Object.keys(markers).map(k=>[k,[]])), pins={};\nlet total=0;\nfunction read(path){\n if(fs.realpathSync(path)!==path)throw Error('asset_symlink');\n const fd=fs.openSync(path,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);\n try {const s=fs.fstatSync(fd);\n  if(!s.isFile()||s.size>2097152)throw Error('asset_size');\n  total+=s.size;if(total>67108864)throw Error('aggregate_size');\n  return fs.readFileSync(fd);\n } finally {fs.closeSync(fd);}\n}\nfunction pin(path,raw){pins[path]={bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')};}\nconst files=fs.readdirSync(root+'/.next/static/chunks').sort();\nif(files.length>2048)throw Error('file_count');\nfor(const file of files){\n if(!/^[a-zA-Z0-9_.-]+\\.(js|css)$/.test(file))continue;\n const path=root+'/.next/static/chunks/'+file,raw=read(path),text=raw.toString('utf8');\n let used=false;\n for(const [name,marker] of Object.entries(markers))if(text.includes(marker)){found[name].push(path);used=true;}\n if(used)pin(path,raw);\n}\nfor(const [name,rows] of Object.entries(found))if(!rows.length)throw Error('missing_marker:'+name);\nfor(const suffix of ['/.next/BUILD_ID','/.next/routes-manifest.json','/public/images/cnooc-workspace-concept-bg.png']){\n const path=root+suffix;pin(path,read(path));\n}\nif(Object.keys(pins).length>32)throw Error('pin_count');\nconsole.log(JSON.stringify({source_pins:pins,feature_markers:found}));\n"


class Refused(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            require(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value
    def invalid(_):
        raise Refused("NONFINITE_JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def read(path, limit=4 * MIB):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(), "REGULAR_FILE_REQUIRED")
    require(0 <= before.st_size <= limit, "FILE_BOUND")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = path.lstat()
    require(len(raw) == before.st_size and len(raw) <= limit, "FILE_BOUND")
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "FILE_CHANGED")
    return raw


def pin(path, raw):
    return {"path": str(Path(path).resolve()), "bytes": len(raw), "sha256": sha(raw)}


def inventory(rows):
    require(isinstance(rows, list) and len(rows) == 982, "SOURCE_COUNT")
    result = {}
    for row in rows:
        name = row.get("path", "")
        path = PurePosixPath(name)
        require(name.startswith("frontend/") and path.as_posix() == name and
                not path.is_absolute() and not any(part in (".", "..") for part in path.parts) and
                "\\" not in name and name not in result, "SOURCE_PATH")
        require(type(row.get("bytes")) is int and 0 <= row["bytes"] <= 32 * MIB and
                re.fullmatch("[0-9a-f]{64}", row.get("sha256", "")) is not None, "SOURCE_PIN")
        result[name] = row
    return result


def verify_sources(qualified, baseline_source):
    raws = {name: read(qualified / name) for name in BUILD_PINS}
    for name, expected in BUILD_PINS.items():
        require(sha(raws[name]) == expected, "BUILD_RECEIPT_CHANGED_" + name)
    result = parse(raws["RESULT.json"])
    require(result["frontend_slim"] == "LOCAL_PASS" and result["platform"] == "linux/arm64" and
            result["image"]["tag"] == TAG and result["image"]["id"] == MANIFEST and
            result["export"]["exact_qualified_layers"] == "PASS" and
            result["export"]["blob_and_diffid_hashes"] == "PASS" and
            result["production_writes"] == 0, "QUALIFICATION_MISMATCH")
    current = inventory(parse(raws["SOURCE.json"]))
    prior_raw = read(baseline_source)
    require(sha(prior_raw) == BASE_SOURCE_SHA, "BASE_SOURCE_CHANGED")
    prior = inventory(parse(prior_raw))
    require(set(current) == set(prior), "SOURCE_PATH_SET_CHANGED")
    changed = sorted(name for name in current if current[name] != prior[name])
    require(changed == [SIDECAR] and current[SIDECAR]["sha256"] == AFTER_SIDECAR and
            prior[SIDECAR]["sha256"] == BEFORE_SIDECAR, "SOURCE_SCOPE_CHANGED")
    delta = parse(raws["SOURCE-CHANGE.json"])
    require(delta["base_image"] == BASE_MANIFEST and delta["base_source_inventory_sha256"] == BASE_SOURCE_SHA and
            delta["preserved_files"] == 981 and delta["changed"] == [{"path": SIDECAR,
            "before_sha256": BEFORE_SIDECAR, "after_sha256": AFTER_SIDECAR}], "SOURCE_DELTA_CHANGED")
    total = 0
    sidecar = ""
    root = (qualified / "context").resolve()
    for name, item in current.items():
        path = root / name
        require(path.resolve() == path.absolute(), "SOURCE_SYMLINK")
        raw = read(path, 32 * MIB)
        total += len(raw)
        require(total <= 64 * MIB, "SOURCE_AGGREGATE_BOUND")
        require(len(raw) == item["bytes"] and sha(raw) == item["sha256"], "SOURCE_FILE_CHANGED")
        if name == SIDECAR:
            sidecar = raw.decode("utf-8")
    require(total == result["source"]["bytes"] == 47617505, "SOURCE_TOTAL")
    require('<Tooltip content={t.inputBox.addAttachments}>' in sidecar and
            '<DropdownMenuTrigger asChild>' in sidecar and '<ModeHoverGuide mode={mode}>' in sidecar and
            'PromptInputActionMenuTrigger' not in sidecar and 'tooltipContent' not in sidecar,
            "SIDECAR_TOOLTIP_SOURCE_MISMATCH")
    for key, marker in MARKERS.items():
        if key.startswith("sidecar_"):
            require(marker in sidecar, "SIDECAR_LAYOUT_SOURCE_MISMATCH")
    return result, {name: pin(qualified / name, raw) for name, raw in raws.items()}, {
        "base_inventory": pin(baseline_source, prior_raw), "files_verified": len(current),
        "bytes_verified": total, "preserved_files": 981, "changed_files": [current[SIDECAR]],
        "scope": "sealed_build_context_not_whole_image", "tooltip_source_structure_verified": True,
    }


def verify_archive(qualified):
    path = qualified / "frontend.oci.tar"
    before = path.stat()
    require(not path.is_symlink() and before.st_size == ARCHIVE_BYTES, "ARCHIVE_SIZE")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(MIB), b""):
            digest.update(block)
    require(digest.hexdigest() == ARCHIVE_SHA, "ARCHIVE_HASH")
    metadata = {}
    targets = {"blobs/sha256/" + item[7:]: item for item in (MANIFEST, CONFIG)}
    with tarfile.open(path, "r:") as archive:
        count = 0
        for item in archive:
            count += 1
            require(count <= 4096, "ARCHIVE_ENTRY_BOUND")
            if item.name not in targets:
                continue
            require(item.name not in metadata and item.isfile() and 0 < item.size <= MIB, "OCI_METADATA_SHAPE")
            stream = archive.extractfile(item)
            require(stream is not None, "OCI_METADATA_MISSING")
            raw = stream.read(MIB + 1)
            require(len(raw) == item.size and "sha256:" + sha(raw) == targets[item.name], "OCI_METADATA_HASH")
            metadata[item.name] = parse(raw)
    require(set(metadata) == set(targets), "OCI_METADATA_MISSING")
    manifest = metadata["blobs/sha256/" + MANIFEST[7:]]
    config = metadata["blobs/sha256/" + CONFIG[7:]]
    require(manifest.get("config", {}).get("digest") == CONFIG and len(manifest.get("layers", [])) == 11 and
            config.get("architecture") == "arm64" and config.get("os") == "linux" and
            len(config.get("rootfs", {}).get("diff_ids", [])) == 11, "OCI_IDENTITY")
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), "ARCHIVE_CHANGED")
    return {"path": str(path.resolve()), "bytes": ARCHIVE_BYTES, "sha256": ARCHIVE_SHA,
            "manifest_digest": MANIFEST, "config_digest": CONFIG, "layers": 11}


def browser_reference(summary_path, evidence_dir):
    raw = read(summary_path, MIB)
    summary = parse(raw)
    require(summary.get("schema") == "portal-sidechat-final-image-browser.v1" and
            summary.get("image") == MANIFEST and summary.get("tests_passed") == 10 and
            summary.get("tests_failed") == 0 and summary.get("production_acceptance") is False and
            summary.get("provider_requests") == 0, "BROWSER_SUMMARY_MISMATCH")
    last_path = evidence_dir / ".last-run.json"
    last_raw = read(last_path, MIB)
    require(parse(last_raw) == {"status": "passed", "failedTests": []}, "BROWSER_LAST_RUN_MISMATCH")
    screenshots = []
    for path in sorted(evidence_dir.glob("*/sidechat-*.png")):
        raw_image = read(path, 2 * MIB)
        require(raw_image.startswith(b"\x89PNG\r\n\x1a\n"), "SCREENSHOT_FORMAT")
        screenshots.append(pin(path, raw_image))
    require(len(screenshots) == 2, "BROWSER_SCREENSHOT_COUNT")
    return {"qualification_summary": pin(summary_path, raw),
            "last_run": pin(last_path, last_raw), "screenshots": screenshots,
            "scope": "archived_local_summary_and_line_reporter_not_structured_playwright_report",
            "summary_reported_passed": 10, "summary_reported_failed": 0,
            "structured_report_available": False, "tests_rerun_by_this_tool": False,
            "new_behavior_acceptance_claimed": False, "production_acceptance": False}


def probe_image():
    argv = ["docker", "run", "--rm", "--pull=never", "--network=none", "--platform=linux/arm64",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=256m",
            "--pids-limit=64", "--entrypoint=node", MANIFEST, "-e", PROBE,
            json.dumps(MARKERS, ensure_ascii=False)]
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.run(argv, stdout=stdout, stderr=stderr, timeout=90, check=False)
        require(process.returncode == 0, "OFFLINE_NODE_PROBE_FAILED")
        require(0 < stdout.tell() <= 65536 and stderr.tell() <= MIB, "PROBE_OUTPUT_BOUND")
        stdout.seek(0)
        assets = parse(stdout.read(65537))
    pins = assets.get("source_pins", {})
    found = assets.get("feature_markers", {})
    require(set(found) == set(MARKERS) and 0 < len(pins) <= 32, "COMPILED_ASSET_SHAPE")
    for name, rows in found.items():
        require(isinstance(rows, list) and 0 < len(rows) <= 32 and
                len(rows) == len(set(rows)) and all(row in pins for row in rows), "MARKER_PINS")
    for name, row in pins.items():
        require(name.startswith("/app/frontend/") and ".." not in PurePosixPath(name).parts and
                type(row.get("bytes")) is int and 0 < row["bytes"] <= 2 * MIB and
                re.fullmatch("[0-9a-f]{64}", row.get("sha256", "")) is not None, "COMPILED_PIN")
    sidecar_sets = [set(found[key]) for key in MARKERS if key.startswith("sidecar_")]
    require(len(set.intersection(*sidecar_sets)) == 1, "SIDECAR_MARKERS_NOT_COLOCATED")
    return assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--browser-qualification", type=Path, required=True)
    parser.add_argument("--browser-evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "OUTPUT_ALREADY_EXISTS")
    result, build_pins, sources = verify_sources(args.qualified, args.baseline_source)
    archive = verify_archive(args.qualified)
    browser = browser_reference(args.browser_qualification, args.browser_evidence_dir)
    assets = probe_image()
    require(assets["source_pins"]["/app/frontend/.next/BUILD_ID"]["sha256"] ==
            result["runtime"]["layout"]["build_id_sha256"] and
            assets["source_pins"]["/app/frontend/.next/routes-manifest.json"]["sha256"] ==
            result["runtime"]["layout"]["routes_sha256"], "RUNTIME_METADATA_CHANGED")
    assets.update(schema="portal-sidechat-security-frontend-assets.v1", status="COMPILED_MARKERS_PINNED",
                  image=MANIFEST, image_tag=TAG, config_digest=CONFIG,
                  qualification_sha256=build_pins["RESULT.json"]["sha256"],
                  source_inventory_sha256=build_pins["SOURCE.json"]["sha256"],
                  build_input_pins=build_pins, source_verification=sources, archive=archive,
                  browser_evidence=browser, probe_sha256=sha(PROBE.encode("utf-8")),
                  tool_sha256=sha(read(Path(__file__), MIB)), production_access=False,
                  behavior_acceptance_claimed=False, gateway_changes=False,
                  skillhub_backend_auth_acceptance_claimed=False)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(assets, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    output_raw = read(args.output, MIB)
    print(json.dumps({"status": assets["status"], "files": len(assets["source_pins"]),
                      "features": len(assets["feature_markers"]), "source_files": sources["files_verified"],
                      "output": pin(args.output, output_raw), "production_access": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
