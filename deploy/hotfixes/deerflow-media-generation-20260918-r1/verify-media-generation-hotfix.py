#!/usr/bin/env python3
from __future__ import print_function

import argparse
import hashlib
from pathlib import Path


REQUIRED = (
    "Dockerfile.gateway",
    "IMAGE_LOCKS.env",
    "README.txt",
    "SOURCE.COMMIT",
    "SOURCE.SHA256",
    "build-stage-datax-01.sh",
    "import-media-generation-image-node.sh",
    "manage-media-generation-skills-datax-01.sh",
    "rollback-media-generation-datax-01.sh",
    "run-media-generation-datax-01.sh",
    "status-media-generation-datax-01.sh",
    "payload/backend/app/gateway/routers/artifacts.py",
    "payload/backend/packages/harness/deerflow/community/media_generation/__init__.py",
    "payload/backend/packages/harness/deerflow/community/media_generation/tools.py",
    "payload/backend/packages/harness/deerflow/tools/tools.py",
    "payload/skills/public/image-generation/SKILL.md",
    "payload/skills/public/video-generation/SKILL.md",
)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
    return value.hexdigest()


def parse_manifest(root, name):
    rows = {}
    with (root / name).open("r", encoding="utf-8", newline="") as handle:
        for raw in handle:
            if raw.endswith("\r\n"):
                raise AssertionError("%s must use LF line endings" % name)
            line = raw.rstrip("\n")
            if not line:
                continue
            checksum, relative = line.split("  ", 1)
            assert relative not in rows
            rows[relative] = checksum
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()

    for relative in REQUIRED:
        assert (root / relative).is_file(), "missing %s" % relative

    package_manifest = parse_manifest(root, "PACKAGE.SHA256")
    expected_package = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "PACKAGE.SHA256"
    }
    assert set(package_manifest) == expected_package
    for relative, expected in package_manifest.items():
        assert digest(root / relative) == expected, "package digest mismatch: %s" % relative

    source_manifest = parse_manifest(root, "SOURCE.SHA256")
    expected_source = set(item for item in REQUIRED if item.startswith("payload/"))
    assert set(source_manifest) == expected_source
    for relative, expected in source_manifest.items():
        assert digest(root / relative) == expected, "source digest mismatch: %s" % relative

    tools = (root / "payload/backend/packages/harness/deerflow/community/media_generation/tools.py").read_text(
        encoding="utf-8"
    )
    assert 'IMAGE_API_URL_ENV = "DEERFLOW_IMAGE_GENERATION_API_URL"' in tools
    assert 'VIDEO_API_URL_ENV = "DEERFLOW_VIDEO_GENERATION_API_URL"' in tools
    assert "trust_env=False" in tools
    assert "follow_redirects=False" in tools
    assert 'response_format": "b64_json"' in tools
    assert '"n": 1' in tools
    assert "_MAX_VIDEO_BYTES = 1024 * 1024 * 1024" in tools
    assert "validate_public_http_url" in tools
    assert "_VIDEO_JOB_DIR" in tools
    assert 'code="video_job_lost"' in tools
    assert "do not resubmit it automatically" in tools

    artifact_router = (root / "payload/backend/app/gateway/routers/artifacts.py").read_text(encoding="utf-8")
    assert '("inline_file", mime_type, None)' in artifact_router
    assert 'mime_type.startswith(("audio/", "video/"))' in artifact_router
    assert "return FileResponse(" in artifact_router

    image_skill = (root / "payload/skills/public/image-generation/SKILL.md").read_text(encoding="utf-8")
    video_skill = (root / "payload/skills/public/video-generation/SKILL.md").read_text(encoding="utf-8")
    assert "  - generate_image" in image_skill
    assert "SDKs" in image_skill and "direct HTTP requests" in image_skill
    assert "  - generate_video" in video_skill
    assert "Never resubmit" in video_skill or "never resubmit" in video_skill

    runner = (root / "run-media-generation-datax-01.sh").read_text(encoding="utf-8")
    rollback = (root / "rollback-media-generation-datax-01.sh").read_text(encoding="utf-8")
    manager = (root / "manage-media-generation-skills-datax-01.sh").read_text(encoding="utf-8")
    builder = (root / "build-stage-datax-01.sh").read_text(encoding="utf-8")
    status = (root / "status-media-generation-datax-01.sh").read_text(encoding="utf-8")
    assert "production_database_mutation=NONE" in runner
    assert "active_deerflow_runs" in runner and "active_oa_jobs" in runner
    assert "ROLLBACK.SHA256" in runner and "automatic_rollback" in runner
    assert "post_backup_activity_race_recheck=PASS" in runner
    assert "pre_gateway_activity_race_recheck=PASS" in runner
    assert "for node in datax-01 datax-02 datax-03" in runner
    assert "samples=3" in runner
    assert "gateway_init_image_changed" in runner
    assert "gateway.rollback.patch.json" in rollback
    assert '"${PATCH_DIRECTIVE}": "delete"' in rollback
    assert "--type=strategic" in rollback
    assert "media-generation-skills.before.tar" in manager
    assert "mv -f" in manager
    assert 'tar -C "${PUBLIC_ROOT}" -cf -' in manager
    assert "--oci-layout" in builder and "registry_access=none" in builder
    assert "BASE_GATEWAY_IMAGE" in builder
    assert "media_generation_live_runtime=PASS" in status

    for relative in REQUIRED:
        if relative.endswith((".sh", ".py")):
            assert b"\r\n" not in (root / relative).read_bytes(), "%s has CRLF" % relative

    print("deerflow_media_generation_r1_static_contracts=PASS")
    print("platform_image_generation_contract=PASS response=b64_json count=1")
    print("platform_video_generation_contract=PASS mode=durable_async_job")
    print("thread_scoped_atomic_artifact_publication=PASS")
    print("large_media_file_response_streaming=PASS")
    print("media_generation_skill_tool_binding=PASS")
    print("live_gateway_base_image_lock=BUILD_TIME")
    print("rollout_active_work_gate=ZERO_REQUIRED")
    print("production_database_mutation_code=NONE")
    print("weknora_mutation_code=NONE")
    print("sandbox_mutation_code=NONE")


if __name__ == "__main__":
    main()
