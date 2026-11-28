"""Install the global pool with the operator's datax kubectl context."""
import argparse
import chat_models_release as chat
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

NS = "deer-flow"
LEDGER = "df-global-warm-pool-v1"
LABEL = "deerflow.io/global-pool-release"
WORKLOADS = ("provisioner", "gateway", "ppt-master-runner")
TAG = hashlib.sha256(Path(sys.argv[0]).read_bytes()).hexdigest()[:16] if __name__ == "__main__" else ""


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def kub(*args, body=None, parse=True, timeout=65):
    raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    target = args[0]
    if args[0] == "exec":
        container = args[args.index("-c") + 1]
        i = args.index("--") + 1
        root = "/app" if container == "provisioner" else "/app/backend"
        # kubectl exec starts from the container workingDir. The workload
        # entrypoint changes directory before launching its Python process.
        launcher = 'cd "$1" && shift && export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" && exec "$@"'
        target = f"exec {args[i-4]} ({container})"
        args = (*args[:i], "sh", "-c", launcher, "release-check", root, *args[i:])
    proc = subprocess.run(["kubectl", "-n", NS, *args], input=raw, capture_output=True, timeout=timeout)
    if proc.returncode:
        text = proc.stderr.decode(errors="replace")
        # Do not echo failed API objects or inline Python that might include deployment config.
        hint = next((line[:250] for line in reversed(text.splitlines()) if line.startswith(
            ("RuntimeError:", "ValueError:", "PermissionError:", "ModuleNotFoundError:", "requests.exceptions."))), "")
        reason = next((x for x in ("Forbidden", "AlreadyExists", "Conflict", "NotFound", "Invalid", "timed out")
                       if ("(" + x + ")") in text or (x == "timed out" and x in text)), "command failed")
        raise RuntimeError(f"kubectl {target}: {reason} {hint}".strip())
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout) if parse else proc.stdout.decode().strip()


def read(kind, name):
    return kub("get", kind, name, "--ignore-not-found", "-o", "json")


def ensure(kind, name, *, version="v1", mutable=False, **parts):
    obj = {"apiVersion": version, "kind": kind, "metadata": {
        "namespace": NS, "name": name, "labels": {LABEL: "v1"}}, **parts}
    old = read(kind, name)
    if old is None:
        return kub("create", "-f", "-", "-o", "json", body=obj)
    require(old["metadata"].get("labels", {}).get(LABEL) == "v1" and
            not old["metadata"].get("deletionTimestamp"), "Resource name conflict: " + name)
    for key, value in parts.items():
        if mutable and key == "data":
            continue
        actual = old.get(key)
        if kind == "NetworkPolicy" and key == "spec" and value.get("ingress") == []:
            actual = {"ingress": [], **actual}
        require(actual == value, "Existing resource differs: " + name + "/" + key)
    return old


def edit_registry(fn):
    for _ in range(20):
        cm = read("cm", LEDGER)
        require(cm and cm["metadata"].get("labels", {}).get(LABEL) == "v1", "Pool ledger is unavailable")
        db = json.loads(cm["data"]["registry"])
        fn(db)
        patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": cm["metadata"]["resourceVersion"]},
                 {"op": "replace", "path": "/data/registry", "value": json.dumps(db, separators=(",", ":"))}]
        try:
            kub("patch", "cm", LEDGER, "--type=json", "-p", json.dumps(patch), "-o", "json")
            return
        except RuntimeError:
            if read("cm", LEDGER)["metadata"]["resourceVersion"] == cm["metadata"]["resourceVersion"]:
                raise
    raise RuntimeError("Concurrent ledger updates; rerun the same command")


def report(stage, **kwargs):
    print(json.dumps({"stage": stage, **kwargs}, ensure_ascii=False), flush=True)


def registry():
    return json.loads(read("cm", LEDGER)["data"]["registry"])


def counts(db):
    result = {}
    for row in db["slots"].values():
        result[row["phase"]] = result.get(row["phase"], 0) + 1
    return result


def select(selector, labels):
    if any(labels.get(k) != v for k, v in selector.get("matchLabels", {}).items()):
        return False
    for term in selector.get("matchExpressions", []):
        key, op = term["key"], term["operator"]
        values = term.get("values", [])
        require(op in ("In", "NotIn", "Exists", "DoesNotExist"), "Unknown label selector")
        if ((op == "In" and labels.get(key) not in values) or (op == "NotIn" and labels.get(key) in values)
                or (op == "Exists" and key not in labels) or (op == "DoesNotExist" and key in labels)):
            return False
    return True


def policy_and_roles(deps, uid):
    policy_name = "df-global-pool-ingress-v1"
    peers = [{"podSelector": deps[n]["spec"]["selector"]} for n in WORKLOADS[1:]]
    require(all(p["podSelector"].get("matchLabels") for p in peers), "Missing trusted client selectors")
    slot_labels = {"app": "deer-flow-sandbox", "app.kubernetes.io/name": "deer-flow",
                   "app.kubernetes.io/component": "sandbox", "df-warm-role": "slot",
                   "df-warm-state": "standby", "df-pool-registry": uid}
    for svc in kub("get", "services", "-o", "json")["items"]:
        sel = svc["spec"].get("selector")
        if sel and all(k in ("df-pool-generation",) or slot_labels.get(k) == v for k, v in sel.items()):
            own = svc["metadata"].get("labels", {}).get("df-pool-registry") == uid
            require(own and "df-pool-generation" in sel, "Existing Service would select neutral slots: " +
                    svc["metadata"]["name"])
    existing = kub("get", "networkpolicies", "-o", "json")["items"]
    for policy in existing:
        if policy["metadata"]["name"] == policy_name or not policy["spec"].get("ingress"):
            continue
        # Pool generation values vary per slot. Treat selectors mentioning that
        # key as potentially matching, regardless of their specific value.
        selector = copy.deepcopy(policy["spec"]["podSelector"])
        selector.get("matchLabels", {}).pop("df-pool-generation", None)
        selector["matchExpressions"] = [x for x in selector.get("matchExpressions", [])
                                         if x["key"] != "df-pool-generation"]
        for role in ("slot", "mount-worker"):
            labels = slot_labels if role == "slot" else {"df-warm-role": role, "df-pool-registry": uid}
            if select(selector, labels):
                allowed = all(rule.get("from") and all(p in peers for p in rule["from"]) for rule in
                              policy["spec"]["ingress"])
                require(allowed and role == "slot", "Existing policy grants additional pool ingress: " +
                        policy["metadata"]["name"])
    ensure("NetworkPolicy", policy_name, version="networking.k8s.io/v1",
           spec={"podSelector": {"matchLabels": {"df-warm-role": "slot", "df-pool-registry": uid}},
                 "policyTypes": ["Ingress"], "ingress": [{"from": peers, "ports": [
                     {"protocol": "TCP", "port": 8080}, {"protocol": "TCP", "port": 8788}]}]})
    ensure("NetworkPolicy", "df-global-mount-worker-deny-v1", version="networking.k8s.io/v1",
           spec={"podSelector": {"matchLabels": {"df-warm-role": "mount-worker", "df-pool-registry": uid}},
                 "policyTypes": ["Ingress"], "ingress": []})
    account = deps["provisioner"]["spec"]["template"]["spec"].get("serviceAccountName")
    require(account == "deer-flow-provisioner", "Unexpected Provisioner service account")
    rbac = "rbac.authorization.k8s.io/v1"
    ensure("Role", LEDGER, version=rbac, rules=[{"apiGroups": [""], "resources": ["configmaps"],
                                                "resourceNames": [LEDGER], "verbs": ["get", "update"]}])
    ensure("RoleBinding", LEDGER, version=rbac, subjects=[{"kind": "ServiceAccount", "name": account,
                                                           "namespace": NS}], roleRef={
               "kind": "Role", "name": LEDGER, "apiGroup": "rbac.authorization.k8s.io"})


def items(z, workload, baseline):
    result = {}
    for path in z.namelist():
        if path.startswith(workload + "/"):
            fname = path.split("/")[1]
            target = baseline["files"].get(fname[:-3], {}).get("path", "/app/" + fname)
            result[fname] = (target, z.read(path).decode())
    return result


def settled(d):
    count = d["spec"].get("replicas", 1)
    s = d["status"]
    return (count > 0 and not d["spec"].get("paused") and d["metadata"]["generation"] == s.get("observedGeneration")
            and all(s.get(k) == count for k in ("readyReplicas", "updatedReplicas", "availableReplicas", "replicas")))


def wait_for(fn, limit, stage):
    end = time.monotonic() + limit
    last = 0
    while time.monotonic() < end:
        if fn():
            return
        if time.monotonic() - last >= 45:
            report(stage)
            last = time.monotonic()
        time.sleep(3)
    raise RuntimeError(stage + " timed out; rerun to resume")


def verify(workload, d, files, patched):
    c = workload
    selector = d["spec"]["selector"]
    require(not selector.get("matchExpressions") and selector.get("matchLabels"), "Unsupported workload selector")
    label = ",".join(k + "=" + v for k, v in selector["matchLabels"].items())
    pods = kub("get", "pods", "-l", label, "-o", "json")["items"]
    running = [(p, s) for p in pods if not p["metadata"].get("deletionTimestamp") for s in
               p["status"].get("containerStatuses", []) if s["name"] == c and s["ready"]]
    require(len(running) == d["spec"].get("replicas", 1) and all(
        status["imageID"] == files["image_id"] for _, status in running), "Workload images changed: " + workload)
    sums = {f["path"]: f["sha256"] for f in files["files"].values()}
    sums.update({p: hashlib.sha256(s.encode()).hexdigest() for p, s in patched.values()})
    code = ("import hashlib,pathlib\nfor p,h in " + repr(sums) + ".items():\n"
            " if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h: raise ValueError('Source drift: '+p)")
    # For pre-existing images, sample each distinct pod-template hash. After a
    # rollout the identical immutable ConfigMap is pinned for every replica.
    checked = set()
    for p, _ in running:
        key = p["metadata"]["labels"].get("pod-template-hash")
        if key not in checked:
            checked.add(key)
            kub("exec", "-i", p["metadata"]["name"], "-c", c, "--",
                "python3" if workload == "provisioner" else "/app/backend/.venv/bin/python", "-B", "-",
                body=code.encode(), parse=False)


def check_imports(workload, payload):
    prefix = "" if workload == "provisioner" else "deerflow.community.aio_sandbox."
    sources = {prefix + fname[:-3]: (path, source) for fname, (path, source) in payload.items()}
    if workload == "gateway":
        for k, m in chat.MODULES.items():
            sources[m] = sources.pop(prefix + k)
    checks = ("""
import app
assert 'post' in app.app.openapi()['paths']['/api/sandboxes/lookup']
assert callable(app.cold_create_sandbox) and callable(app._start_pool)
""" if workload == "provisioner" else """
from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider
from deerflow.community.aio_sandbox.remote_backend import RemoteSandboxBackend
assert callable(AioSandboxProvider._discover_thread_sandbox)
assert callable(RemoteSandboxBackend.discover_for_thread)
""")
    if workload == "gateway":
        checks += "\nimport deerflow.agents.lead_agent.agent\n" + chat.VERIFY
    program = """
import importlib.abc, importlib.util, sys
class Overlay(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in files:
            return importlib.util.spec_from_loader(fullname, self)
    def exec_module(self, mod):
        path, text = files[mod.__name__]
        mod.__file__ = path
        exec(compile(text, path, 'exec'), mod.__dict__)
sys.meta_path.insert(0, Overlay())
"""
    kub("exec", "-i", "deployment/" + workload, "-c", workload, "--",
        "python3" if workload == "provisioner" else "/app/backend/.venv/bin/python",
        "-B", "-", body=("files=" + repr(sources) + program + checks).encode(), parse=False)


def rollout(name, data, uid, original):
    d = read("deployment", name)
    stamped = d["spec"]["template"]["metadata"].get("annotations", {}).get(LABEL)
    if not stamped:
        require(d["metadata"]["generation"] == original["metadata"]["generation"], "Concurrent rollout: " + name)
        cm_name = f"df-pool-{TAG}-{name}"
        ensure("ConfigMap", cm_name, immutable=True, data={k: v[1] for k, v in data.items()})
        tpl = copy.deepcopy(d["spec"]["template"])
        spec = tpl["spec"]
        container = next(c for c in spec["containers"] if c["name"] == name)
        paths = {v[0] for v in data.values()}
        mounts = container.setdefault("volumeMounts", [])
        mounts[:] = [m for m in mounts if m["mountPath"] not in paths]
        require(not any(v["name"] == "df-pool-code" for v in spec.get("volumes", [])), "Volume name collision")
        spec.setdefault("volumes", []).append({"name": "df-pool-code", "configMap": {"name": cm_name}})
        mounts.extend({"name": "df-pool-code", "mountPath": target, "subPath": k, "readOnly": True}
                      for k, (target, _) in sorted(data.items()))
        if name == "provisioner":
            env = container.setdefault("env", [])
            require(all(v["name"] != "DF_GLOBAL_POOL_LEDGER_UID" for v in env), "Unexpected existing pool UID")
            env.append({"name": "DF_GLOBAL_POOL_LEDGER_UID", "value": uid})
        tpl["metadata"].setdefault("annotations", {})[LABEL] = TAG
        patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": d["metadata"]["resourceVersion"]},
                 {"op": "replace", "path": "/spec/template", "value": tpl}]
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as payload:
            json.dump(patch, payload)
            payload.flush()
            kub("patch", "deployment", name, "--type=json", "--patch-file", payload.name, "-o", "name", parse=False)
    else:
        require(stamped == TAG, "Different release installed on " + name)
    def complete():
        current = read("deployment", name)
        require(current["spec"]["template"]["metadata"]["annotations"].get(LABEL) == TAG,
                "Deployment changed during rollout: " + name)
        return settled(current)
    wait_for(complete, 7200, "WAITING_FOR_" + name.upper())
    report("WORKLOAD_READY", workload=name)


def install(z):
    from upgrade_r3_layout import resume
    resume(sys.modules[__name__], z)
    manifest = json.loads(z.read("manifest.json"))
    deps = {n: read("deployment", n) for n in WORKLOADS}
    content = {n: items(z, n, manifest[n + "/" + n]) for n in WORKLOADS}
    old_gateway = deps["gateway"]["spec"]["template"]["metadata"].get("annotations", {}).get(LABEL)
    if old_gateway == TAG:
        saved = read("cm", f"df-pool-{TAG}-gateway")
        require(saved and saved.get("immutable"), "Gateway release overlay unavailable")
        for k,m in chat.MODULES.items():
            target = "/app/backend/packages/harness/" + m.replace(".", "/") + ".py"
            content["gateway"][k+".py"] = (target, saved["data"][k+".py"])
    else:
        chat.prepare(kub, content["gateway"])
    for n, d in deps.items():
        old = d["spec"]["template"]["metadata"].get("annotations", {}).get(LABEL)
        require(old in (None, TAG), "Different release on " + n)
        if not old:
            require(d["metadata"]["generation"] == manifest[n + "/" + n]["generation"],
                    "Deployment configuration changed since source capture: " + n)
        if not old or settled(d):
            wait_for(lambda: settled(read("deployment", n)), 300, "WAITING_FOR_BASELINE_" + n)
            verify(n, read("deployment", n), manifest[n + "/" + n], content[n] if old else {})
        check_imports(n, content[n])
    ident = [str(uuid.uuid4()), str(uuid.uuid4()), False]
    cm = ensure("ConfigMap", LEDGER, mutable=True, data={"registry": json.dumps({
        "schema": 1, "enabled": False, "commission": ident, "fixture": ident, "nonce": str(uuid.uuid4()),
        "release": TAG, "slots": {}, "cold": {}})})
    uid = cm["metadata"]["uid"]
    db = registry()
    require(db["release"] == TAG, "Ledger owned by a different release")
    epoch = db.get("pause_epoch")
    if not db.get("commission") and not db.get("verified"):
        edit_registry(lambda r: r.update(commission=r["fixture"]))
        db = registry()
    policy_and_roles(deps, uid)
    rollout("provisioner", content["provisioner"], uid, deps["provisioner"])
    if not db.get("verified"):
        ident, nonce = db["commission"], db["nonce"]
        wait_for(lambda: any(r["phase"] == "empty" or r.get("owner") == ident for r in registry()["slots"].values()),
                 360, "WAITING_FOR_FIRST_NEUTRAL_SLOT")
        out = kub("exec", "-i", "deployment/gateway", "-c", "gateway", "--", "/app/backend/.venv/bin/python",
                  "-B", "-", ident[0], ident[1], nonce, body=z.read("commission.py"), timeout=260)
        require(out["stage"] == "FIRST_GLOBAL_BINDING_VERIFIED", "First slot verification incomplete")
        report(**out)
        edit_registry(lambda r: r.update(verified=True, commission=None, acceptance=out))
    db = registry()
    if not db.get("fixture_cleaned"):
        ident = db["fixture"]
        wait_for(lambda: not any(r.get("owner") == ident for r in registry()["slots"].values()), 240,
                 "WAITING_FOR_COMMISSION_RELEASE")
        clean = kub("exec", "-i", "deployment/gateway", "-c", "gateway", "--", "/app/backend/.venv/bin/python",
                    "-B", "-", ident[0], ident[1], db["nonce"], "--cleanup", body=z.read("commission.py"))
        require(clean["stage"] == "COMMISSION_STORAGE_CLEANED", "Release fixture cleanup incomplete")
        edit_registry(lambda r: r.update(fixture_cleaned=True))
    for n in WORKLOADS[1:]:
        rollout(n, content[n], uid, deps[n])
    for n in WORKLOADS:
        verify(n, read("deployment", n), manifest[n + "/" + n], content[n])
    kub("exec", "-i", "deployment/gateway", "-c", "gateway", "--", "/app/backend/.venv/bin/python",
        "-B", "-", body=chat.VERIFY.encode(), parse=False)
    report("GATEWAY_CHAT_MODEL_CONFIG_VERIFIED")
    def enable(r):
        require(r.get("verified") and r["release"] == TAG and r.get("pause_epoch") == epoch, "Release changed or paused")
        r["enabled"] = True
    edit_registry(enable)
    db = registry()
    report("ALL_USERS_WARM_FIRST_ENABLED", target=170, current_slots=counts(db), enabled=db["enabled"])


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    for flag in ("install", "status", "pause"):
        group.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    if args.install:
        try:
            with zipfile.ZipFile(sys.argv[0]) as z:
                install(z)
        except Exception as exc:
            report("INSTALL_STOPPED", reason=str(exc)[:450])
            try:
                db = registry()
                report("GLOBAL_POOL_STATUS", enabled=db["enabled"], current_slots=counts(db))
                for name, row in db["slots"].items():
                    if db.get("verified") or row.get("owner", db.get("fixture")) != db.get("fixture"):
                        continue
                    names = [name]
                    if row.get("uid"):
                        names.append("df-mount-" + row["uid"])
                    for podname in names:
                        pod = read("pod", podname)
                        if pod:
                            events = kub("get", "events", "--field-selector",
                                         "involvedObject.uid=" + pod["metadata"]["uid"] + ",type=Warning", "-o", "json")
                            report("COMMISSION_POD", name=podname, node=pod["spec"].get("nodeName"),
                                   phase=pod["status"].get("phase"), warnings=sorted({
                                       e.get("reason", "Unknown") for e in events["items"]}))
            except Exception:
                pass
            sys.exit(1)
    else:
        if args.pause:
            def pause(r):
                r.update(enabled=False, commission=None, pause_epoch=str(uuid.uuid4()))
                for row in r["slots"].values():
                    if row["phase"] in ("preparing", "empty"):
                        row["phase"] = "retiring"
            edit_registry(pause)
        db = registry()
        report("GLOBAL_POOL_STATUS", enabled=db["enabled"], target=170, current_slots=counts(db))


if __name__ == "__main__":
    main()
