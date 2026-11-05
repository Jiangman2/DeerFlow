"""Temporary SIDEBAR UI-only disable; Python 3.7+, datax-01 only.

No image/deployment/secret/database changes. NO knowledge page/API blocking.
Changes only one pinned ConfigMap key and the current Nginx runtime config.
Failed/uncertain writes require --status/--restore, never automatic replay.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time


CM = "deer-flow-nginx-hostguard-20261011-r1"
POD = "nginx-57b7896547-jffvl"
IMAGE = "m.daocloud.io/docker.io/library/nginx@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752"
LIVE = "/tmp/nginx.conf"
MARKER = "kb-entry-ui-20261014-r1"
HOST = "cnoocclaw.cnooc.com.cn"
ASSET = "/__deerflow_entry_ui_20261014_r1/"
PATHS = (ASSET + "entry.js", ASSET + "entry.css", "/workspace/chats")
KUBE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "-n", "deer-flow", "--request-timeout=20s"]
CSS = '''[data-sidebar="sidebar"] a[data-kb-entry-disabled="true"] {
  opacity: .42 !important; cursor: not-allowed !important;
  pointer-events: auto !important;
}'''
JS = r'''(() => {
  "use strict";
  if (window.__deerflowKbEntryUiR1) return;
  window.__deerflowKbEntryUiR1 = true;
  const reason = "知识库入口暂时停用";
  const disabled = new WeakSet();
  function matches(anchor) {
    if (!(anchor instanceof HTMLAnchorElement) || !anchor.closest('[data-sidebar="sidebar"]')) return false;
    if (disabled.has(anchor)) return true;
    if (anchor.getAttribute("aria-label") === "打开知识库平台") return true;
    const href = anchor.getAttribute("href");
    if (!href) return false;
    try {
      const url = new URL(href, location.href);
      return url.origin === location.origin && (url.pathname === "/workspace/knowledge" || url.pathname === "/workspace/knowledge/");
    } catch (_) { return false; }
  }
  function disable(anchor) {
    disabled.add(anchor);
    if (anchor.hasAttribute("href")) anchor.removeAttribute("href");
    if (anchor.getAttribute("data-kb-entry-disabled") !== "true") anchor.setAttribute("data-kb-entry-disabled", "true");
    if (anchor.getAttribute("aria-disabled") !== "true") anchor.setAttribute("aria-disabled", "true");
    if (anchor.getAttribute("tabindex") !== "-1") anchor.setAttribute("tabindex", "-1");
    if (anchor.getAttribute("title") !== reason) anchor.setAttribute("title", reason);
    if (anchor.getAttribute("role") !== "link") anchor.setAttribute("role", "link");
  }
  function scan() {
    document.querySelectorAll('[data-sidebar="sidebar"] a').forEach((anchor) => {
      if (matches(anchor)) disable(anchor);
    });
  }
  function block(event) {
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!matches(anchor)) return;
    if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
    disable(anchor);
    event.preventDefault();
    event.stopImmediatePropagation();
  }
  ["click", "auxclick", "keydown", "contextmenu", "dragstart"].forEach((type) => window.addEventListener(type, block, true));
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => { queued = false; scan(); });
  });
  observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true,
    attributeFilter: ["href", "aria-label", "data-sidebar"]});
  scan();
  document.addEventListener("DOMContentLoaded", scan, {once: true});
})();'''
INJECTION = '<link rel="stylesheet" href="' + ASSET + 'entry.css"><script defer src="' + ASSET + 'entry.js"></script>'


class CheckFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailure(label)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def command(args, data=None, timeout=35):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(result.returncode == 0, "command_failed_no_raw_output")
    require(len(result.stdout) <= 4 * 1024 * 1024, "output_too_large")
    return result.stdout


def get(kind, name):
    return json.loads(command(KUBE + ["get", kind, name, "-o", "json"]))


def execute(script, args=(), data=None):
    return command(KUBE + ["exec", "-i", POD, "-c", "nginx", "--", "sh", "-eu", "-c", script, "kb-maintenance"] + list(args), data)


def nginx_quote(value):
    require("$" not in value, "unexpected_nginx_variable_in_asset")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def lex(text):
    tokens, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            continue
        start = i
        if text[i] in "{};":
            i += 1
        elif text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < len(text) and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            require(i < len(text), "unclosed_nginx_quote")
            i += 1
        else:
            while i < len(text) and not text[i].isspace() and text[i] not in "{};#":
                if text.startswith("${", i):
                    end = text.find("}", i)
                    require(end >= 0, "unclosed_nginx_variable")
                    i = end + 1
                else:
                    i += 2 if text[i] == "\\" else 1
        tokens.append((text[start:i], start, i))
    return tokens


def parse_blocks(text):
    root = {"header": [], "children": [], "directives": []}
    stack, pending = [root], []
    for value, start, end in lex(text):
        if value == "{":
            require(bool(pending), "missing_block_header")
            node = {"header": [x[0] for x in pending], "open": start, "children": [], "directives": []}
            stack[-1]["children"].append(node)
            stack.append(node)
            pending = []
        elif value == "}":
            require(len(stack) > 1 and not pending, "unbalanced_nginx_block")
            stack.pop()["close"] = start
        elif value == ";":
            stack[-1]["directives"].append([x[0] for x in pending])
            pending = []
        else:
            pending.append((value, start, end))
    require(len(stack) == 1 and not pending, "incomplete_nginx_config")
    return root


def ui_blocks():
    locations = "\n        # BEGIN " + MARKER + " assets\n"
    for path, mime, content in ((PATHS[0], "application/javascript", JS), (PATHS[1], "text/css", CSS)):
        locations += ("        location = " + path + " {\n"
                      "            default_type " + mime + ";\n            charset utf-8;\n"
                      '            add_header Cache-Control "no-store" always;\n'
                      '            add_header X-Content-Type-Options nosniff always;\n'
                      '            add_header X-Knowledge-Entry-UI "' + MARKER + '" always;\n'
                      "            return 200 " + nginx_quote(content) + ";\n        }\n")
    locations += "        # END " + MARKER + " assets\n"
    inject = ("\n            # BEGIN " + MARKER + " injection\n"
              '            proxy_set_header Accept-Encoding "";\n'
              "            sub_filter_once on;\n"
              "            sub_filter '</head>' " + nginx_quote(INJECTION + "</head>") + ";\n"
              "            # END " + MARKER + " injection\n")
    return locations, inject


def patch(raw):
    require(len(raw) < 2 * 1024 * 1024 and b"\x00" not in raw, "invalid_config_size_or_nul")
    text = raw.decode("utf-8")
    require(MARKER not in text and ASSET not in text, "already_marked_use_status_or_restore")
    root = parse_blocks(text)
    http = [x for x in root["children"] if x["header"] == ["http"]]
    require(len(http) == 1, "expected_one_http_block")
    servers = [x for x in http[0]["children"] if x["header"] == ["server"] and any(d[:2] == ["listen", "2026"] for d in x["directives"])]
    require(len(servers) == 1, "expected_unique_2026_server")
    server = servers[0]
    locations = [x for x in server["children"] if x["header"] == ["location", "/"]]
    require(len(locations) == 1, "expected_one_frontend_location")
    location = locations[0]
    upstreams = [d for d in location["directives"] if d and d[0] == "proxy_pass"]
    require(len(upstreams) == 1 and len(upstreams[0]) == 2 and upstreams[0][1] in ("http://frontend:3000", "http://$frontend_upstream"), "frontend_upstream_changed")
    for scope in (http[0], server, location):
        require(not any(d and (d[0].startswith("sub_filter") or d[0] == "proxy_set_header" and len(d) > 1 and d[1].lower() == "accept-encoding") for d in scope["directives"]), "existing_filter_or_encoding_needs_review")
    assets, inject = ui_blocks()
    # Insert only; all knowledge page/API routes and security directives survive.
    for point, addition in sorted(((server["open"] + 1, assets), (location["open"] + 1, inject)), reverse=True):
        text = text[:point] + addition + text[point:]
    return text.encode("utf-8")


def safe_directory(path):
    for item in reversed([path] + list(path.parents)):
        s = item.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, "unsafe_backup_directory")


def save(path, raw):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        s = os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077 and s.st_nlink == 1 and s.st_size <= 4 * 1024 * 1024, "unsafe_backup_file")
        return stream.read()


def snapshot():
    d, p, c = get("deployment", "nginx"), get("pod", POD), get("configmap", CM)
    require(not d["metadata"].get("deletionTimestamp") and not p["metadata"].get("deletionTimestamp"), "target_deleting")
    require(d["spec"].get("replicas") == 1, "nginx_replica_count_changed")
    ds = d.get("status", {})
    require(ds.get("observedGeneration") == d["metadata"]["generation"] and all(ds.get(x) == 1 for x in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")), "deployment_not_converged")
    owner = [x for x in p["metadata"].get("ownerReferences", []) if x.get("controller")]
    require(len(owner) == 1 and owner[0]["kind"] == "ReplicaSet", "pod_owner_not_replicaset")
    rs = get("replicaset", owner[0]["name"])
    require(rs["metadata"]["uid"] == owner[0]["uid"], "replicaset_identity_changed")
    require(any(x.get("controller") and x.get("uid") == d["metadata"]["uid"] for x in rs["metadata"].get("ownerReferences", [])), "pod_not_owned_by_deployment")
    for s in (d["spec"]["template"]["spec"], p["spec"]):
        containers = [x for x in s["containers"] if x["name"] == "nginx"]
        require(len(containers) == 1 and containers[0]["image"] == IMAGE, "nginx_image_changed")
        require(any(v["name"] == "nginx-config" and v.get("configMap", {}).get("name") == CM for v in s.get("volumes", [])), "configmap_mount_changed")
        require(any(v.get("name") == "nginx-config" and v.get("mountPath") == "/etc/nginx/nginx.conf.template" and v.get("subPath") == "nginx.conf" and v.get("readOnly") for v in containers[0].get("volumeMounts", [])), "template_mount_changed")
    ready = [x for x in p.get("status", {}).get("containerStatuses", []) if x["name"] == "nginx"]
    require(p.get("status", {}).get("phase") == "Running" and len(ready) == 1 and ready[0].get("ready") and "running" in ready[0].get("state", {}), "pod_not_ready")
    require(not c.get("immutable") and isinstance(c.get("data", {}).get("nginx.conf"), str), "configmap_immutable_or_missing_key")
    identity = {"deployment_uid": d["metadata"]["uid"], "deployment_spec_sha256": sha(json.dumps(d["spec"], sort_keys=True).encode()),
                "pod_uid": p["metadata"]["uid"], "container_id": ready[0].get("containerID"), "configmap_uid": c["metadata"]["uid"]}
    return identity, c


FILE_GUARD = 'test -f /tmp/nginx.conf; test ! -L /tmp/nginx.conf; test "$(stat -c %u:%h /tmp/nginx.conf)" = 0:1; kb_mode=$(stat -c %a /tmp/nginx.conf); test "$((0$kb_mode & 022))" -eq 0; '


def live_read():
    return execute(FILE_GUARD + 'test "$(stat -c %s /tmp/nginx.conf)" -lt 2097152; cat /tmp/nginx.conf')


def http_probe(path):
    require(path in PATHS + ("/health",), "probe_path_not_allowed")
    # Loopback HTTP only, no proxy, redirects, or cookies. Never depend on nc's
    # stdin-EOF behavior: it can exit before a proxied response reaches it.
    raw = execute('curl --silent --show-error --http1.1 --noproxy "*" --max-time 10 --connect-timeout 3 --max-redirs 0 --max-filesize 2097152 --dump-header - --output /dev/null --header "Host: $2" "http://127.0.0.1:2026$1"', (path, HOST))
    headers = raw.split(b"\r\n\r\n", 1)[0]
    match = re.match(rb"HTTP/1\.[01] (\d{3})", headers)
    return {"path": path, "status": int(match.group(1)) if match else None,
            "ui_asset_marker": ("x-knowledge-entry-ui: " + MARKER).encode() in headers.lower()}


def stage_runtime(raw):
    directory = execute('umask 077; mktemp -d /tmp/kb-maintenance-XXXXXXXX').decode().strip()
    require(re.fullmatch(r"/tmp/kb-maintenance-[A-Za-z0-9]{8}", directory), "unexpected_staging_path")
    execute('umask 077; test -d "$1"; test ! -L "$1"; test "$(stat -c %u:%a "$1")" = 0:700; test ! -e "$1/candidate.conf"; set -C; cat > "$1/candidate.conf"', (directory,), raw)
    require(execute('sha256sum "$1/candidate.conf"', (directory,)).decode().split()[0] == sha(raw), "staged_digest_mismatch")
    execute('nginx -t -c "$1/candidate.conf"', (directory,))
    return directory


def patch_cm(current, target):
    operations = [
        {"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
        {"op": "test", "path": "/data/nginx.conf", "value": current["data"]["nginx.conf"]},
        {"op": "replace", "path": "/data/nginx.conf", "value": target.decode("utf-8")},
    ]
    command(KUBE + ["patch", "configmap", CM, "--type=json", "--patch-file=/dev/stdin", "-o", "name"], json.dumps(operations).encode())
    require(get("configmap", CM)["data"]["nginx.conf"].encode() == target, "configmap_write_not_confirmed")


def apply_runtime(directory, expected):
    execute(FILE_GUARD + 'test "$(sha256sum /tmp/nginx.conf | cut -d " " -f 1)" = "$2"; test -f "$1/candidate.conf"; test ! -L "$1/candidate.conf"; mv "$1/candidate.conf" /tmp/nginx.conf; nginx -t -c /tmp/nginx.conf; nginx -c /tmp/nginx.conf -s reload', (directory, sha(expected)))


def verify(enabled):
    for attempt in range(3):
        receipts = [http_probe(p) for p in PATHS] + [http_probe("/health")]
        ok = all(x["status"] == 200 and x["ui_asset_marker"] for x in receipts[:2]) if enabled else all(x["status"] is not None and not x["ui_asset_marker"] for x in receipts[:2])
        if ok and receipts[2]["status"] in (200, 301, 302, 303, 307, 308) and receipts[-1]["status"] == 200:
            return receipts
        if attempt < 2:
            time.sleep(1)
    emit(http_checks=receipts)
    raise CheckFailure("reload_or_http_verification_unconfirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true")
    group.add_argument("--restore", metavar="BACKUP_DIRECTORY")
    group.add_argument("--status", metavar="BACKUP_DIRECTORY")
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01", "requires_root_on_datax01")
    os.umask(0o077)
    backup = None
    try:
        identity, cm = snapshot()
        original = live_read()
        # Only already available tools; no installs or external provider calls.
        execute('command -v curl >/dev/null; command -v sha256sum >/dev/null; nginx -t -c /tmp/nginx.conf')
        if args.enable:
            require(http_probe("/health")["status"] == 200, "baseline_health_not_200")
            template = cm["data"]["nginx.conf"].encode()
            candidate, template_after = patch(original), patch(template)
            safe_directory(Path("/var/lib"))
            backup = Path(tempfile.mkdtemp(prefix="deerflow-knowledge-maintenance-", dir="/var/lib"))
            files = {"runtime.before.conf": original, "runtime.after.conf": candidate,
                     "template.before.conf": template, "template.after.conf": template_after}
            for name, raw in files.items():
                save(backup / name, raw)
            save(backup / "STATE.json", json.dumps({"identity": identity, "hashes": {k: sha(v) for k, v in files.items()}, "scope": "SIDEBAR_UI_ONLY", "marker": MARKER}).encode())
            emit(private_backup=str(backup), stage="BACKUP_SAVED_NO_LIVE_CHANGE")
            directory = stage_runtime(candidate)
            check_identity, current_cm = snapshot()
            require(check_identity == identity and live_read() == original and current_cm["data"]["nginx.conf"].encode() == template, "baseline_changed_before_apply")
            emit(stage="APPLY_CONFIGMAP_THEN_RUNTIME", automatic_retry=False)
            patch_cm(current_cm, template_after)
            apply_runtime(directory, original)
            require(live_read() == candidate, "runtime_write_not_confirmed")
            receipts = verify(True)
            post_identity, post_cm = snapshot()
            require(post_identity == identity and post_cm["data"]["nginx.conf"].encode() == template_after, "post_apply_state_drift")
            save(backup / "ENABLE-PASS.json", json.dumps({"http_checks": receipts}).encode())
            emit(knowledge_entry_ui_config="PASS", mode="ENABLED", private_backup=str(backup), http_checks=receipts,
                 browser_acceptance="REQUIRED_AFTER_FULL_REFRESH", knowledge_routes_changed=False,
                 images_changed=False, pods_restarted=False, gateway_changed=False, full_knowledge_api_block=False,
                 container_restart_recheck="REQUIRED_SUBPATH_TEMPLATE", new_pod_template="UPDATED")
        else:
            backup = Path(args.restore or args.status)
            require(backup.parent == Path("/var/lib") and backup.name.startswith("deerflow-knowledge-maintenance-"), "invalid_backup_path")
            safe_directory(backup)
            state = json.loads(read(backup / "STATE.json"))
            require(state["marker"] == MARKER, "backup_version_mismatch")
            require(identity == state["identity"], "deployment_pod_or_container_changed_requires_fresh_review")
            files = {name: read(backup / name) for name in ("runtime.before.conf", "runtime.after.conf", "template.before.conf", "template.after.conf")}
            require(all(sha(raw) == state["hashes"][name] for name, raw in files.items()), "backup_digest_mismatch")
            require(patch(files["runtime.before.conf"]) == files["runtime.after.conf"] and patch(files["template.before.conf"]) == files["template.after.conf"], "backup_patch_mismatch")
            runtime_state = "BEFORE" if original == files["runtime.before.conf"] else "AFTER" if original == files["runtime.after.conf"] else "DRIFTED"
            template = cm["data"]["nginx.conf"].encode()
            template_state = "BEFORE" if template == files["template.before.conf"] else "AFTER" if template == files["template.after.conf"] else "DRIFTED"
            emit(runtime_config=runtime_state, configmap_template=template_state, private_backup=str(backup))
            if args.status:
                emit(http_checks=[http_probe(p) for p in PATHS] + [http_probe("/health")], configuration_modified=False)
                return
            require("DRIFTED" not in (runtime_state, template_state), "configuration_drift_do_not_overwrite")
            directory = stage_runtime(files["runtime.before.conf"])
            check_identity, current_cm = snapshot()
            require(check_identity == identity and live_read() == original and current_cm["data"]["nginx.conf"].encode() == template, "baseline_changed_before_restore")
            if template_state == "AFTER":
                patch_cm(current_cm, files["template.before.conf"])
            # Reload even when disk already equals BEFORE: active workers may differ.
            apply_runtime(directory, original)
            require(live_read() == files["runtime.before.conf"], "restored_runtime_not_confirmed")
            receipts = verify(False)
            post_identity, post_cm = snapshot()
            require(post_identity == identity and post_cm["data"]["nginx.conf"].encode() == files["template.before.conf"], "post_restore_state_drift")
            emit(knowledge_entry_ui_config="PASS", mode="RESTORED", http_checks=receipts, browser_refresh="REQUIRED", images_changed=False, pods_restarted=False, gateway_changed=False)
    except Exception as exc:
        emit(knowledge_entry_ui_config="UNCONFIRMED", error_type=type(exc).__name__,
             detail=str(exc) if isinstance(exc, CheckFailure) else "SAFE_DIAGNOSTIC_REQUIRED",
             private_backup=str(backup) if backup else None, automatic_retry=False,
             instruction="If backup exists, use --status then --restore with that directory; do not repeat --enable.", raw_error_printed=False)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
