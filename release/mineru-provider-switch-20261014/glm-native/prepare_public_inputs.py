"""Local workstation only: collect pinned public inputs, never run image code.

No kubectl/SSH, enterprise endpoint, credentials, container engine or package
installation. Download files remain data. Cargo crates are NOT extracted here.
Each phase can resume verified files; partial/mismatched files fail closed.
"""
import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import tomllib
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
REGISTRY = "https://registry-1.docker.io/v2/library/golang/"
MAX_BLOB = 2 * 1024**3


def require(value, check):
    if not value:
        raise ValueError(check)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()


def emit(**value):
    print(json.dumps(value, sort_keys=True), flush=True)


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == "https", "https_redirect_required")
        nxt = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            nxt.remove_header("Authorization")
        return nxt


def response(url, headers=None):
    require(urllib.parse.urlsplit(url).scheme == "https", "https_required")
    req = urllib.request.Request(url, headers={"User-Agent": "weknora-offline-inputs/1", **(headers or {})})
    proxies = urllib.request.getproxies()
    # urllib does not expand ALL_PROXY, although the workstation's browser does.
    # Use only the operator's existing proxy configuration, never invent one.
    if proxies.get("all"):
        for scheme in ("http", "https"):
            proxies.setdefault(scheme, proxies["all"])
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies), PublicRedirect()).open(req, timeout=45)


def small(url, headers=None, limit=8*1024**2):
    with response(url, headers) as r:
        raw = r.read(limit + 1)
    require(len(raw) <= limit, "metadata_too_large")
    return raw


def safe_directory(root):
    root = Path(os.path.abspath(root))
    for part in (root, *root.parents):
        require(not part.is_symlink(), "symlink_directory")
        if part.exists():
            require(part.is_dir(), "not_directory")
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_once(path, raw):
    safe_directory(path.parent)
    require(not path.is_symlink(), "symlink_file")
    if path.exists():
        require(path.read_bytes() == raw, "existing_generated_file_differs")
    else:
        with path.open("xb") as f:
            f.write(raw)


def json_once(path, value):
    write_once(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def download(url, path, digest, expected_bytes=None, headers=None):
    require(re.fullmatch(r"[0-9a-f]{64}", digest), "bad_sha256")
    safe_directory(path.parent)
    require(not path.is_symlink(), "symlink_download")
    if path.exists():
        require(path.is_file() and path.stat().st_nlink == 1 and sha(path) == digest, "cached_digest_mismatch")
        if expected_bytes is not None:
            require(path.stat().st_size == expected_bytes, "cached_size_mismatch")
        return {"sha256": digest, "bytes": path.stat().st_size}
    # Unique partials are retained on failure, never mistaken for verified files.
    tmp = path.with_name(path.name + ".partial-" + uuid.uuid4().hex)
    h, total = hashlib.sha256(), 0
    started, notice = time.monotonic(), time.monotonic() + 20
    with response(url, headers) as r, tmp.open("xb") as f:
        while True:
            require(time.monotonic() - started < 1800, "public_download_deadline")
            b = getattr(r, "read1", r.read)(256*1024)
            if not b:
                break
            total += len(b)
            require(total <= (expected_bytes if expected_bytes is not None else MAX_BLOB), "download_size_limit")
            h.update(b)
            f.write(b)
            if time.monotonic() >= notice:
                emit(public_download="RUNNING", sha256=digest, bytes=total)
                notice = time.monotonic() + 20
    require(h.hexdigest() == digest, "download_digest_mismatch")
    require(expected_bytes is None or total == expected_bytes, "download_size_mismatch")
    require(not path.exists(), "destination_appeared")
    tmp.rename(path)
    return {"sha256": digest, "bytes": total}


def verify_layers(layout, manifest, config):
    require(config.get("os") == "linux" and config.get("architecture") == "arm64", "builder_platform")
    diffs = config["rootfs"]["diff_ids"]
    require(len(diffs) == len(manifest["layers"]), "diffid_count")
    total = 0
    for i, (layer, diff) in enumerate(zip(manifest["layers"], diffs), 1):
        require(layer["mediaType"] in ("application/vnd.oci.image.layer.v1.tar+gzip", "application/vnd.docker.image.rootfs.diff.tar.gzip"), "unsupported_layer")
        h = hashlib.sha256()
        count = 0
        with gzip.open(layout / "blobs/sha256" / layer["digest"].split(":")[1], "rb") as f:
            for b in iter(lambda: f.read(1024**2), b""):
                count += len(b)
                require(count < 8*1024**3, "uncompressed_layer_bound")
                h.update(b)
        require("sha256:" + h.hexdigest() == diff, "builder_diffid_mismatch")
        total += count
        emit(builder_layer=i, diffid="PASS")
    return total


def builder(root, lock):
    layout = safe_directory(root / "builder-oci")
    spec = lock["builder"]
    def pull_headers():
        # Anonymous public pull bearer is used in memory only, never recorded.
        # A large earlier blob may outlive the token; obtain a fresh public
        # token per queued blob instead of sharing a stale phase-wide token.
        auth = json.loads(small("https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/golang:pull"))
        return {"Authorization": "Bearer " + auth["token"], "Accept": "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"}
    headers = pull_headers()
    digest = spec["manifest_digest"].split(":")[1]
    path = layout / "blobs/sha256" / digest
    download(REGISTRY + "manifests/" + spec["manifest_digest"], path, digest, headers=headers)
    manifest = json.loads(path.read_bytes())
    require(manifest["schemaVersion"] == 2 and manifest["config"]["digest"] == spec["config_digest"], "builder_manifest_identity")
    descriptors = [manifest["config"], *manifest["layers"]]
    def blob(d):
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", d["digest"]), "descriptor_digest")
        require(0 < d["size"] < MAX_BLOB, "descriptor_size")
        download(REGISTRY + "blobs/" + d["digest"], layout / "blobs/sha256" / d["digest"].split(":")[1], d["digest"].split(":")[1], d["size"], pull_headers())
        emit(builder_blob="VERIFIED", bytes=d["size"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(blob, descriptors))
    config = json.loads((layout / "blobs/sha256" / spec["config_digest"].split(":")[1]).read_bytes())
    require("GOLANG_VERSION=1.26.8" in config["config"].get("Env", []), "builder_go_version")
    total = verify_layers(layout, manifest, config)
    json_once(layout / "oci-layout", {"imageLayoutVersion": "1.0.0"})
    json_once(layout / "index.json", {"schemaVersion": 2, "manifests": [{"mediaType": manifest["mediaType"], "digest": spec["manifest_digest"], "size": path.stat().st_size, "platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}}]})
    return {"manifest_digest": spec["manifest_digest"], "config_digest": spec["config_digest"], "layers": len(manifest["layers"]), "uncompressed_bytes": total, "image_executed": False}


def crate_index_path(name):
    require(re.fullmatch(r"[A-Za-z0-9_-]+", name), "crate_name")
    name = name.lower()
    if len(name) <= 2:
        return str(len(name)) + "/" + name
    if len(name) == 3:
        return "3/" + name[0] + "/" + name
    return name[:2] + "/" + name[2:4] + "/" + name


def crates(root, source, lock):
    cargo = source / "third_party/anydoc-go/Cargo.lock"
    raw = cargo.read_bytes()
    packages = tomllib.loads(raw.decode())["package"]
    require(all(p.get("source") in (None, "registry+https://github.com/rust-lang/crates.io-index") for p in packages), "unreviewed_cargo_source")
    packages = [p for p in packages if "source" in p]
    groups = {}
    for p in packages:
        require(re.fullmatch(r"[0-9A-Za-z.+_-]+", p["version"]), "crate_version")
        groups.setdefault(p["name"], []).append(p)
    registry = safe_directory(root / "cargo-local-registry")
    def group(item):
        name, versions = item
        index = crate_index_path(name)
        cached = registry / "index" / index
        require(not cached.is_symlink(), "crate_index_link")
        raw_index = cached.read_bytes() if cached.exists() else small("https://index.crates.io/" + index)
        entries = {x["vers"]: x for x in map(json.loads, raw_index.splitlines())}
        selected = []
        for p in versions:
            e = entries[p["version"]]
            require(e["cksum"] == p["checksum"] and e["name"] == name, "cargo_index_lock_mismatch")
            download("https://static.crates.io/crates/%s/%s-%s.crate" % (name, name, p["version"]), registry / (name + "-" + p["version"] + ".crate"), p["checksum"])
            selected.append(json.dumps(e, sort_keys=True))
        write_once(registry / "index" / index, ("\n".join(selected) + "\n").encode())
    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for _ in pool.map(group, sorted(groups.items())):
            completed += 1
            if completed % 25 == 0:
                emit(cargo_crates="RUNNING", names_completed=completed, names_total=len(groups))
    anydoc = lock["anydoc"]
    download(anydoc["url"], root / "anydoc-0.1.9.crate", anydoc["sha256"], anydoc["bytes"])
    require(cargo.read_bytes() == raw, "cargo_lock_changed")
    return {"registry_packages": len(packages), "cargo_lock_sha256": hashlib.sha256(raw).hexdigest(), "anydoc_sha256": anydoc["sha256"], "archives_extracted": False}


def rust(root, lock):
    result = []
    for p in lock["rust_components"]:
        result.append({"component": p["component"], **download(p["url"], root / "rust" / p["url"].rsplit("/", 1)[1], p["sha256"])})
        emit(rust_component=p["component"], sha256="PASS", executed=False)
    return result


def go_offline(root, source, go, cache):
    """Prove the file proxy closes over the real source with an EMPTY cache."""
    require((root / "go-PASS.json").is_file(), "online_closure_required")
    # Lazy module graph loading needs go.mod files for some packages outside
    # the linked package closure. Those cached metadata files were obtained by
    # the normal Go downloader; the offline invocation rechecks go.sum.
    cache_root = cache / "pkg/mod/cache/download"
    proxy = safe_directory(root / "go-proxy")
    for src in cache_root.rglob("*"):
        if src.is_file() and src.suffix in (".mod", ".info") and src.parent.name == "@v":
            require(not src.is_symlink(), "go_metadata_link")
            write_once(proxy / src.relative_to(cache_root), src.read_bytes())
    work = root / "go-resolution"
    checks = safe_directory(root / ("offline-go-check-" + uuid.uuid4().hex))
    env = dict(os.environ, GOTOOLCHAIN="local", GOFLAGS="-mod=readonly", GOOS="linux", GOARCH="arm64",
               CGO_ENABLED="1", GOPATH=str(checks), GOMODCACHE=str(checks / "pkg/mod"),
               GOPROXY=proxy.as_uri(), GOSUMDB="off", GOPRIVATE="", GONOPROXY="", GONOSUMDB="",
               GOINSECURE="", GOWORK="off")
    receipt = json.loads((root / "go-PASS.json").read_bytes())
    with (checks / "packages.jsonl").open("xb") as out, (checks / "stderr.log").open("xb") as err:
        proc = subprocess.Popen([str(go), "list", "-deps", "-test", "-tags", "anydoc", "-json"] + receipt["targets"],
                                cwd=work, env=env, stdout=out, stderr=err)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                require(time.monotonic()-start < 900, "offline_go_deadline")
                emit(go_offline="RUNNING", elapsed_seconds=int(time.monotonic()-start))
                time.sleep(10)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
    require(proc.returncode == 0, "offline_go_closure_failed_see_local_log")
    for name, field in (("go.mod", "go_mod_sha256"), ("go.sum", "go_sum_sha256")):
        require(sha(work / name) == sha(source / name) == receipt[field], "offline_lock_changed")
    return {"empty_cache_dependency_resolution": "PASS", "proxy": "LOCAL_FILES_ONLY_NO_FALLBACK",
            "platform": "linux/arm64", "cgo_enabled": True, "anydoc_tag": True,
            "compile_performed": False, "targets": receipt["targets"]}


def go_modules(root, source, go, cache):
    from assemble_offline_context import source_map
    work = safe_directory(root / "go-resolution")
    locks = {p: (source / p).read_bytes() for p in ("go.mod", "go.sum", "third_party/anydoc-go/go.mod", "third_party/anydoc-go/go.sum") if (source / p).exists()}
    for p, raw in locks.items():
        write_once(work / p, raw)
    for name, path in source_map(source).items():
        write_once(work / name, path.read_bytes())
    env = dict(os.environ, GOTOOLCHAIN="local", GOFLAGS="-mod=readonly", GOOS="linux", GOARCH="arm64", CGO_ENABLED="1", GOPATH=str(cache), GOPROXY="https://goproxy.cn", GOSUMDB="sum.golang.org", GOWORK="off", GOPRIVATE="", GONOPROXY="", GONOSUMDB="", GOINSECURE="", GOMODCACHE=str(cache / "pkg/mod"))
    proxies = urllib.request.getproxies()
    for protocol in ("http", "https"):
        if proxies.get(protocol) or proxies.get("all"):
            env[protocol.upper() + "_PROXY"] = proxies.get(protocol, proxies.get("all"))
    def command(label, argv):
        log = work / (label + "-" + uuid.uuid4().hex + ".jsonl")
        err = log.with_suffix(".stderr")
        start = time.monotonic()
        with log.open("xb") as output, err.open("xb") as errors:
            proc = subprocess.Popen([str(go)] + argv, cwd=work, env=env, stdout=output, stderr=errors)
            try:
                while proc.poll() is None:
                    require(time.monotonic()-start < 900, "go_download_deadline")
                    emit(go_modules=label, elapsed_seconds=int(time.monotonic()-start))
                    time.sleep(10)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=15)
        require(proc.returncode == 0, "go_" + label + "_failed_see_local_log")
        text = log.read_text(encoding="utf-8")
        decoder, pos, rows = json.JSONDecoder(), 0, []
        whitespace = re.compile(r"\s*")
        while True:
            pos = whitespace.match(text, pos).end()
            if pos == len(text):
                break
            row, pos = decoder.raw_decode(text, pos)
            require(not row.get("Error") and not row.get("DepsErrors"), "go_module_error")
            rows.append(row)
        return rows
    # Collect the actual Linux/ARM64 application+test closure, not every test
    # dependency of every upstream module on every supported platform.
    targets = ["./cmd/server", "./cmd/glm-vlm-probe", "./cmd/anydoc-probe",
               "./internal/models/vlm", "./internal/infrastructure/docparser/...", "./internal/utils"]
    packages = command("linux-dependency-list", ["list", "-deps", "-test", "-tags", "anydoc", "-json"] + targets)
    modules = set()
    for package in packages:
        module = package.get("Module", {})
        module = module.get("Replace", module)
        if module.get("Version") and not module.get("Main"):
            modules.add(module["Path"] + "@" + module["Version"])
    rows = []
    names = sorted(modules)
    require(len(names) > 0, "empty_native_module_closure")
    for offset in range(0, len(names), 30):
        rows.extend(command("download-batch", ["mod", "download", "-json"] + names[offset:offset+30]))
    for p, raw in locks.items():
        require((source / p).read_bytes() == raw and (work / p).read_bytes() == raw, "go_lock_changed")
    proxy = safe_directory(root / "go-proxy")
    cache_root = (cache / "pkg/mod/cache/download").resolve()
    for row in rows:
        for key in ("Info", "GoMod", "Zip"):
            require(key in row, "module_artifact_missing")
            src = Path(row[key]).resolve()
            rel = src.relative_to(cache_root)
            dst = proxy / rel
            safe_directory(dst.parent)
            if dst.exists():
                require(sha(dst) == sha(src), "go_proxy_cache_mismatch")
            else:
                with src.open("rb") as inp, dst.open("xb") as out:
                    shutil.copyfileobj(inp, out, 1024**2)
    return {"modules": len(rows), "go_mod_sha256": hashlib.sha256(locks["go.mod"]).hexdigest(), "go_sum_sha256": hashlib.sha256(locks["go.sum"]).hexdigest(), "compile_performed": False, "checksum_database": "sum.golang.org", "scope": "LINUX_ARM64_APP_AND_QUALIFICATION_TEST_CLOSURE", "targets": targets}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=("builder", "rust", "crates", "go", "go-offline", "sqlite"), required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--go", type=Path)
    p.add_argument("--go-cache", type=Path)
    args = p.parse_args()
    root = safe_directory(args.output)
    require(shutil.disk_usage(root).free > 20*1024**3, "local_disk_reserve")
    lock = json.loads((HERE / "BUILD-INPUT-LOCK.json").read_bytes())
    emit(phase=args.phase, state="START", production_contact=False)
    if args.phase in ("go", "go-offline"):
        require(args.go and args.go_cache, "go_paths_required")
        result = (go_modules if args.phase == "go" else go_offline)(root, args.source, args.go, args.go_cache)
    elif args.phase == "sqlite":
        from package_sqlite_fix import acquire
        result = acquire(root / "debian")
    elif args.phase == "crates":
        result = crates(root, args.source, lock)
    else:
        result = globals()[args.phase](root, lock)
    json_once(root / (args.phase + "-PASS.json"), result)
    emit(phase=args.phase, state="PASS", result=result, ready_for_build=False, ready_for_rollout=False)


if __name__ == "__main__":
    main()
