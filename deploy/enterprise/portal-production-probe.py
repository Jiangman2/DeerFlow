#!/usr/bin/env python3
"""Read-only Kubernetes inventory plus bounded Work root connectivity probes.

Python 3.7-compatible controller, intended for datax-01. No credentials, cookies,
query strings, response bodies, raw errors, full env or Secret objects exported.
Two owned ready Gateway samples, at most four HEADs plus two clean navigations
(each limited to five same-origin document requests; no subresources).
No workload/config changes, no login, no clicks, no model calls or image imports.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys

KUBE = ["kubectl", "--request-timeout=20s", "-n", "deer-flow"]
DEPLOYMENTS = ("gateway", "frontend", "ppt-master-runner", "provisioner")

PROBE = r'''
import asyncio, contextlib, hashlib, io, json, os, socket, ssl
from pathlib import Path
from urllib.parse import urlsplit

TARGET = "https://open.w.cnooc.com.cn/"
HOST = "open.w.cnooc.com.cn"
report = {"target_origin": TARGET, "application_credentials_sent": False,
          "config_scope": "BROWSER_TOOL_SETTINGS_NO_SECRET_VALUES_EXPORTED",
          "configuration_modified": False, "production_threads_inspected": False,
          "tls_verification": "ENABLED", "openssl": ssl.OPENSSL_VERSION}

def classify(exc):
    value = str(exc).lower()
    for label, tokens in (
        ("TLS_WEAK_KEY_OR_SIGNATURE", ("key too weak", "key_too_weak", "digest too weak")),
        ("TLS_CERTIFICATE_VERIFICATION", ("certificate verify", "certificate_verify", "err_cert_")),
        ("PROXY", ("proxyerror", "proxy error", "err_proxy", "tunnel_connection")),
        ("DNS", ("name or service", "name_not_resolved", "name resolution")),
        ("TIMEOUT", ("timeout", "timed out")),
        ("CONNECTION", ("connection refused", "connection reset", "err_connection")),
        ("BROWSER_NOT_INSTALLED", ("executable doesn't exist", "executable does not exist")),
    ):
        if any(token in value for token in tokens):
            return label
    return "OTHER"

def origin(url):
    try:
        p = urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return "NON_HTTP"
        return p.scheme + "://" + p.hostname + (":" + str(p.port) if p.port else "")
    except Exception:
        return "INVALID"

try:
    report["dns_addresses"] = sorted(set(item[4][0] for item in socket.getaddrinfo(HOST, 443)))
except Exception as exc:
    report["dns_error"] = type(exc).__name__
report["proxy_variables_present"] = {key: bool(os.getenv(key)) for key in
    ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")}
report["ca_variables_present"] = {key: bool(os.getenv(key)) for key in
    ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "DEERFLOW_BROWSER_CA_BUNDLE")}
source_paths = ("app/gateway/routers/agents.py", "app/gateway/routers/browser.py",
                "packages/harness/deerflow/community/browser_automation/tools.py",
                "packages/harness/deerflow/community/browser_automation/session.py",
                "packages/harness/deerflow/models/robust_openai.py")
report["sources"] = {}
for name in source_paths:
    path = Path("/app/backend") / name
    if path.is_file() and path.stat().st_size < 2097152:
        report["sources"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        report["sources"][name] = "ABSENT_OR_OVERSIZE"

try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from deerflow.community.browser_automation.tools import _get_tool_config, validate_browser_url
        cfg = _get_tool_config("browser_navigate")
        verdict = validate_browser_url(TARGET)
    report["browser_policy"] = {"target_allowed": verdict is None,
        "private_origins_configured": bool(cfg.get("private_origins")),
        "legacy_allow_private_addresses": cfg.get("allow_private_addresses") is True,
        "cdp_configured": bool(cfg.get("cdp_url")),
        "allow_unguarded_cdp": cfg.get("allow_unguarded_cdp") is True}
except Exception as exc:
    report["browser_policy"] = {"check": "UNAVAILABLE", "error_type": type(exc).__name__}

report["head_probes"] = []
try:
    import requests
    for mode in ("CURRENT_ENVIRONMENT", "DIRECT"):
        receipt = {"route": mode, "method": "HEAD", "redirects_followed": False}
        session = requests.Session()
        session.trust_env = mode == "CURRENT_ENVIRONMENT"
        # Explicit no-op auth prevents requests from reading application .netrc.
        session.auth = lambda request: request
        try:
            verify = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or True
            request = session.prepare_request(requests.Request("HEAD", TARGET))
            settings = session.merge_environment_settings(TARGET, {}, False, verify, None)
            receipt["proxy_selected"] = bool(requests.utils.select_proxy(TARGET, settings["proxies"]))
            response = session.send(request, timeout=(5, 8), allow_redirects=False, **settings)
            receipt["http_status"] = response.status_code
            receipt["redirect_origin"] = origin(response.headers.get("Location", "")) if response.is_redirect else None
            response.close()
        except Exception as exc:
            receipt.update(error_type=type(exc).__name__, error_class=classify(exc))
        finally:
            session.close()
        report["head_probes"].append(receipt)
except ImportError:
    report["head_probes"] = [{"status": "REQUESTS_NOT_INSTALLED"}]

async def chromium_probe():
    receipt = {"clean_context": True, "credentials": "NONE", "clicks": 0,
        "route": "DIRECT_DEFAULT_CHROMIUM", "certificate_bypass": False,
        "cross_origin_requests": "BLOCKED", "scope": "ROOT_GET_NOT_LOGIN_ACCEPTANCE"}
    blocked = set()
    requests_sent = 0
    browser = None
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(headless=True, timeout=15000)
                context = await browser.new_context(service_workers="block", ignore_https_errors=False, java_script_enabled=False)
                receipt["javascript"] = "DISABLED_FOR_BOUNDED_DIAGNOSTIC"
                # CDP interception pauses every redirect hop before sending it.
                page = await context.new_page()
                cdp = await context.new_cdp_session(page)
                async def paused(event):
                    nonlocal requests_sent
                    url = event.get("request", {}).get("url", "")
                    allowed = (origin(url) == TARGET.rstrip("/")
                        and event.get("resourceType") == "Document"
                        and event.get("request", {}).get("method") == "GET"
                        and requests_sent < 5)
                    if allowed:
                        requests_sent += 1
                    if not allowed:
                        blocked.add(origin(url))
                    await cdp.send("Fetch.continueRequest" if allowed else "Fetch.failRequest",
                        {"requestId": event["requestId"], **({} if allowed else {"errorReason": "BlockedByClient"})})
                cdp.on("Fetch.requestPaused", paused)
                await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
                response = await page.goto(TARGET, timeout=20000, wait_until="domcontentloaded")
                receipt.update(http_status=response.status if response else None, final_origin=origin(page.url))
            finally:
                if browser is not None:
                    await browser.close()
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, error_class=classify(exc))
    receipt["blocked_origins"] = sorted(blocked)[:16]
    receipt["document_requests_sent"] = requests_sent
    return receipt
report["chromium"] = asyncio.run(chromium_probe())
print(json.dumps(report, ensure_ascii=False))
'''


def command(args, stdin=None, timeout=25):
    result = subprocess.run(KUBE + args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("kubernetes_command_failed")
    return json.loads(result.stdout)


def bounded_probe_source():
    # The supervisor bounds DNS/import waits as well as HTTP/browser timeouts.
    # Start an isolated child process group, never signal the Gateway process.
    return '''
import json, os, signal, subprocess, sys
child = subprocess.Popen([sys.executable, "-B", "-c", %r], stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, universal_newlines=True, start_new_session=True)
try:
    out, _ = child.communicate(timeout=85)
    data = json.loads(out) if child.returncode == 0 else {"probe": "INCOMPLETE", "child_exit_nonzero": True}
except subprocess.TimeoutExpired:
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.communicate()
    data = {"probe": "INCOMPLETE", "error_class": "PROBE_DEADLINE", "automatic_retry": False}
except (ValueError, TypeError):
    data = {"probe": "INCOMPLETE", "error_class": "PROBE_OUTPUT_INVALID", "raw_output_printed": False}
print(json.dumps(data))
''' % PROBE


def fingerprint(deployment):
    spec = deployment["spec"]
    encoded = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    return {"uid": deployment["metadata"]["uid"], "generation": deployment["metadata"].get("generation"),
            "spec_sha256": hashlib.sha256(encoded).hexdigest(), "desired": spec.get("replicas"),
            "ready": deployment.get("status", {}).get("readyReplicas", 0),
            "images": {c["name"]: c["image"] for c in spec["template"]["spec"]["containers"]}}


def main():
    if socket.gethostname().split(".")[0] != "datax-01":
        raise RuntimeError("requires_datax01")
    os.environ.setdefault("KUBECONFIG", "/etc/kubernetes/admin.conf")
    deployments = {name: command(["get", "deployment", name, "-o", "json"]) for name in DEPLOYMENTS}
    before = {name: fingerprint(value) for name, value in deployments.items()}
    rs = command(["get", "replicasets", "-o", "json"])["items"]
    gateway_rs = {r["metadata"]["uid"] for r in rs if any(o.get("uid") == before["gateway"]["uid"] and o.get("controller") for o in r["metadata"].get("ownerReferences", []))}
    pods = command(["get", "pods", "-o", "json"])["items"]
    eligible = [p for p in pods if not p["metadata"].get("deletionTimestamp")
                and any(o.get("uid") in gateway_rs and o.get("controller") for o in p["metadata"].get("ownerReferences", []))
                and any(c.get("name") == "gateway" and c.get("ready") for c in p.get("status", {}).get("containerStatuses", []))]
    selected = []
    for pod in sorted(eligible, key=lambda p: p["metadata"]["name"]):
        if pod["spec"].get("nodeName") not in {p["spec"].get("nodeName") for p in selected}:
            selected.append(pod)
        if len(selected) == 2:
            break
    if not selected:
        raise RuntimeError("no_owned_ready_gateway")
    samples = []
    for pod in selected:
        name = pod["metadata"]["name"]
        print("portal_probe=RUNNING pod=" + name, flush=True)
        receipt = command(["--request-timeout=95s", "exec", "-i", name, "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-B", "-"], bounded_probe_source(), timeout=100)
        samples.append({"pod": name, "node": pod["spec"]["nodeName"], "image_id": next(c.get("imageID") for c in pod["status"]["containerStatuses"] if c["name"] == "gateway"), "receipt": receipt})
    volumes = deployments["gateway"]["spec"]["template"]["spec"].get("volumes", [])
    mounts = next(c.get("volumeMounts", []) for c in deployments["gateway"]["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
    shared = [{"name": v["name"], "pvc": v.get("persistentVolumeClaim", {}).get("claimName"), "nfs": "nfs" in v} for v in volumes if "persistentVolumeClaim" in v or "nfs" in v]
    shared_names = {v["name"] for v in shared}
    after = {name: fingerprint(command(["get", "deployment", name, "-o", "json"])) for name in DEPLOYMENTS}
    print(json.dumps({"portal_production_probe": "COMPLETE", "deployments": before, "baseline_unchanged": before == after,
        "gateway_shared_volumes": shared, "gateway_shared_mounts": [m for m in mounts if m["name"] in shared_names],
        "samples": samples, "workloads_mutated": False, "configuration_modified": False, "model_calls": 0,
        "images_imported": False, "rollout_performed": False, "ready_for_rollout": False}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"portal_production_probe": "INCOMPLETE", "error_type": type(exc).__name__,
                          "raw_error_printed": False, "automatic_retry": False, "production_mutation": False}))
        sys.exit(1)
