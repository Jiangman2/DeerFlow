"""Prepare/build/qualify a slim frontend locally; NEVER deploy or push an image.

Run on a dedicated build host, not a production Kubernetes node. Standard library only.
Every invocation owns a fresh evidence directory; builder cache is deliberately retained.
"""
import argparse
from contextlib import nullcontext
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import uuid


class CheckFailure(RuntimeError):
    pass


def require(ok, check):
    if not ok:
        raise CheckFailure(check)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(file, value):
    with file.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def selected(name):
    parts = Path(name).parts
    if not parts or parts[0] != "frontend":
        return False
    if any(x in {"node_modules", ".next", ".git", ".pnpm-store", ".turbo", "coverage",
                 "test-results", "playwright-report", "blob-report", "generated"} for x in parts):
        return False
    if any(x.startswith(".env") for x in parts):
        return False
    return not name.endswith((".log", ".pem", ".key", ".p12", ".pfx", ".tsbuildinfo",
                              ".tar", ".tar.gz", ".zip", "/AGENTS.md", "/CLAUDE.md"))


def source_inventory(root):
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--cached",
                                   "--others", "--exclude-standard", "--", "frontend"], timeout=30)
    names = sorted(set(n.decode("utf-8") for n in raw.split(b"\0") if n))
    rows = []
    for name in names:
        if not selected(name):
            continue
        file = root / name
        require(not file.is_symlink() and file.resolve().is_relative_to(root), "source_link_or_escape")
        s = file.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_size < 256 * 1024**2, "source_file_invalid")
        rows.append({"path": name, "bytes": s.st_size, "sha256": sha(file.read_bytes())})
    required = {"frontend/" + n for n in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml",
                ".npmrc", "next.config.js", "Dockerfile.slim", "Dockerfile.slim.dockerignore",
                "scripts/verify-standalone.mjs")}
    require(required.issubset({r["path"] for r in rows}), "missing_build_inputs")
    # Do not bake npm credentials or operator registry settings into any layer.
    npmrc = (root / "frontend/.npmrc").read_text(encoding="utf-8")
    require(all(not line.strip() or line.startswith("#") or
                re.fullmatch(r"public-hoist-pattern\[\]=[\w*.-]+", line.strip())
                for line in npmrc.splitlines()), "npmrc_requires_private_registry_review")
    return rows


def prepare(root, out):
    before = source_inventory(root)
    context = out / "context"
    context.mkdir()
    for row in before:
        target = context / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (root / row["path"]).read_bytes()
        require(sha(raw) == row["sha256"], "source_changed_during_copy")
        with target.open("xb") as f:
            f.write(raw)
    require(before == source_inventory(root), "source_changed_during_snapshot")
    write_json(out / "SOURCE.json", before)
    return {"files": len(before), "bytes": sum(r["bytes"] for r in before),
            "sha256": sha(json.dumps(before, sort_keys=True).encode()), "path": str(context)}


def build_command(args, context, metadata, cache_scope):
    command = ["docker", "buildx", "build", "--builder", args.builder, "--platform", args.platform,
               "--file", str(context / "frontend/Dockerfile.slim"), "--target", "runtime",
               "--build-arg", "NODE_IMAGE=" + args.node_image,
               "--build-arg", "CACHE_SCOPE=" + cache_scope,
               "--build-arg", "DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=" + args.gateway_url,
               "--build-arg", "APP_VERSION=" + args.app_version,
               "--build-arg", "NEXT_PUBLIC_STATIC_WEBSITE_ONLY=false",
               "--provenance=false", "--progress=plain", "--metadata-file", str(metadata),
               "--tag", args.tag, "--load"]
    if args.npm_registry:
        command += ["--build-arg", "NPM_REGISTRY=" + args.npm_registry]
    for name in ("enterprise_ca", "enterprise_proxy"):
        file = getattr(args, name)
        if file:
            require(file.is_file() and not file.is_symlink(), "secret_input_not_regular")
            require(not file.resolve().is_relative_to(context), "secret_inside_build_context")
            command += ["--secret", "id=" + name + ",src=" + str(file.resolve())]
    return command + [str(context)]


def capture(command, timeout=30):
    r = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(r.returncode == 0, "command_failed:" + command[0])
    require(len(r.stdout) < 8 * 1024**2, "command_output_limit")
    return r.stdout


def verify_fresh_layers(base, image):
    baseline = base['RootFS']['Layers']
    layers = image['RootFS']['Layers']
    require(bool(baseline) and layers[:len(baseline)] == baseline, 'runtime_not_from_pinned_clean_base')
    require(0 < len(layers) - len(baseline) <= 12, 'runtime_added_layer_budget_exceeded')
    return {'pinned_base_prefix': 'PASS', 'base_layers': len(baseline),
            'added_layers': len(layers) - len(baseline), 'previous_application_image_used': False}


def inspect_platform_base(reference, platform):
    base = json.loads(capture(["docker", "image", "inspect", "--platform", platform, reference]))[0]
    require(base["Architecture"] == platform.split('/')[1], "base_image_architecture_mismatch")
    return base


def run_build(command, out, log_name="BUILD.log"):
    start = time.monotonic()
    with (out / log_name).open("xb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                elapsed = int(time.monotonic() - start)
                print(json.dumps({"slim_build": "RUNNING", "elapsed_seconds": elapsed,
                                  "log_bytes": log.tell()}), flush=True)
                require(elapsed < 7200, "build_deadline")
                time.sleep(10)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    require(process.returncode == 0, "build_failed_see_private_log")
    return round(time.monotonic() - start, 2)


def hash_stream(stream, maximum):
    digest, size = hashlib.sha256(), 0
    while True:
        data = stream.read(1024 * 1024)
        if not data:
            return digest.hexdigest(), size
        size += len(data)
        require(size <= maximum, 'archive_size_budget_exceeded')
        digest.update(data)


def audit_oci(file, image):
    """Verify exported bytes against the EXACT locally qualified filesystem layers."""
    require(file.is_file() and not file.is_symlink(), 'export_missing_or_link')
    with file.open('rb') as stream:
        checksum, size = hash_stream(stream, 1536 * 1024**2)
    with tarfile.open(file, 'r:') as archive:
        members = archive.getmembers()
        require(len(members) < 256, 'oci_member_budget')
        names = {m.name for m in members}
        require(len(names) == len(members), 'duplicate_oci_member')
        for item in members:
            require(item.isfile() or item.isdir(), 'oci_link_or_special_file')
            require(not item.name.startswith('/') and '..' not in Path(item.name).parts,
                    'oci_path_escape')
        def raw(name, maximum=4 * 1024**2):
            info = archive.getmember(name)
            require(info.isfile() and info.size <= maximum, 'oci_document_bound')
            return archive.extractfile(info).read(maximum + 1)
        def document(desc):
            require(re.fullmatch(r'sha256:[a-f0-9]{64}', desc['digest']), 'oci_digest_format')
            data = raw('blobs/sha256/' + desc['digest'][7:])
            require(len(data) == desc['size'] and 'sha256:' + sha(data) == desc['digest'], 'oci_document_hash')
            return json.loads(data)
        index = json.loads(raw('index.json'))
        require(len(index['manifests']) == 1, 'single_platform_export_required')
        manifest = document(index['manifests'][0])
        config = document(manifest['config'])
        require(config['architecture'] == image['Architecture'] and config['os'] == 'linux', 'oci_platform')
        require(config['rootfs']['diff_ids'] == image['RootFS']['Layers'], 'export_not_qualified_runtime')
        require(len(manifest['layers']) == len(config['rootfs']['diff_ids']), 'oci_layer_count')
        compressed = uncompressed = 0
        for layer, expected in zip(manifest['layers'], config['rootfs']['diff_ids']):
            require(re.fullmatch(r'sha256:[a-f0-9]{64}', layer['digest']), 'oci_layer_digest_format')
            info = archive.getmember('blobs/sha256/' + layer['digest'][7:])
            require(info.isfile() and info.size == layer['size'], 'oci_layer_size')
            actual, count = hash_stream(archive.extractfile(info), 1536 * 1024**2)
            require('sha256:' + actual == layer['digest'], 'oci_layer_hash')
            compressed += count
            stream = archive.extractfile(info)
            if layer['mediaType'] == 'application/vnd.oci.image.layer.v1.tar+gzip':
                stream = gzip.GzipFile(fileobj=stream)
            else:
                require(layer['mediaType'] == 'application/vnd.oci.image.layer.v1.tar', 'unsupported_oci_compression')
            actual, count = hash_stream(stream, 2 * 1024**3)
            require('sha256:' + actual == expected, 'oci_diffid_mismatch')
            uncompressed += count
            require(uncompressed <= 2 * 1024**3, 'expanded_image_budget')
    return dict(archive=str(file), sha256=checksum, bytes=size, layers=len(manifest['layers']),
                compressed_layer_bytes=compressed, uncompressed_layer_bytes=uncompressed,
                exact_qualified_layers='PASS', blob_and_diffid_hashes='PASS')


HTTP_PROBE = r"""
const assert=require('node:assert/strict');
(async()=>{
 const origin='http://127.0.0.1:3000';
 const get=async(path)=>{const r=await fetch(origin+path,{redirect:'manual',signal:AbortSignal.timeout(10000)});
   return {r,text:await r.text()};};
 const config=await get('/api/runtime-config');
 assert.equal(config.r.status,200); const value=JSON.parse(config.text);
 assert.equal(value.weknoraFrontendUrl,'/platform/knowledge-bases');
 assert.equal(value.skillHubEnabled,true);
 const page=await get('/login');
 // Synthetic SSO-only configuration: no Gateway/model or real credentials.
 assert([302,303,307,308].includes(page.r.status));
 assert.equal(page.r.headers.get('location'),'https://sso.invalid/entry');
 const html=await get('/');
 assert([200,302,303,307,308].includes(html.r.status));
 const fs=require('node:fs'),path=require('node:path');
 const walk=d=>fs.readdirSync(d,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(d,e.name)):[path.join(d,e.name)]);
 const all=walk('.next/static');
 const checks=[];
 for(const ext of ['.js','.css']){
   const file=all.find(p=>p.endsWith(ext));assert(file);
   const url='/_next/static/'+file.slice('.next/static/'.length);
   const result=await get(url);assert.equal(result.r.status,200);
   assert.equal(result.text,fs.readFileSync(file,'utf8'));checks.push(ext);
 }
 const denied=await get('/api/cnooc/external-agents/zywdjxzj/launch');
 assert.equal(denied.r.status,401);
 assert.equal(denied.r.headers.get('x-content-type-options'),'nosniff');
 console.log(JSON.stringify({local_http:'PASS',runtime_env:'PASS',sso_redirect:'SYNTHETIC_PASS',
   static_assets:checks,unauthenticated_launch_status:denied.r.status,
   production_sso:'NOT_TESTED',gateway:'NOT_TESTED',models:'NOT_REQUESTED'}));
})().catch(()=>{console.error(JSON.stringify({local_http:'FAIL'}));process.exit(1)});
"""


def qualify(image_id, platform, out):
    layout = json.loads(capture(["docker", "run", "--rm", "--pull=never", "--platform", platform,
        "--network=none", "--entrypoint", "node", image_id, "/opt/deerflow/verify-standalone.mjs",
        "/app/frontend", "--assets"], timeout=120))
    name = "deerflow-slim-qa-" + uuid.uuid4().hex[:12]
    cid = None
    try:
        cid = capture(["docker", "run", "-d", "--name", name, "--pull=never", "--platform", platform,
                       "--network=none", "--cpus=2", "--memory=2g", "--pids-limit=256",
                       "-e", "WEKNORA_FRONTEND_URL=/platform/knowledge-bases",
                       "-e", "DEER_FLOW_SKILLHUB_ENABLED=true", "-e", "DEER_FLOW_SSO_ONLY=true",
                       "-e", "DEER_FLOW_UNAUTHENTICATED_HOME_URL=https://sso.invalid/entry",
                       image_id]).decode().strip()
        for _ in range(30):
            r = subprocess.run(["docker", "exec", cid, "node", "-e",
                "fetch('http://127.0.0.1:3000/api/runtime-config').then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if r.returncode == 0:
                break
            time.sleep(2)
        else:
            raise CheckFailure("runtime_start_deadline")
        probe = json.loads(capture(["docker", "exec", cid, "node", "-e", HTTP_PROBE], timeout=90))
        write_json(out / "RUNTIME.json", {"layout": layout, "http": probe})
        return {"layout": layout, "http": probe}
    finally:
        if cid:
            capture(["docker", "rm", "-f", cid])  # Only this invocation's disposable fixture.


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--node-image", required=True)
    p.add_argument("--platform", choices=["linux/amd64", "linux/arm64"], required=True)
    p.add_argument("--builder", default="desktop-linux")
    p.add_argument("--pool", type=Path, help="Persistent resource-bounded pool created by build_pool.py")
    p.add_argument("--lane", default="frontend")
    p.add_argument("--tag", required=True)
    p.add_argument("--gateway-url", default="http://127.0.0.1:8001")
    p.add_argument("--app-version", default="")
    p.add_argument("--npm-registry", default="")
    p.add_argument("--enterprise-ca", type=Path)
    p.add_argument("--enterprise-proxy", type=Path)
    p.add_argument("--build", action="store_true", help="Without this flag only prepares a source snapshot")
    p.add_argument("--export-oci", action="store_true", help="Export and hash-check the qualified image; no push")
    args = p.parse_args()
    require(re.fullmatch(r"[\w./:-]+@sha256:[a-f0-9]{64}", args.node_image), "pin_node_image_digest")
    require(re.fullmatch(r"[a-z0-9][a-z0-9./:-]+:[a-zA-Z0-9_.-]+", args.tag) and
            not args.tag.endswith(":latest"), "versioned_candidate_tag_required")
    # No accidental heavy builds on the known production hosts or Kubernetes control planes.
    if args.build:
        require(not re.fullmatch(r"datax-\d+|ecs-zdz-pro-\d+", socket.gethostname().split('.')[0]) and
                not Path('/etc/kubernetes/admin.conf').exists(), "use_dedicated_build_host")
        existing = subprocess.run(["docker", "image", "inspect", args.tag], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=30)
        require(existing.returncode != 0, "candidate_tag_already_exists")
    os.umask(0o077)
    root, out = args.source.resolve(), args.out.absolute()
    require(not out.exists() and not out.is_symlink(), "fresh_evidence_directory_required")
    require(not out.is_relative_to(root), "evidence_must_be_outside_source")
    out.mkdir(parents=True)
    result = {"frontend_slim": "INCOMPLETE", "production_writes": 0, "images_pushed": False,
              "ready_for_rollout": False, "platform": args.platform, "node_image": args.node_image}
    try:
        result["source"] = source = prepare(root, out)
        print(json.dumps({"source_snapshot": "PASS", "files": source["files"], "bytes": source["bytes"]}), flush=True)
        # Node digest + lockfile: architecture is added separately in the Dockerfile.
        scope = "deerflow-slim-v1-" + sha((args.node_image +
            sha((Path(source["path"]) / "frontend/pnpm-lock.yaml").read_bytes())).encode())[:20]
        command = build_command(args, Path(source["path"]), out / "METADATA.json", scope)
        if not args.build:
            result["frontend_slim"] = "PREPARED_ONLY"
            return
        if args.pool:
            from build_pool import lease
            context = lease(args.pool, args.lane)
        else:
            context = nullcontext(args.builder)
        with context as builder:
            args.builder = builder
            command = build_command(args, Path(source["path"]), out / "METADATA.json", scope)
            result["builder"] = builder
            result["build_seconds"] = run_build(command, out)
        image = json.loads(capture(["docker", "image", "inspect", args.tag]))[0]
        require(image["Architecture"] == args.platform.split('/')[1], "image_architecture_mismatch")
        require(image["Size"] <= 1536 * 1024**2 and len(image["RootFS"]["Layers"]) <= 30,
                "image_size_or_layer_budget_exceeded")
        result["image"] = {"tag": args.tag, "id": image["Id"], "bytes": image["Size"],
                           "layers": len(image["RootFS"]["Layers"])}
        base = inspect_platform_base(args.node_image, args.platform)
        result['fresh_runtime'] = verify_fresh_layers(base, image)
        result["runtime"] = qualify(image["Id"], args.platform, out)
        if args.export_oci:
            context = lease(args.pool, args.lane) if args.pool else nullcontext(args.builder)
            with context as builder:
                args.builder = builder
                command = build_command(args, Path(source['path']), out / 'EXPORT-METADATA.json', scope)
                command.remove('--load')
                command[-1:-1] = ['--output', 'type=oci,dest=' + str(out / 'frontend.oci.tar')]
                result['export_seconds'] = run_build(command, out, 'EXPORT.log')
                result['export'] = audit_oci(out / 'frontend.oci.tar', image)
        result["frontend_slim"] = "LOCAL_PASS"
    except Exception as exc:
        result["frontend_slim"] = "FAIL"
        result["failure"] = str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__
        raise
    finally:
        write_json(out / "RESULT.json", result)
        print(json.dumps({"result": str(out / "RESULT.json"), "frontend_slim": result["frontend_slim"],
                          "production_writes": 0, "ready_for_rollout": False}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"frontend_slim": "FAIL", "check": str(exc) if isinstance(exc, CheckFailure)
                          else type(exc).__name__, "raw_error_printed": False}), flush=True)
        sys.exit(1)
