"""Read-only KB UI route audit, Python 3.7+, root on datax-01.

Reads pinned operation code and current Kubernetes routing. Makes at most three
anonymous GET requests to the existing UI asset (HTTP loopback, HTTPS loopback,
production HTTPS). No reload, configuration write, redirects, or TLS bypass.
No response body, Cookie, Authorization, TLS key, or raw config is printed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types


TOOL = Path("/var/lib/deerflow-kb-ui-r2-TLq4STGh/entry.py")
TOOL_SHA256 = "bf9cfc6283e0f02264635dad3375878aae2fad6cfad977d7efbc78249d8a0b40"
BACKUP = Path("/var/lib/deerflow-kb-ui-runtime-r2-41wmevpo")
HOST = "cnoocclaw.cnooc.com.cn"
ASSET = "/__deerflow_entry_ui_20261014_r1/entry.js"
MARKER = "kb-entry-ui-20261014-r1"


class AuditFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise AuditFailure(label)


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


def load_tool():
    for path in reversed([TOOL.parent] + list(TOOL.parent.parents)):
        s = path.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                "unsafe_operation_parent")
    fd = os.open(str(TOOL), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        s = os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o077 and s.st_size <= 131072, "unsafe_operation_file")
        raw = stream.read(131073)
    require(hashlib.sha256(raw).hexdigest() == TOOL_SHA256, "operation_hash_changed")
    module = types.ModuleType("kb_readonly_route_helper")
    module.__file__ = str(TOOL)
    # Its __main__ entry is not invoked. Only its read-only helpers are used.
    exec(compile(raw, str(TOOL), "exec"), module.__dict__)
    return module


def upstream_kind(value):
    value = value.strip("\"'")
    authority = re.match(r"^https?://([A-Za-z0-9_.$:\[\]-]+)(?:/|$)", value)
    if not authority:
        return "OTHER_REDACTED"
    host = authority.group(1)
    if "frontend" in host.lower():
        return "FRONTEND"
    if host in ("127.0.0.1:2026", "localhost:2026", "[::1]:2026"):
        return "LOOPBACK_2026"
    return "OTHER_REDACTED"


def route_summary(root):
    records = []
    http = [x for x in root["children"] if x["header"] == ["http"]]
    require(len(http) == 1, "http_block_count_changed")
    servers = [x for x in http[0]["children"] if x["header"] == ["server"]]
    for index, server in enumerate(servers):
        listens = [d[1:] for d in server["directives"] if d and d[0] == "listen"]
        entry = {"server_index": index, "listen": listens, "locations": [],
                 "direct_include_count": sum(bool(d and d[0] == "include") for d in server["directives"])}
        def walk(node):
            for child in node["children"]:
                if child["header"][:1] == ["location"]:
                    targets = [upstream_kind(d[1]) for d in child["directives"] if len(d) == 2 and d[0] == "proxy_pass"]
                    header = " ".join(child["header"])
                    if child["header"] == ["location", "/"] or "workspace" in header or "__deerflow_entry_ui" in header or any(t != "OTHER_REDACTED" for t in targets):
                        entry["locations"].append({
                            "location": header[:256], "proxy_target_kinds": targets,
                            "injection_filter": any(d and d[0] == "sub_filter" and any("/__deerflow_entry_ui_20261014_r1/" in s for s in d[1:]) for d in child["directives"]),
                            "accept_encoding_cleared": any(d == ["proxy_set_header", "Accept-Encoding", '\"\"'] for d in child["directives"]),
                        })
                walk(child)
        walk(server)
        records.append(entry)
    return records


def parse_probe(raw, label):
    text = raw.decode("latin1", errors="replace")
    rc = re.search(r"^KB_ROUTE_CURL_RC=(\d+)\s*$", text, re.M)
    lines = text.splitlines()
    statuses = [int(x) for x in re.findall(r"^HTTP/\S+ (\d{3})", text, re.M)]
    fields = {}
    for line in lines:
        if re.match(r"^HTTP/\S+ \d{3}", line):
            fields = {}
        elif ":" in line:
            name, value = line.split(":", 1)
            fields[name.lower()] = value.strip()
    code = int(rc.group(1)) if rc else None
    return {
        "probe": label, "curl_exit_code": code,
        "http_status": statuses[-1] if statuses else None,
        "ui_asset_marker": fields.get("x-knowledge-entry-ui") == MARKER,
        "content_type": fields.get("content-type", "")[:100],
        "cache_age_seconds": int(fields["age"]) if fields.get("age", "").isdigit() and len(fields["age"]) < 12 else None,
        "via_header_present": "via" in fields,
        "redirect_not_followed": bool(statuses and 300 <= statuses[-1] < 400),
        "failure_class": {0: "NONE", 6: "DNS", 7: "CONNECT", 28: "TIMEOUT", 35: "TLS_HANDSHAKE", 60: "TLS_VERIFICATION"}.get(code, "OTHER_OR_UNAVAILABLE"),
        "body_printed": False, "tls_verification_bypassed": False,
    }


def probe(m, label, nonce):
    require(label in ("HTTP_LOOPBACK_2026", "HTTPS_LOOPBACK_2443", "HTTPS_PUBLIC_DOMAIN"), "unexpected_probe")
    path = ASSET + "?kb_route_probe=" + nonce
    args = ["curl", "--silent", "--show-error", "--http1.1", "--noproxy", "*",
            "--max-time", "12", "--connect-timeout", "4", "--max-redirs", "0",
            "--max-filesize", "1048576", "--dump-header", "-", "--output", "/dev/null",
            "--header", "Cache-Control: no-cache", "--header", "Host: " + HOST]
    if label == "HTTP_LOOPBACK_2026":
        args.append("http://127.0.0.1:2026" + path)
    else:
        if label == "HTTPS_LOOPBACK_2443":
            args.extend(["--connect-to", HOST + ":443:127.0.0.1:2443"])
        args.append("https://" + HOST + path)
    # Execute only this fixed curl argv, no shell expansion of response data.
    raw = m.execute('set +e; "$@"; kb_rc=$?; printf "\\nKB_ROUTE_CURL_RC=%s\\n" "$kb_rc"; exit 0', args)
    return parse_probe(raw, label)


def optional_get(m, resource):
    require(resource in ("services", "endpointslices", "ingresses"), "unexpected_resource")
    try:
        return json.loads(m.command(m.KUBE + ["get", resource, "-o", "json"]))["items"]
    except Exception as exc:
        emit(routing_inventory=resource, available=False, error_type=type(exc).__name__, raw_error_printed=False)
        return []


def inventory(m):
    selected = set()
    for service in optional_get(m, "services"):
        name, s = service["metadata"]["name"], service["spec"]
        if not ("nginx" in name or "frontend" in name or s.get("selector", {}).get("app") in ("nginx", "frontend")):
            continue
        selected.add(name)
        emit(service=name, service_type=s.get("type"), cluster_ip=s.get("clusterIP"),
             selector=s.get("selector"), external_ips=s.get("externalIPs", []),
             load_balancer=service.get("status", {}).get("loadBalancer", {}).get("ingress", []),
             ports=[{k: p.get(k) for k in ("name", "port", "targetPort", "nodePort", "protocol")} for p in s.get("ports", [])])
    for entry in optional_get(m, "endpointslices"):
        service = entry.get("metadata", {}).get("labels", {}).get("kubernetes.io/service-name")
        if service not in selected:
            continue
        emit(endpoint_service=service,
             ports=[{k: p.get(k) for k in ("name", "port", "protocol")} for p in entry.get("ports", [])],
             endpoints=[{"addresses": e.get("addresses", []), "ready": e.get("conditions", {}).get("ready"),
                         "pod": e.get("targetRef", {}).get("name")} for e in entry.get("endpoints", [])])
    matches = 0
    for ingress in optional_get(m, "ingresses"):
        spec = ingress.get("spec", {})
        for rule in spec.get("rules", []):
            host = rule.get("host", "")
            if host not in (HOST, "", "*.cnooc.com.cn"):
                continue
            matches += 1
            emit(ingress=ingress["metadata"]["name"], ingress_class=spec.get("ingressClassName"), host=host,
                 paths=[{"path": p.get("path"), "path_type": p.get("pathType"),
                         "service": p.get("backend", {}).get("service")} for p in rule.get("http", {}).get("paths", [])])
    emit(ingress_matching_rules=matches, routing_scope="deer-flow namespace; external load balancer configuration not inspected")


def main():
    stage = "LOCAL_OPERATION_VALIDATION"
    try:
        require(os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01", "requires_root_on_datax01")
        m = load_tool()
        stage = "LIVE_BASELINE_AND_CONFIG"
        identity, cm = m.snapshot()
        m.safe_directory(BACKUP)
        state = json.loads(m.read(BACKUP / "STATE.json"))
        require(state.get("operation_version") == "runtime-only-r2" and state["identity"] == identity,
                "baseline_identity_changed")
        live = m.live_read()
        require(m.sha(live) == state["hashes"]["runtime.after.conf"], "runtime_not_exact_enabled_version")
        records = route_summary(m.parse_blocks(live.decode("utf-8")))
        emit(runtime_config="EXACT_ENABLED_VERSION", pod=m.POD, configmap_immutable=cm["immutable"], server_routes=records)
        stage = "SERVICE_AND_INGRESS_INVENTORY"
        inventory(m)
        nonce = str(int(time.time()))
        labels = ["HTTP_LOOPBACK_2026"]
        if any(any(parts and parts[0].strip("\"'").split(":")[-1] == "2443" for parts in row["listen"]) for row in records):
            labels.append("HTTPS_LOOPBACK_2443")
        labels.append("HTTPS_PUBLIC_DOMAIN")
        for label in labels:
            stage = label
            emit(route_probe="RUNNING", target=label)
            try:
                emit(**probe(m, label, nonce))
            except Exception as exc:
                emit(probe=label, outcome="UNAVAILABLE", error_type=type(exc).__name__, raw_error_printed=False)
        emit(route_audit="COMPLETE", production_mutation=False, files_modified=False,
             nginx_reload=False, credentials_sent=False, raw_config_printed=False,
             raw_bodies_printed=False, browser_route_acceptance="NOT_PROVEN")
        return 0
    except Exception as exc:
        emit(route_audit="INCOMPLETE", stage=stage, error_type=type(exc).__name__,
             detail=str(exc) if isinstance(exc, AuditFailure) else "SAFE_DIAGNOSTIC_REQUIRED",
             production_mutation=False, files_modified=False, nginx_reload=False, automatic_retry=False,
             raw_error_printed=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
