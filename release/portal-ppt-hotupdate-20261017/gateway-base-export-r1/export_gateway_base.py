#!/usr/bin/env python3
"""Export one pinned local Gateway base; Python 3.7+, no service mutation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

NODE = "ecs-zdz-pro-0020"
SOCKET = "/run/containerd/containerd.sock"
TAG = "deerflow.local/deer-flow-gateway:20261017-background-stream-953eb97e4aaf"
MANIFEST = "sha256:ef500864eb402d0b9a76556764dc0e2a9d3fe417f6fef89fbf4e3816ee0c1c84"
CONFIG = "sha256:dff2b9d7323329733f14133b384030d6efeda948a86f082f055f1573ced224fd"
LAYERS = 29
MEDIA = "application/vnd.oci.image.manifest.v1+json"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_METADATA = 4 * 1024 * 1024
TOTAL_SECONDS = 1200
EXPORT_SECONDS = 900
MAX_CONTENT = 20 * 1024 ** 3


class Failure(Exception):
    def __init__(self, code, returncode=None):
        self.code, self.returncode = code, returncode


def require(ok, code):
    if not ok:
        raise Failure(code)


def check_time(deadline):
    require(time.monotonic() < deadline, "TOTAL_TIMEOUT")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except Failure:
        raise
    except Exception:
        raise Failure("INVALID_JSON")


def bounded(argv, timeout, limit=MAX_METADATA, watched=None, maximum=None):
    """Drain both pipes continuously; never expose stderr or failed stdout."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False,
                                   start_new_session=(os.name == "posix"))
    except Exception:
        raise Failure("COMMAND_SPAWN_FAILED")
    chunks, counts, overflow = [], [0, 0], threading.Event()

    def drain(pipe, index):
        try:
            while True:
                data = pipe.read(8192)
                if not data:
                    break
                counts[index] += len(data)
                if counts[index] > (limit if index == 0 else 65536):
                    overflow.set()
                if index == 0 and counts[0] <= limit:
                    chunks.append(data)
        except Exception:
            overflow.set()
        finally:
            pipe.close()

    readers = [threading.Thread(target=drain, args=(p, n), daemon=True)
               for n, p in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    stop, code = time.monotonic() + timeout, None
    while process.poll() is None:
        if overflow.is_set():
            code = "COMMAND_OUTPUT_LIMIT"
        elif time.monotonic() >= stop:
            code = "COMMAND_TIMEOUT"
        elif watched is not None:
            try:
                info = watched.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                    code = "EXPORT_SIZE_OR_TYPE_LIMIT"
            except FileNotFoundError:
                code = "EXPORT_FILE_DISAPPEARED"
        if code:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except Exception:
                pass
            break
        time.sleep(0.05)
    try:
        process.wait(timeout=2)
    except Exception:
        code = code or "COMMAND_NOT_REAPED"
    for reader in readers:
        reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        code = code or "COMMAND_PIPE_NOT_CLOSED"
    if overflow.is_set():
        code = code or "COMMAND_OUTPUT_LIMIT"
    if code or process.returncode:
        raise Failure(code or "COMMAND_NONZERO_EXIT", process.returncode)
    return b"".join(chunks)


class Runtime:
    def __init__(self, executable, deadline):
        self.command = [executable, "--address", SOCKET, "--namespace", "k8s.io"]
        self.deadline = deadline

    def run(self, args, timeout=30, **kwargs):
        check_time(self.deadline)
        return bounded(self.command + args, min(timeout, self.deadline - time.monotonic()), **kwargs)

    def tag(self):
        raw = self.run(["images", "ls"])
        try:
            rows = [line.split() for line in raw.decode("utf-8").splitlines()]
        except Exception:
            raise Failure("IMAGE_LIST_ENCODING")
        rows = [row for row in rows if row and row[0] == TAG]
        require(len(rows) == 1 and len(rows[0]) >= 3, "EXACT_TAG_NOT_UNIQUE")
        require(rows[0][1] == MEDIA and rows[0][2] == MANIFEST, "TAG_TARGET_CHANGED")
        return MANIFEST

    def content(self, digest):
        require(digest in (MANIFEST, CONFIG), "CONTENT_NOT_ALLOWLISTED")
        raw = self.run(["content", "get", digest])
        require(sha(raw) == digest[7:], "LOCAL_CONTENT_HASH_MISMATCH")
        return raw

    def export(self, path, maximum):
        self.run(["images", "export", "--platform", "linux/arm64", str(path), TAG],
                 timeout=EXPORT_SECONDS, limit=65536, watched=path, maximum=maximum)


def blob_path(digest):
    return "blobs/sha256/" + digest[7:]


def descriptor(row):
    require(isinstance(row, dict), "INVALID_DESCRIPTOR")
    digest, size = row.get("digest"), row.get("size")
    require(isinstance(digest, str) and DIGEST.fullmatch(digest), "INVALID_DESCRIPTOR_DIGEST")
    require(isinstance(size, int) and not isinstance(size, bool) and 0 <= size <= MAX_CONTENT, "INVALID_DESCRIPTOR_SIZE")
    require("urls" not in row and "data" not in row, "EXTERNAL_OR_INLINE_DESCRIPTOR")
    return digest, size


def expected_blobs(manifest_raw, config_raw, manifest_pin=MANIFEST, config_pin=CONFIG, layers_count=LAYERS):
    require(sha(manifest_raw) == manifest_pin[7:] and sha(config_raw) == config_pin[7:], "PINNED_CONTENT_HASH_MISMATCH")
    manifest, config = parse_json(manifest_raw), parse_json(config_raw)
    require(isinstance(manifest, dict) and manifest.get("schemaVersion") == 2 and manifest.get("mediaType") == MEDIA, "INVALID_MANIFEST")
    config_descriptor = manifest.get("config")
    require(descriptor(config_descriptor) == (config_pin, len(config_raw)), "CONFIG_DESCRIPTOR_MISMATCH")
    require(config_descriptor.get("mediaType") in ("application/vnd.oci.image.config.v1+json", "application/vnd.docker.container.image.v1+json"), "CONFIG_MEDIA_TYPE")
    require(isinstance(config, dict) and config.get("os") == "linux" and config.get("architecture") == "arm64", "CONFIG_PLATFORM_MISMATCH")
    layers = manifest.get("layers")
    require(isinstance(layers, list) and len(layers) == layers_count, "LAYER_COUNT_MISMATCH")
    rootfs = config.get("rootfs", {})
    require(isinstance(rootfs, dict), "CONFIG_ROOTFS_SCHEMA")
    diff_ids = rootfs.get("diff_ids")
    require(rootfs.get("type") == "layers" and isinstance(diff_ids, list) and len(diff_ids) == layers_count
            and all(isinstance(d, str) and DIGEST.fullmatch(d) for d in diff_ids), "CONFIG_ROOTFS_SCHEMA")
    blobs = {blob_path(manifest_pin): (len(manifest_raw), manifest_pin[7:]), blob_path(config_pin): (len(config_raw), config_pin[7:])}
    allowed = {"application/vnd.oci.image.layer.v1.tar", "application/vnd.oci.image.layer.v1.tar+gzip",
               "application/vnd.oci.image.layer.v1.tar+zstd", "application/vnd.docker.image.rootfs.diff.tar.gzip"}
    for layer in layers:
        digest, size = descriptor(layer)
        require(layer.get("mediaType") in allowed, "LAYER_MEDIA_TYPE")
        name = blob_path(digest)
        require(name not in blobs or blobs[name] == (size, digest[7:]), "CONFLICTING_BLOB_DESCRIPTOR")
        blobs[name] = (size, digest[7:])
    require(sum(v[0] for v in blobs.values()) <= MAX_CONTENT, "TOTAL_CONTENT_LIMIT")
    return blobs, [blob_path(layer["digest"]) for layer in layers]


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def validate_archive(path, blobs, layer_paths, deadline, manifest_pin=MANIFEST, config_pin=CONFIG, tag=TAG):
    """Strict streaming tar framing; no extraction, links, PAX or hidden tail."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "UNSAFE_ARCHIVE_FILE")
        maximum = sum(v[0] for v in blobs.values()) + 8 * 1024 * 1024
        require(0 < before.st_size <= maximum, "ARCHIVE_SIZE_LIMIT")
        metadata, seen, checked = {}, set(), []
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while True:
                check_time(deadline)
                header = stream.read(512)
                require(len(header) == 512, "TRUNCATED_TAR_HEADER")
                if header == b"\0" * 512:
                    require(stream.read(512) == b"\0" * 512, "INVALID_TAR_END")
                    while True:
                        check_time(deadline)
                        rest = stream.read(1024 * 1024)
                        if not rest:
                            break
                        require(not rest.strip(b"\0"), "NONZERO_TAR_TRAILER")
                    break
                try:
                    member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
                except Exception:
                    raise Failure("INVALID_TAR_HEADER")
                name = member.name
                require(name and not name.startswith("/") and "\\" not in name and all(s not in ("", ".", "..") for s in name.rstrip("/").split("/")), "UNSAFE_ARCHIVE_PATH")
                require(name.rstrip("/") not in seen, "DUPLICATE_ARCHIVE_MEMBER")
                seen.add(name.rstrip("/"))
                require(len(seen) <= len(blobs) + 7, "TOO_MANY_ARCHIVE_MEMBERS")
                require(not member.linkname and not member.sparse, "LINK_OR_SPARSE_MEMBER")
                if member.isdir():
                    require(name.rstrip("/") in ("blobs", "blobs/sha256") and member.size == 0, "UNEXPECTED_ARCHIVE_DIRECTORY")
                    continue
                require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE), "NONREGULAR_ARCHIVE_MEMBER")
                require(name in blobs or name in ("index.json", "oci-layout", "manifest.json"), "EXTRA_ARCHIVE_MEMBER")
                if name in blobs:
                    require(member.size == blobs[name][0], "BLOB_SIZE_MISMATCH")
                else:
                    require(0 < member.size <= 65536, "ARCHIVE_METADATA_SIZE")
                digest, remaining, pieces = hashlib.sha256(), member.size, []
                while remaining:
                    check_time(deadline)
                    chunk = stream.read(min(1024 * 1024, remaining))
                    require(bool(chunk), "TRUNCATED_ARCHIVE_MEMBER")
                    remaining -= len(chunk)
                    digest.update(chunk)
                    if name not in blobs:
                        pieces.append(chunk)
                padding = (-member.size) % 512
                require(stream.read(padding) == b"\0" * padding, "INVALID_TAR_PADDING")
                if name in blobs:
                    require(digest.hexdigest() == blobs[name][1], "BLOB_HASH_MISMATCH")
                    checked.append(name)
                else:
                    metadata[name] = parse_json(b"".join(pieces))
            require(set(checked) == set(blobs), "MISSING_BLOBS")
            require(metadata.get("oci-layout") == {"imageLayoutVersion": "1.0.0"}, "INVALID_OCI_LAYOUT")
            index = metadata.get("index.json")
            require(isinstance(index, dict) and index.get("schemaVersion") == 2, "INVALID_OCI_INDEX")
            entries = index.get("manifests")
            require(isinstance(entries, list) and len(entries) == 1, "INDEX_DESCRIPTOR_COUNT")
            entry = entries[0]
            require(descriptor(entry) == (manifest_pin, blobs[blob_path(manifest_pin)][0]) and entry.get("mediaType") == MEDIA, "INDEX_MANIFEST_MISMATCH")
            platform = entry.get("platform")
            require(platform is None or (isinstance(platform, dict) and platform.get("os") == "linux" and platform.get("architecture") == "arm64"), "INDEX_PLATFORM_MISMATCH")
            annotations = entry.get("annotations", {})
            require(isinstance(annotations, dict), "INDEX_ANNOTATIONS_SCHEMA")
            require(annotations.get("io.containerd.image.name", tag) == tag and annotations.get("org.opencontainers.image.ref.name", tag) in (tag, tag.rsplit(":", 1)[-1]), "INDEX_TAG_MISMATCH")
            if "manifest.json" in metadata:
                docker = metadata["manifest.json"]
                require(isinstance(docker, list) and len(docker) == 1 and isinstance(docker[0], dict), "DOCKER_MANIFEST_SCHEMA")
                require(docker[0] == {"Config": blob_path(config_pin), "RepoTags": [tag], "Layers": layer_paths}, "DOCKER_MANIFEST_MISMATCH")
            require(identity(before) == identity(os.fstat(fd)), "ARCHIVE_CHANGED_DURING_VERIFY")
            stream.seek(0)
            total_digest = hashlib.sha256()
            while True:
                check_time(deadline)
                chunk = stream.read(4 * 1024 * 1024)
                if not chunk:
                    break
                total_digest.update(chunk)
            require(identity(before) == identity(os.fstat(fd)), "ARCHIVE_CHANGED_DURING_HASH")
        return {"sha256": total_digest.hexdigest(), "bytes": before.st_size,
                "verified_unique_blobs": len(checked), "layer_descriptors": len(layer_paths),
                "optional_docker_manifest": "manifest.json" in metadata,
                "extracted": False, "diff_ids_decompressed_verified": False}
    finally:
        os.close(fd)


def create_stage():
    require(os.name == "posix" and os.geteuid() == 0, "ROOT_LINUX_REQUIRED")
    require(socket.gethostname().split(".", 1)[0] == NODE, "WRONG_WORKER")
    require(stat.S_ISSOCK(os.stat(SOCKET).st_mode), "CONTAINERD_SOCKET_REQUIRED")
    root = Path("/var/lib")
    require(root.resolve() == root, "STAGE_PARENT_SYMLINK")
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "UNSAFE_STAGE_PARENT")
    old = os.umask(0o077)
    try:
        stage = Path(tempfile.mkdtemp(prefix="portal-gateway-base-20261017-", dir=str(root)))
    finally:
        os.umask(old)
    os.chmod(str(stage), 0o700)
    return stage


def write_report(stage, report):
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(str(stage / "REPORT.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(str(stage), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {"report_path": str(stage / "REPORT.json"), "report_sha256": sha(payload), "report_bytes": len(payload)}


def handoff(stage):
    """Last filesystem operation phase. Parent remains root-owned /var/lib."""
    import pwd
    owner = pwd.getpwnam("cnooc")
    require(owner.pw_uid > 0 and owner.pw_gid > 0, "INVALID_CNOOC_ACCOUNT")
    directory_fd = os.open(str(stage), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fds = []
    try:
        info = os.fstat(directory_fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, "UNSAFE_HANDOFF_DIRECTORY")
        for name in ("gateway.base.oci.tar", "REPORT.json"):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            fds.append(fd)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, "UNSAFE_HANDOFF_FILE")
        for fd in fds:
            os.fchown(fd, owner.pw_uid, owner.pw_gid)
            os.fchmod(fd, 0o600)
        os.fchmod(directory_fd, 0o700)
        os.fchown(directory_fd, owner.pw_uid, owner.pw_gid)
    finally:
        for fd in fds:
            os.close(fd)
        os.close(directory_fd)


def export_workflow(stage, runtime, report, deadline):
    def step(name):
        report["steps"].append({"operation": name, "status": "STARTED"})
        print("Gateway base export: " + name, file=sys.stderr, flush=True)
    def passed():
        report["steps"][-1]["status"] = "COMPLETE"
    step("verify_tag_before")
    report["tag_before"] = runtime.tag()
    passed()
    step("verify_manifest_config")
    manifest_raw, config_raw = runtime.content(MANIFEST), runtime.content(CONFIG)
    blobs, layer_paths = expected_blobs(manifest_raw, config_raw)
    del config_raw, manifest_raw
    content_bytes = sum(v[0] for v in blobs.values())
    required = 2 * content_bytes + 512 * 1024 * 1024
    free = shutil.disk_usage(str(stage)).free
    report["disk"] = {"content_bytes": content_bytes, "required_free_bytes": required, "observed_free_bytes": free}
    require(free >= required, "INSUFFICIENT_DISK_SPACE")
    passed()
    step("export_fixed_local_image")
    partial = stage / "gateway.base.oci.tar.partial"
    fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    runtime.export(partial, content_bytes + 8 * 1024 * 1024)
    info = partial.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, "UNSAFE_EXPORTED_FILE")
    passed()
    step("verify_archive_and_hash")
    report["archive"] = validate_archive(partial, blobs, layer_paths, deadline)
    passed()
    step("verify_tag_after")
    report["tag_after"] = runtime.tag()
    require(report["tag_before"] == report["tag_after"] == MANIFEST, "TAG_DRIFT")
    passed()
    final = stage / "gateway.base.oci.tar"
    os.link(str(partial), str(final))
    partial.unlink()
    fd = os.open(str(final), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    report["archive"]["path"] = str(final)
    report["collection"] = "VERIFIED_PINNED_BASE_ARCHIVE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true", required=True)
    parser.parse_args()
    deadline = time.monotonic() + TOTAL_SECONDS
    stage = None
    report = {"schema": "portal-gateway-base-export.v1", "collection": "INCOMPLETE", "worker": NODE,
              "image": TAG, "manifest_digest": MANIFEST, "config_digest": CONFIG, "layer_descriptors": LAYERS,
              "platform": "linux/arm64", "steps": [], "production_service_mutation": False,
              "local_private_files_written": False, "ready_for_build": False, "ready_for_rollout": False,
              "source_preconditions": "NOT_COLLECTED", "image_env": "NOT_EXPORTED_OUTSIDE_PRIVATE_OCI",
              "handoff": "RESULT_ONLY_ON_STDOUT_AFTER_ALL_ROOT_WRITES", "errors": []}
    try:
        stage = create_stage()
        report["local_private_files_written"] = True
        executable = shutil.which("ctr")
        require(bool(executable), "CTR_NOT_FOUND")
        report["directory"] = str(stage)
        export_workflow(stage, Runtime(executable, deadline), report, deadline)
    except Exception as exc:
        code = exc.code if isinstance(exc, Failure) else "UNEXPECTED_FAILURE_RAW_DETAILS_WITHHELD"
        failure = {"code": code, "returncode": exc.returncode if isinstance(exc, Failure) else None}
        report["errors"].append(failure)
        if report["steps"] and report["steps"][-1]["status"] == "STARTED":
            report["steps"][-1].update(status="FAILED", **failure)
    if stage is not None:
        try:
            report.update(write_report(stage, report))
        except Exception:
            report["errors"].append({"code": "REPORT_WRITE_FAILED", "returncode": None})
    if report["collection"] == "VERIFIED_PINNED_BASE_ARCHIVE" and not report["errors"]:
        try:
            handoff(stage)
            report["handoff_complete"] = True
            report["directory_owner"] = "cnooc"
        except Exception:
            report["handoff_complete"] = False
            report["errors"].append({"code": "HANDOFF_FAILED_NO_FURTHER_ROOT_WRITES", "returncode": None})
    # Never write again after handoff begins, including on a partial failure.
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["collection"] == "VERIFIED_PINNED_BASE_ARCHIVE" and not report["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
