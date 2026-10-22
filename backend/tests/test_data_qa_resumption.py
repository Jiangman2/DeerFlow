"""Resume probes only; preserve immutable configuration and credential identity."""

import copy
import importlib.util
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[2] / "deploy/enterprise"
spec = importlib.util.spec_from_file_location("resumption_builder", FOLDER / "package-data-qa-glm-resumption.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def scope():
    s = {"__name__": "test_resumption"}
    exec(compile(builder.build(FOLDER), "<resumption>", "exec"), s)
    return s


class Fake:
    def __init__(self, s, fail=None):
        self.s, self.fail = s, fail
        self.report = dict(operation="a" * 32, candidate_checks=[])
        self.jobs, self.claimed = [], False
        self.d = dict(
            metadata=dict(uid="gateway", generation=381),
            spec=dict(
                replicas=70,
                template=dict(spec=dict(nodeSelector={"workload": "gateway"}, tolerations=copy.deepcopy(s["TOLERATIONS"]), schedulerName="default-scheduler", containers=[dict(name="gateway", env=[dict(name="OLD", value="keep")])])),
            ),
            status=dict(observedGeneration=381, replicas=70, readyReplicas=70, updatedReplicas=70, availableReplicas=70),
        )
        s["RELEASE"]["field_acceptance"]["gateway"] = s["binding"](self.d)
        self.front = copy.deepcopy(self.d)
        self.front["metadata"]["uid"] = "front"
        s["FRONT"] = s["binding"](self.front)
        self.old = dict(metadata=dict(uid=s["CONFIG_UID"]), immutable=True, data={"config.yaml": "old"})
        s["CONFIG_SHA"] = s["digest"](self.old["data"])
        self.receipt = copy.deepcopy(s["FAILURE"])
        metadata = {k: self.receipt["prepared_config"][k] for k in ("name", "uid", "namespace")}
        self.config = dict(kind="ConfigMap", metadata=metadata, immutable=True, data={"config.yaml": "synthetic"})
        digest = s["digest"](self.config["data"])
        self.receipt["prepared_config"]["data_sha256"] = digest
        s["FAILURE"]["prepared_config"]["data_sha256"] = digest
        s["REUSE_NAMESPACE"]["CONFIG"]["data_sha256"] = digest
        s["sealed_evidence"] = lambda: None
        s["inspect_job"] = lambda c, name, uid: self.report["candidate_checks"].append(dict(job=name, uid=uid, observation=dict(status="PASS")))

    def get(self, kind, name):
        if kind == "deployment":
            if name == "frontend":
                return self.front
            d = copy.deepcopy(self.d)
            if self.fail == "baseline":
                d["metadata"]["generation"] += 1
            if self.fail == "toleration":
                d["spec"]["template"]["spec"]["tolerations"] = [dict(operator="Exists")]
                self.s["RELEASE"]["field_acceptance"]["gateway"] = self.s["binding"](d)
            return d
        if kind == "configmap":
            if name == self.s["CONFIG_NAME"]:
                return self.old
            c = copy.deepcopy(self.config)
            if self.fail == "config_hash":
                c["data"]["config.yaml"] = "changed"
            if self.fail == "config_uid":
                c["metadata"]["uid"] = "changed"
            return c
        if kind == "node":
            taints = [dict(key="dedicated", value="gateway", effect="NoSchedule")]
            if self.fail == "node_taint":
                taints.append(dict(key="unexpected", effect="NoSchedule"))
            return dict(metadata=dict(uid=self.s["PRELOADS"][name]["expected_node_uid"], labels={"kubernetes.io/hostname": name, "workload": "gateway"}), spec=dict(taints=taints), status=dict(conditions=[dict(type="Ready", status="True")]))
        raise AssertionError(kind)

    def call(self, phase, args, raw=False):
        if phase == "existing_secret_metadata":
            secret = self.s["FAILURE"]["prepared_secret"]
            rows = [secret["name"], secret["namespace"], secret["uid"], "true", secret["keys"][0]]
            if self.fail == "secret_uid":
                rows[2] = "changed"
            return ("\n".join(rows) + "\n").encode()
        if phase == "previous_job_absence":
            return b"{}" if self.fail == "old_job" else b""
        if phase == "previous_pods_absence":
            return dict(items=[{}] if self.fail == "old_pods" else [])
        raise AssertionError(phase)

    def create(self, kind, job):
        assert self.claimed and kind == "job"
        self.jobs.append(job)
        if self.fail == "job_create":
            raise self.s["Refused"]("create_job_timeout")
        return "job-uid"

    def claim_once(self):
        assert not self.claimed
        self.claimed = True

    def save(self):
        pass


def test_resume_reuses_resources_and_exact_toleration_without_production_changes(scope):
    c = Fake(scope)
    original = copy.deepcopy(c.d)
    scope["resume"](c, lambda: c.receipt)
    assert c.d == original
    assert len(c.jobs) == 2
    for job in c.jobs:
        spec = job["spec"]["template"]["spec"]
        assert spec["tolerations"] == scope["TOLERATIONS"]
        assert spec["nodeSelector"]["workload"] == "gateway"
        assert "nodeName" not in spec
        assert "app" not in job["spec"]["template"]["metadata"]["labels"]
        assert spec["containers"][0]["env"][-1]["valueFrom"]["secretKeyRef"]["name"] == c.receipt["prepared_secret"]["name"]
        assert job["spec"]["activeDeadlineSeconds"] == 180
    assert c.report["candidate_accepted"] == 2
    assert c.report["gateway_before"] == c.report["gateway_after"]


@pytest.mark.parametrize("failure", ["baseline", "toleration", "node_taint", "config_hash", "config_uid", "secret_uid", "old_job", "old_pods"])
def test_reuse_rejects_drift_before_claim_or_job_creation(scope, failure):
    c = Fake(scope, failure)
    with pytest.raises((scope["Refused"], ValueError)):
        scope["resume"](c, lambda: c.receipt)
    assert not c.claimed and c.jobs == []


def test_uncertain_probe_creation_is_not_repeated(scope):
    c = Fake(scope, "job_create")
    with pytest.raises(scope["Refused"], match="create_job_timeout"):
        scope["resume"](c, lambda: c.receipt)
    assert len(c.jobs) == 1


@pytest.mark.parametrize("kind", ["secret", "configmap", "deployment"])
def test_resume_controller_cannot_create_other_resources(scope, tmp_path, kind):
    c = scope["ResumptionController"](tmp_path, {"operation": "a" * 32, "resource_intents": []}, "/never")
    with pytest.raises(scope["Refused"], match="only_creates_owned_jobs"):
        c.create(kind, {"kind": kind, "metadata": {"name": "forbidden"}})
    assert c.report["resource_intents"] == []


@pytest.mark.parametrize("change", ["extra_intent", "provider_request", "deployment_patch", "unconfirmed_resource"])
def test_failed_receipt_cannot_authorize_unrelated_or_uncertain_reuse(scope, change):
    c = Fake(scope)
    if change == "extra_intent":
        c.receipt["resource_intents"].append({})
    elif change == "provider_request":
        c.receipt["provider_requests"] = 1
    elif change == "deployment_patch":
        c.receipt["deployment_patch_requested"] = True
    else:
        c.receipt["resource_intents"][0]["creation_confirmed"] = False
    with pytest.raises(ValueError):
        scope["resume"](c, lambda: c.receipt)
    assert not c.jobs and not c.claimed
