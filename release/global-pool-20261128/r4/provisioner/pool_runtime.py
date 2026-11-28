"""Provisioner global pool. Kubernetes resource versions serialize routing decisions."""
import copy
import json
import logging
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from kubernetes.client.rest import ApiException

from scoped_layout import slot, worker

log = logging.getLogger(__name__)
LEDGER = "df-global-warm-pool-v1"
CAP = 170


def ready(pod):
    return not pod.metadata.deletion_timestamp and any(
        c.type == "Ready" and c.status == "True" for c in pod.status.conditions or [])


def booted(pod):
    statuses = pod.status.container_statuses or []
    return not pod.metadata.deletion_timestamp and {c.name for c in statuses if c.started} == {
        "sandbox", "ppt-master-agent-host"}


def owner(req):
    return [req.user_id, req.thread_id, req.include_legacy_skills]


def missing(req):
    raise HTTPException(404, "assignment_absent")


class Pool:
    def __init__(self, app, uid):
        self.app = app
        self.api = app.core_v1
        self.ns = app.K8S_NAMESPACE
        self.uid = uid
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.serial = threading.Lock()
        self.worker_code = Path(__file__).with_name("mount_worker.py").read_text()
        self.probe_code = Path(__file__).with_name("consumer_check.py").read_text()
        self.controller = threading.Thread(target=self.run, daemon=True)

    def load(self):
        cm = self.api.read_namespaced_config_map(LEDGER, self.ns, _request_timeout=5)
        if cm.metadata.uid != self.uid or cm.metadata.deletion_timestamp:
            raise HTTPException(503, "Pool ledger generation changed")
        db = json.loads(cm.data["registry"])
        if db.get("schema") != 1 or len(db["slots"]) > CAP or type(db.get("enabled")) is not bool:
            raise HTTPException(503, "Invalid pool ledger")
        return cm, db

    def edit(self, change):
        for _ in range(12):
            cm, db = self.load()
            old = copy.deepcopy(db)
            result = change(db)
            if old == db:
                return result
            cm.data["registry"] = json.dumps(db, separators=(",", ":"))
            try:
                self.api.replace_namespaced_config_map(LEDGER, self.ns, cm, _request_timeout=5)
            except ApiException as exc:
                if exc.status == 409:
                    continue
                raise
            self.changed.set()
            return result
        raise HTTPException(503, "Pool allocation busy")

    def object(self, kind, name):
        try:
            return getattr(self.api, "read_namespaced_" + kind)(name, self.ns, _request_timeout=5)
        except ApiException as exc:
            if exc.status != 404:
                raise
            return None

    def create_object(self, kind, obj):
        try:
            return getattr(self.api, "create_namespaced_" + kind)(self.ns, obj, _request_timeout=8)
        except ApiException as exc:
            if exc.status != 409:
                raise
            return self.object(kind, obj["metadata"]["name"])

    def delete_object(self, kind, obj):
        if obj is None or obj.metadata.deletion_timestamp:
            return
        try:
            getattr(self.api, "delete_namespaced_" + kind)(obj.metadata.name, self.ns,
                body={"preconditions": {"uid": obj.metadata.uid, "resourceVersion": obj.metadata.resource_version}},
                _request_timeout=8)
        except ApiException as exc:
            if exc.status not in (404, 409):
                raise

    def warm_url(self, row):
        return f"http://df-w-{row['uid']}.{self.ns}.svc.cluster.local:{self.app.SANDBOX_CONTAINER_PORT}"

    @staticmethod
    def is_warm_url(url):
        return url.startswith("http://df-w-")

    def transition(self, name, previous, **fields):
        def update(db):
            if db["slots"].get(name) == previous:
                db["slots"][name].update(fields)
        self.edit(update)

    def new_slot(self, name, row):
        app = self.app
        raw = app._build_pod("neutral", "neutral", user_id="neutral", include_legacy_skills=False)
        body = slot(self.api.api_client.sanitize_for_serialization(raw), name, self.ns)
        labels = body["metadata"]["labels"]
        labels.update({"df-pool-generation": row["generation"], "df-pool-registry": self.uid})
        for container in body["spec"]["containers"]:
            probe = container["readinessProbe"]
            command = {key: probe.pop(key) for key in ("exec", "httpGet") if key in probe}
            if len(command) != 1:
                raise RuntimeError("Unrecognized workload readiness probe")
            probe.update({"exec": {"command": ["python3", "-I", "-B", "-c", self.probe_code, json.dumps(command)]},
                          "periodSeconds": 3, "failureThreshold": 1, "initialDelaySeconds": 0})
        return self.create_object("pod", body)

    def service(self, pod, row):
        obj = self.api.api_client.sanitize_for_serialization(self.app._build_service("unused"))
        if obj["spec"].get("type") != "ClusterIP":
            raise RuntimeError("Pool requires ClusterIP")
        obj.update(kind="Service", apiVersion="v1")
        obj["metadata"] = {"name": "df-w-" + pod.metadata.uid, "namespace": self.ns,
                           "labels": {"df-pool-registry": self.uid},
                           "ownerReferences": [self.ref(pod)]}
        obj["spec"]["selector"] = {"df-pool-generation": row["generation"], "df-pool-registry": self.uid}
        got = self.create_object("service", obj)
        if not self.owned(got, pod.metadata.uid) or got.spec.selector != obj["spec"]["selector"]:
            raise RuntimeError("Physical Service identity mismatch")
        return got

    @staticmethod
    def ref(pod):
        return {"apiVersion": "v1", "kind": "Pod", "name": pod.metadata.name, "uid": pod.metadata.uid}

    @staticmethod
    def owned(obj, uid):
        return obj is not None and any(r.kind == "Pod" and r.uid == uid
                                       for r in obj.metadata.owner_references or [])

    def attach(self, pod, row):
        user, thread, legacy = row["owner"]
        image = next(c.image for c in pod.spec.containers if c.name == "sandbox")
        obj = worker(self.api.api_client.sanitize_for_serialization(pod), user, thread, legacy, image,
                     self.worker_code, legacy_subpath=self.app._skills_pvc_subpath(
                         "custom", user_id=user, thread_id=thread))
        obj["metadata"]["labels"]["df-pool-registry"] = self.uid
        got = self.create_object("pod", obj)
        if not self.owned(got, pod.metadata.uid):
            raise RuntimeError("Mount worker owner mismatch")
        return got

    def retire(self, name, row, pod):
        uid = row.get("uid")
        if pod and (pod.metadata.labels or {}).get("df-pool-generation") != row["generation"]:
            raise RuntimeError("Slot name occupied by another generation")
        if pod and uid and pod.metadata.uid != uid:
            raise RuntimeError("Slot UID changed")
        if uid:
            svc = self.object("service", "df-w-" + uid)
            if svc:
                if not self.owned(svc, uid):
                    raise RuntimeError("Service owner changed")
                if row.get("service_uid") and svc.metadata.uid != row["service_uid"]:
                    raise RuntimeError("Refusing to remove replacement Service")
                self.delete_object("service", svc)
                return
        if uid:
            helper = self.object("pod", "df-mount-" + uid)
            if helper:
                if not self.owned(helper, uid):
                    raise RuntimeError("Mount worker owner changed")
                self.delete_object("pod", helper)
                return
        if pod:
            self.delete_object("pod", pod)
            return
        if self.object("pod", name) is not None:
            return
        def forget(db):
            if db["slots"].get(name) == row:
                del db["slots"][name]
        self.edit(forget)

    def step(self, name, row, pod):
        phase = row["phase"]
        if phase == "retiring":
            return self.retire(name, row, pod)
        if pod is None and phase == "preparing" and not row.get("uid"):
            pod = self.new_slot(name, row)
        if pod and ((pod.metadata.labels or {}).get("df-pool-generation") != row["generation"] or
                    (pod.metadata.labels or {}).get("df-pool-registry") != self.uid):
            raise RuntimeError("Slot generation mismatch")
        if not pod or pod.metadata.deletion_timestamp or (row.get("uid") and pod.metadata.uid != row["uid"]):
            self.transition(name, row, phase="retiring")
        elif phase == "preparing" and booted(pod):
            svc = self.service(pod, row)
            if svc and not svc.metadata.deletion_timestamp:
                self.transition(name, row, phase="empty", uid=pod.metadata.uid, service_uid=svc.metadata.uid)
        elif phase == "binding":
            if time.time() - row["since"] > 165:
                return self.transition(name, row, phase="retiring")
            helper = self.attach(pod, row)
            if helper.status.phase in ("Failed", "Succeeded"):
                return self.transition(name, row, phase="retiring")
            if ready(helper) and ready(pod):
                svc = self.object("service", "df-w-" + row["uid"])
                if not self.owned(svc, row["uid"]) or svc.metadata.uid != row["service_uid"]:
                    raise RuntimeError("Bound Service identity mismatch")
                self.transition(name, row, phase="ready")

    def run(self):
        while not self.stop.is_set():
            self.changed.clear()
            try:
                _, db = self.load()
                pods = self.api.list_namespaced_pod(self.ns,
                    label_selector="df-pool-registry=" + self.uid + ",df-warm-role=slot", _request_timeout=8)
                live = {p.metadata.name: p for p in pods.items}
                for name, row in db["slots"].items():
                    try:
                        self.step(name, row, live.get(name))
                    except Exception:
                        log.exception("Pool reconciliation failed for %s", name)
                if db["enabled"] or db.get("commission"):
                    cold = self.api.list_namespaced_pod(self.ns, label_selector="app=deer-flow-sandbox,!df-warm-role",
                                                       _request_timeout=8)
                    ceiling = max(0, CAP - len(cold.items)) if db["enabled"] else 1
                    live_cold = {p.metadata.uid for p in cold.items}
                    stale = {sid: entry for sid, entry in db["cold"].items() if entry.get("uid")
                             and entry["uid"] not in live_cold and
                             self.object("service", self.app._svc_name(sid)) is None}
                    def refill(current):
                        slots = current["slots"]
                        for sid, previous in stale.items():
                            if current["cold"].get(sid) == previous:
                                del current["cold"][sid]
                        if (not (current["enabled"] or current.get("commission")) or len(slots) >= ceiling or
                                sum(r["phase"] == "preparing" for r in slots.values()) >= 2):
                            return
                        name = next(f"df-warm-slot-{i:03}" for i in range(CAP)
                                    if f"df-warm-slot-{i:03}" not in slots)
                        slots[name] = {"phase": "preparing", "generation": uuid4().hex}
                    self.edit(refill)
            except Exception:
                log.exception("Global pool reconciliation will retry")
            self.changed.wait(2)

    @staticmethod
    def find(db, sid):
        return next(((n, r) for n, r in db["slots"].items() if r.get("sid") == sid), (None, None))

    def choose(self, req, cold_exists=False):
        def reserve(db):
            _, row = self.find(db, req.sandbox_id)
            identity = owner(req)
            if row:
                if row["owner"] != identity:
                    raise HTTPException(409, "Assignment context changed")
                return row
            if req.sandbox_id in db["cold"]:
                if db["cold"][req.sandbox_id]["owner"] != identity:
                    raise HTTPException(409, "Cold reservation context changed")
                return None
            for row in db["slots"].values():
                if row.get("owner", [])[:2] == identity[:2]:
                    raise HTTPException(409, "Thread already reserved")
            commissioning = db.get("commission") == identity
            if (db["enabled"] or commissioning) and not cold_exists:
                for row in db["slots"].values():
                    if row["phase"] == "empty":
                        row.update(phase="binding", owner=identity, sid=req.sandbox_id, since=time.time())
                        return row
            if commissioning:
                raise HTTPException(503, "Waiting for first neutral slot")
            # Persist the miss until the cold Pod/Service exists. Other callers
            # cannot claim a newly available warm slot for this ID in the gap.
            db["cold"][req.sandbox_id] = {"owner": identity}
            return None
        return self.edit(reserve)

    def finish_cold(self, req):
        pod = self.object("pod", self.app._pod_name(req.sandbox_id))
        if pod is None:
            raise HTTPException(503, "Cold sandbox disappeared")
        def record(db):
            entry = db["cold"].get(req.sandbox_id)
            if entry and entry["owner"] == owner(req):
                entry["uid"] = pod.metadata.uid
        self.edit(record)

    def lookup_cold(self, req):
        pod = self.object("pod", self.app._pod_name(req.sandbox_id))
        svc = self.object("service", self.app._svc_name(req.sandbox_id))
        if pod is None and svc is None:
            return None
        if pod is None or pod.metadata.deletion_timestamp or (svc and svc.metadata.deletion_timestamp):
            raise HTTPException(503, "Cold sandbox is transitioning")
        self.app._assert_existing_sandbox_identity(req.sandbox_id, thread_id=req.thread_id,
            user_id=req.user_id, include_legacy_skills=req.include_legacy_skills)
        if svc is None:
            return None
        return self.app.SandboxResponse(sandbox_id=req.sandbox_id,
            sandbox_url=self.app._url_from_service(svc, req.sandbox_id), status=pod.status.phase)

    def response(self, sid, row):
        if row["phase"] == "binding":
            raise HTTPException(425, "assignment_binding")
        if row["phase"] != "ready":
            raise HTTPException(503, "Assignment is retiring")
        helper = self.object("pod", "df-mount-" + row["uid"])
        if not self.owned(helper, row["uid"]) or not ready(helper):
            raise HTTPException(503, "Mount worker unavailable")
        ref = next(r for r in helper.metadata.owner_references if r.uid == row["uid"])
        pod = self.object("pod", ref.name)
        if not pod or pod.metadata.uid != row["uid"] or not ready(pod):
            raise HTTPException(503, "Sandbox mount not ready")
        svc = self.object("service", "df-w-" + row["uid"])
        if (not self.owned(svc, row["uid"]) or svc.metadata.uid != row["service_uid"] or svc.metadata.deletion_timestamp
                or svc.spec.selector != {"df-pool-generation": row["generation"], "df-pool-registry": self.uid}):
            raise HTTPException(503, "Sandbox Service generation unavailable")
        return self.app.SandboxResponse(sandbox_id=sid, sandbox_url=self.warm_url(row), status="Running")
