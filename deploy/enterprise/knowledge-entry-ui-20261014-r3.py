"""Temporary SIDEBAR UI-only disable; Python 3.7+, datax-01 only.

No image/deployment/secret/database changes. NO knowledge page/API blocking.
Changes ONLY the current Nginx /tmp/nginx.conf; ConfigMap is read-only.
TEMPORARY: container/Pod restart or redeployment restores the original template.
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


CM = "deer-flow-nginx-heai-20261012-r1"
CM_SHA256 = "81a5f799531a9aeea5312cc85b768705fcdd75966a7acf913cec8ac07f652f06"
OPERATION_VERSION = "active-runtime-r3"
DEPLOYMENT = "nginx-security-heai-20261012-r1"
POD = "nginx-security-heai-20261012-r1-6cfdf58fdb-2kwjd"
SERVICES = ("nginx", "nginx-nodeport")
IMAGE = "m.daocloud.io/docker.io/library/nginx@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752"

BEFORE_SHA = "2829cdcdb0e98d23e17945a190ba1df37e9b75807a1874cb111da90422fce64d"
AFTER_SHA = "14da6965ad7de34cd3790d10fa7040fed9d6e012dc0ac79b7950e68f3166d803"
PINNED_IDENTITY = {
    "deployment_uid": "ce96a382-140e-4baf-9a85-d623b7df6e3d",
    "deployment_spec_sha256": "8429c2724d82225a60b3b9400acdee2d0885d230e4f0a2df4ceaf1fd799272b2",
    "pod_uid": "386de7d4-5c2a-4458-98bb-97f94bf3bdc8",
    "container_id": "containerd://da30a9c7ca8a7aaee45c511163f36ac4f3e58c3b3f21a984cfd2b87670e63ce8",
    "configmap_uid": "0220e933-f271-4f92-ae28-622128749a76",
    "configmap_sha256": CM_SHA256, "configmap_immutable": True,
    "services": {
        "nginx": {
            "uid": "968e2c68-6635-4e60-9eba-9b13ae10647c",
            "spec_sha256": "4466eebc5928640ecee0982ed1b90131857897e1a7a59a8e7deee6381c1a8b67",
            "selector": {"app": DEPLOYMENT}, "ready_pod": POD,
            "ready_pod_uid": "386de7d4-5c2a-4458-98bb-97f94bf3bdc8",
        },
        "nginx-nodeport": {
            "uid": "5c200684-780b-4559-897c-39d3a4664afa",
            "spec_sha256": "d5c784d31129ffd4c77e67c2d613fac4717435b129d866e6ef2c8708673ffab1",
            "selector": {"app": DEPLOYMENT}, "ready_pod": POD,
            "ready_pod_uid": "386de7d4-5c2a-4458-98bb-97f94bf3bdc8",
        },
    },
}

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
    # Adding the first location-level proxy_set_header would suppress inherited
    # headers. Refuse that shape instead of changing authentication/Host behavior.
    local_headers = any(d and d[0] == "proxy_set_header" for d in location["directives"])
    inherited_headers = any(d and d[0] == "proxy_set_header"
                            for scope in (http[0], server) for d in scope["directives"])
    require(local_headers or not inherited_headers, "inherited_proxy_headers_need_review")
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


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def active_snapshot():
    pod = get('pod', POD)
    deployment = get('deployment', DEPLOYMENT)
    require(not pod['metadata'].get('deletionTimestamp') and not deployment['metadata'].get('deletionTimestamp'), 'target_deleting')
    owner = [x for x in pod['metadata'].get('ownerReferences', []) if x.get('controller')]
    require(len(owner) == 1 and owner[0]['kind'] == 'ReplicaSet', 'pod_owner_changed')
    rs = get('replicaset', owner[0]['name'])
    require(rs['metadata']['uid'] == owner[0]['uid'] and any(
        o.get('controller') and o.get('kind') == 'Deployment' and o.get('uid') == deployment['metadata']['uid']
        for o in rs['metadata'].get('ownerReferences', [])), 'deployment_ownership_changed')
    ds = deployment.get('status', {})
    require(deployment['spec'].get('replicas') == 1 and ds.get('observedGeneration') == deployment['metadata']['generation']
            and all(ds.get(k) == 1 for k in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas')), 'deployment_not_converged')
    pcs = [c for c in pod['spec']['containers'] if c['name'] == 'nginx']
    dcs = [c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'nginx']
    ready = [c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'nginx']
    require(len(pcs) == len(dcs) == len(ready) == 1 and pod['status'].get('phase') == 'Running'
            and ready[0].get('ready') and 'running' in ready[0].get('state', {}), 'container_not_ready')
    require(pcs[0]['image'] == dcs[0]['image'] and bool(ready[0].get('containerID')), 'container_image_or_identity_changed')
    bound = {}
    for name in SERVICES:
        service = get('service', name)
        selector = service['spec'].get('selector', {})
        require(bool(selector) and all(pod['metadata'].get('labels', {}).get(k) == v for k, v in selector.items()), 'service_selector_changed_' + name)
        ports = [p for p in service['spec'].get('ports', []) if p.get('port') == 2026 and p.get('protocol', 'TCP') == 'TCP']
        require(len(ports) == 1 and ports[0].get('targetPort') == 2026, 'service_port_changed_' + name)
        slices = json.loads(command(KUBE + ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + name, '-o', 'json']))
        live = []
        for entry in slices.get('items', []):
            require(entry.get('metadata', {}).get('labels', {}).get('kubernetes.io/service-name') == name, 'slice_label_mismatch')
            for endpoint in entry.get('endpoints', []):
                if endpoint.get('conditions', {}).get('ready') is not True or endpoint.get('conditions', {}).get('terminating') is True:
                    continue
                ref = endpoint.get('targetRef', {})
                require(ref.get('kind') == 'Pod' and ref.get('name') == POD and ref.get('uid') == pod['metadata']['uid']
                        and pod['status']['podIP'] in endpoint.get('addresses', []), 'service_has_other_ready_backend_' + name)
                require(any(p.get('port') == 2026 and p.get('protocol', 'TCP') == 'TCP' for p in entry.get('ports', [])), 'endpoint_port_changed')
                live.append(ref['uid'])
        require(len(live) == 1, 'requires_one_ready_endpoint_' + name)
        bound[name] = {'uid': service['metadata']['uid'], 'spec_sha256': digest(service['spec']),
                       'selector': selector, 'ready_pod': POD, 'ready_pod_uid': live[0]}
    return pod, deployment, pcs[0], ready[0], bound


def snapshot():
    p, d, container, status, services = active_snapshot()
    c = get("configmap", CM)
    require(status.get("imageID") == IMAGE, "running_image_id_changed")
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
    require(c.get("immutable") is True, "configmap_immutability_changed")
    require(set(c.get("data", {})) == {"nginx.conf"} and not c.get("binaryData")
            and isinstance(c["data"]["nginx.conf"], str), "configmap_key_shape_changed")
    require(sha(c["data"]["nginx.conf"].encode()) == CM_SHA256, "configmap_baseline_hash_changed")
    identity = {"deployment_uid": d["metadata"]["uid"], "deployment_spec_sha256": digest(d["spec"]),
                "pod_uid": p["metadata"]["uid"], "container_id": ready[0].get("containerID"), "configmap_uid": c["metadata"]["uid"],
                "configmap_sha256": CM_SHA256, "configmap_immutable": True, "services": services}
    require(identity == PINNED_IDENTITY, "active_entry_pinned_identity_changed")
    return identity, c


FILE_GUARD = 'test -f /tmp/nginx.conf; test ! -L /tmp/nginx.conf; test "$(stat -c %u:%h /tmp/nginx.conf)" = 0:1; kb_mode=$(stat -c %a /tmp/nginx.conf); test "$((0$kb_mode & 022))" -eq 0; '


def guard_master_runtime():
    masters = execute('for kb_p in /proc/[0-9]*/cmdline; do test -r "$kb_p" || continue; kb_line=$(tr "\\000" " " < "$kb_p" 2>/dev/null) || continue; case "$kb_line" in "nginx: master process "*) printf "%s\\n" "$kb_line";; esac; done').decode().splitlines()
    require(len(masters) == 1 and bool(re.search(r"(?:^|\s)-c\s+/tmp/nginx\.conf(?:\s|$)", masters[0])),
            "active_master_config_path_changed")


def live_read():
    return execute(FILE_GUARD + 'test "$(stat -c %s /tmp/nginx.conf)" -lt 2097152; cat /tmp/nginx.conf')


def http_probe(path):
    require(path in PATHS + ("/health",), "probe_path_not_allowed")
    # Loopback HTTP only, no proxy, redirects, or cookies. Never depend on nc's
    # stdin-EOF behavior: it can exit before a proxied response reaches it.
    raw = execute('curl --disable --silent --show-error --http1.1 --noproxy "*" --max-time 10 --connect-timeout 3 --max-redirs 0 --max-filesize 2097152 --dump-header - --output /dev/null --header "Host: $2" "http://127.0.0.1:2026$1"', (path, HOST))
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


def apply_runtime(directory, expected):
    require(re.fullmatch(r"/tmp/kb-maintenance-[A-Za-z0-9]{8}", directory), "unexpected_staging_path")
    execute(FILE_GUARD + 'test -d "$1"; test ! -L "$1"; test "$(stat -c %u:%a "$1")" = 0:700; test "$(sha256sum /tmp/nginx.conf | cut -d " " -f 1)" = "$2"; test -f "$1/candidate.conf"; test ! -L "$1/candidate.conf"; test "$(stat -c %u:%a:%h "$1/candidate.conf")" = 0:600:1; nginx -t -c "$1/candidate.conf"; mv "$1/candidate.conf" /tmp/nginx.conf; nginx -t -c /tmp/nginx.conf; nginx -c /tmp/nginx.conf -s reload', (directory, sha(expected)))


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



ROUTE_ORIGINS = {
    "SERVICE_nginx": "http://nginx.deer-flow.svc:2026",
    "SERVICE_nginx-nodeport": "http://nginx-nodeport.deer-flow.svc:2026",
    "PUBLIC_HTTPS": "https://" + HOST,
}


def route_probe(label, path):
    require(label in ROUTE_ORIGINS and path in PATHS[:2], "route_probe_not_allowed")
    args = ["curl", "--disable", "--silent", "--show-error", "--http1.1", "--noproxy", "*",
            "--max-time", "12", "--connect-timeout", "4", "--max-redirs", "0",
            "--max-filesize", "1048576", "--dump-header", "-", "--output", "-",
            "--header", "Cache-Control: no-cache", "--header", "Accept-Encoding: identity",
            "--header", "Host: " + HOST, ROUTE_ORIGINS[label] + path]
    # Only fixed anonymous GETs. No -k, cookies, redirects, curlrc or proxy env.
    raw = execute('set +e; "$@"; kb_rc=$?; printf "\\nKB_ACTIVE_CURL_RC=%s\\n" "$kb_rc"; exit 0', args)
    payload, separator, trailer = raw.rpartition(b"\nKB_ACTIVE_CURL_RC=")
    require(bool(separator) and re.fullmatch(rb"\d+\n", trailer), "route_probe_framing")
    code = int(trailer)
    header, _, body = payload.partition(b"\r\n\r\n")
    status = re.match(rb"HTTP/1\.[01] (\d{3})", header)
    fields = {}
    for line in header.split(b"\r\n")[1:]:
        key, sep, value = line.partition(b":")
        if sep:
            fields[key.lower()] = value.strip()
    expected = JS.encode("utf-8") if path == PATHS[0] else CSS.encode("utf-8")
    receipt = {
        "probe": label, "path": path, "curl_exit_code": code,
        "status": int(status.group(1)) if status else None,
        "ui_asset_marker": fields.get(b"x-knowledge-entry-ui") == MARKER.encode(),
        "asset_body_matches": code == 0 and sha(body) == sha(expected),
        "failure_class": {0: "NONE", 6: "DNS", 7: "CONNECT", 28: "TIMEOUT",
                          35: "TLS_HANDSHAKE", 60: "TLS_VERIFICATION"}.get(code, "OTHER"),
        "tls_verification_bypassed": False, "credentials_sent": False, "raw_body_printed": False,
    }
    return receipt


def route_checks():
    receipts = []
    for label in ROUTE_ORIGINS:
        for path in PATHS[:2]:
            emit(route_check="RUNNING", target=label, path=path)
            try:
                receipt = route_probe(label, path)
            except Exception as exc:
                receipt = {"probe": label, "path": path, "status": None,
                           "error_type": type(exc).__name__, "raw_error_printed": False}
            receipts.append(receipt)
            emit(route_check=receipt)
    return receipts


def require_routes(receipts, enabled):
    def accepted(row):
        if row.get("curl_exit_code") != 0:
            return False
        if enabled:
            return row.get("status") == 200 and row.get("ui_asset_marker") and row.get("asset_body_matches")
        return row.get("status") in (200, 301, 302, 303, 307, 308, 404) and row.get("ui_asset_marker") is False
    require(len(receipts) == 6 and all(accepted(row) for row in receipts),
            "service_or_public_route_not_verified_use_status_not_enable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true")
    group.add_argument("--restore", metavar="BACKUP_DIRECTORY")
    group.add_argument("--status", metavar="BACKUP_DIRECTORY")
    args = parser.parse_args()
    backup = None
    stage = "TARGET_BASELINE"
    runtime_change_attempted = False
    try:
        require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01", "requires_root_on_datax01")
        os.umask(0o077)
        identity, cm = snapshot()
        guard_master_runtime()
        original = live_read()
        template = cm["data"]["nginx.conf"].encode()
        # Read-only check of the mounted immutable template, not a write target.
        mounted_hash = execute('sha256sum /etc/nginx/nginx.conf.template').decode().split()[0]
        require(mounted_hash == CM_SHA256, "mounted_template_baseline_changed")
        execute('command -v curl >/dev/null; command -v sha256sum >/dev/null')
        if args.enable:
            execute('nginx -t -c /tmp/nginx.conf')
            require(http_probe("/health")["status"] == 200, "baseline_health_not_200")
            candidate = patch(original)
            require(sha(original) == BEFORE_SHA and sha(candidate) == AFTER_SHA, "runtime_baseline_or_transform_hash_changed")
            stage = "PRIVATE_BACKUP"
            safe_directory(Path("/var/lib"))
            backup = Path(tempfile.mkdtemp(prefix="deerflow-kb-ui-active-r3-", dir="/var/lib"))
            files = {"runtime.before.conf": original, "runtime.after.conf": candidate,
                     "template.snapshot.conf": template}
            for name, raw in files.items():
                save(backup / name, raw)
            save(backup / "STATE.json", json.dumps({
                "operation_version": OPERATION_VERSION, "identity": identity,
                "hashes": {k: sha(v) for k, v in files.items()},
                "scope": "RUNTIME_ONLY_SIDEBAR_UI", "marker": MARKER,
                "configmap_modified": False, "restart_persistence": False,
            }).encode())
            emit(private_backup=str(backup), stage="BACKUP_SAVED_NO_LIVE_CHANGE",
                 configmap_modified=False, restart_persistence=False)
            stage = "STAGE_AND_VALIDATE_RUNTIME"
            directory = stage_runtime(candidate)
            check_identity, current_cm = snapshot()
            require(check_identity == identity and live_read() == original
                    and current_cm["data"]["nginx.conf"].encode() == template, "baseline_changed_before_apply")
            stage = "APPLY_RUNTIME_ONLY"
            emit(stage=stage, automatic_retry=False, configmap_modified=False)
            guard_master_runtime()
            runtime_change_attempted = True
            apply_runtime(directory, original)
            stage = "VERIFY_RUNTIME_AND_HTTP"
            require(live_read() == candidate, "runtime_write_not_confirmed")
            receipts = verify(True)
            emit(runtime_config="AFTER", active_service_backend="VERIFIED", browser_acceptance="PENDING")
            stage = "VERIFY_SERVICE_AND_PUBLIC_ROUTES"
            routes = route_checks()
            require_routes(routes, True)
            post_identity, post_cm = snapshot()
            require(post_identity == identity and post_cm["data"]["nginx.conf"].encode() == template, "post_apply_state_drift")
            save(backup / "ENABLE-PASS.json", json.dumps({"http_checks": receipts, "route_checks": routes}).encode())
            emit(knowledge_entry_ui_config="PASS", mode="ENABLED_RUNTIME_ONLY",
                 private_backup=str(backup), http_checks=receipts, route_checks=routes,
                 deployment=DEPLOYMENT, pod=POD, service_selectors_changed=False,
                 public_assets="VERIFIED_BODY_AND_MARKER", authenticated_html="NOT_TESTED",
                 browser_acceptance="REQUIRED_AFTER_FULL_REFRESH", knowledge_routes_changed=False,
                 images_changed=False, pods_restarted=False, gateway_changed=False, full_knowledge_api_block=False,
                 configmap_modified=False, new_pod_template="UNCHANGED",
                 restart_persistence=False, nginx_restart_effect="ORIGINAL_TEMPLATE_RESTORED")
        else:
            stage = "CHECK_PRIVATE_BACKUP"
            backup = Path(args.restore or args.status)
            require(backup.parent == Path("/var/lib") and re.fullmatch(r"deerflow-kb-ui-active-r3-[a-zA-Z0-9_-]+", backup.name), "invalid_backup_path")
            safe_directory(backup)
            require(stat.S_IMODE(backup.lstat().st_mode) == 0o700, "backup_directory_not_private")
            state = json.loads(read(backup / "STATE.json"))
            require(state.get("marker") == MARKER and state.get("operation_version") == OPERATION_VERSION
                    and state.get("scope") == "RUNTIME_ONLY_SIDEBAR_UI", "backup_version_mismatch")
            require(identity == state["identity"], "deployment_pod_or_container_changed_requires_fresh_review")
            files = {name: read(backup / name) for name in ("runtime.before.conf", "runtime.after.conf", "template.snapshot.conf")}
            require(all(sha(raw) == state["hashes"][name] for name, raw in files.items()), "backup_digest_mismatch")
            require(patch(files["runtime.before.conf"]) == files["runtime.after.conf"], "backup_patch_mismatch")
            require(sha(files["runtime.before.conf"]) == BEFORE_SHA and sha(files["runtime.after.conf"]) == AFTER_SHA, "backup_not_pinned_runtime")
            require(files["template.snapshot.conf"] == template, "configmap_snapshot_changed")
            runtime_state = "BEFORE" if original == files["runtime.before.conf"] else "AFTER" if original == files["runtime.after.conf"] else "DRIFTED"
            emit(runtime_config=runtime_state, configmap_template="UNCHANGED_IMMUTABLE",
                 private_backup=str(backup), restart_persistence=False)
            if args.status:
                emit(http_checks=[http_probe(p) for p in PATHS] + [http_probe("/health")],
                     configuration_modified=False, configmap_modified=False)
                emit(route_checks=route_checks(), configuration_modified=False,
                     service_selectors_changed=False, browser_acceptance="NOT_TESTED")
                return 0
            require(runtime_state != "DRIFTED", "configuration_drift_do_not_overwrite")
            stage = "STAGE_AND_VALIDATE_RESTORE"
            directory = stage_runtime(files["runtime.before.conf"])
            check_identity, current_cm = snapshot()
            require(check_identity == identity and live_read() == original
                    and current_cm["data"]["nginx.conf"].encode() == template, "baseline_changed_before_restore")
            # Reload even when disk already equals BEFORE: active workers may differ.
            stage = "RESTORE_RUNTIME_ONLY"
            runtime_change_attempted = True
            apply_runtime(directory, original)
            stage = "VERIFY_RESTORE"
            require(live_read() == files["runtime.before.conf"], "restored_runtime_not_confirmed")
            receipts = verify(False)
            routes = route_checks()
            require_routes(routes, False)
            post_identity, post_cm = snapshot()
            require(post_identity == identity and post_cm["data"]["nginx.conf"].encode() == template, "post_restore_state_drift")
            emit(knowledge_entry_ui_config="PASS", mode="RESTORED", http_checks=receipts, route_checks=routes,
                 service_selectors_changed=False,
                 browser_refresh="REQUIRED", configmap_modified=False, images_changed=False,
                 pods_restarted=False, gateway_changed=False)
    except Exception as exc:
        emit(knowledge_entry_ui_config="UNCONFIRMED", error_type=type(exc).__name__, stage=stage,
             detail=str(exc) if isinstance(exc, CheckFailure) else "SAFE_DIAGNOSTIC_REQUIRED",
             private_backup=str(backup) if backup else None, automatic_retry=False,
             runtime_change_attempted=runtime_change_attempted, configmap_modified=False,
             service_selectors_changed=False, browser_acceptance="NOT_CONFIRMED",
             instruction="If backup exists, use --status then --restore with that directory; do not repeat --enable.",
             raw_error_printed=False)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


