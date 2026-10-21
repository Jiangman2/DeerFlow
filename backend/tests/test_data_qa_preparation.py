"""No real cluster writes: preparation ownership, drift and uncertain failures."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

FOLDER = Path(__file__).resolve().parents[2] / "deploy/enterprise"
spec = importlib.util.spec_from_file_location("preparation_packager", FOLDER / "package-data-qa-glm-preparation.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def scope():
    s = {"__name__": "test_preparation"}
    exec(compile(builder.build(FOLDER), "<packaged>", "exec"), s)
    return s


def deployment():
    container = dict(name="gateway", env=[dict(name="EXISTING", value="keep")], envFrom=[dict(secretRef=dict(name="existing-secret"))])
    return dict(
        metadata=dict(uid="gateway", generation=381), spec=dict(replicas=70, template=dict(spec=dict(containers=[container]))), status=dict(observedGeneration=381, replicas=70, readyReplicas=70, availableReplicas=70, updatedReplicas=70)
    )


def test_actual_five_preloads_are_exactly_bound(scope):
    scope["preload_gate"]()
    scope["PRELOADS"]["ecs-zdz-pro-0018"]["roles"][0]["manifest_digest"] = "sha256:wrong"
    with pytest.raises(scope["Refused"], match="preload_image"):
        scope["preload_gate"]()


def test_candidate_job_is_separate_bounded_and_uses_dedicated_secret(scope):
    d = deployment()
    original = copy.deepcopy(d)
    job = scope["candidate_job"](d, "probe", "node", "new-config", "new-secret", "offline")
    assert d == original
    spec = job["spec"]
    assert (spec["backoffLimit"], spec["activeDeadlineSeconds"], spec["ttlSecondsAfterFinished"]) == (0, 180, 600)
    pod = spec["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "app" not in spec["template"]["metadata"]["labels"]
    assert len(pod["containers"]) == 1
    c = pod["containers"][0]
    assert c["imagePullPolicy"] == "Never" and c["command"][:5] == ["/app/backend/.venv/bin/python", "-I", "-B", "-u", "-c"]
    assert c["env"][-1]["valueFrom"]["secretKeyRef"] == dict(name="new-secret", key=scope["KEY"])
    assert c["envFrom"] == d["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert pod["volumes"] == [dict(name="candidate-config", configMap=dict(name="new-config", items=[dict(key="config.yaml", path="config.yaml")]))]


@pytest.mark.parametrize("field", ["replicas", "readyReplicas", "availableReplicas", "updatedReplicas", "observedGeneration", "terminatingReplicas"])
def test_all_rollout_drift_refused(scope, field):
    d = deployment()
    scope["RELEASE"]["field_acceptance"]["gateway"] = scope["binding"](d)
    d["status"][field] = 1
    with pytest.raises(scope["Refused"], match="rollout"):
        scope["stable_gateway"](d)


class Fake:
    def __init__(self, s, fail=None):
        self.s, self.fail = s, fail
        self.report = dict(operation="a" * 32, queries=[], resource_intents=[], candidate_checks=[])
        self.writes, self.claimed, self.gets = [], False, 0
        self.d = deployment()
        s["RELEASE"]["field_acceptance"]["gateway"] = s["binding"](self.d)
        self.cm = dict(
            metadata=dict(uid=s["CONFIG_UID"]),
            immutable=True,
            data={
                "config.yaml": yaml.safe_dump(
                    dict(
                        sandbox=dict(use="keep"),
                        models=[],
                        tools=[dict(name="datalake_smart_qa", use="deerflow.community.appforge.tools:datalake_smart_qa_tool"), dict(name="datalake_detail_qa", use="deerflow.community.appforge.tools:datalake_detail_qa_tool")],
                    )
                )
            },
        )
        s["CONFIG_SHA"] = s["digest"](self.cm["data"])
        self.front = copy.deepcopy(self.d)
        self.front["metadata"]["uid"] = "front"
        s["FRONT"] = s["binding"](self.front)
        s["sealed_evidence"] = lambda: None
        s["inspect_job"] = lambda *args: None
        self.new = None

    def get(self, kind, name):
        if kind == "deployment":
            self.gets += 1
            if name == "frontend":
                return self.front
            result = copy.deepcopy(self.d)
            if self.fail == "drift" and self.gets > 2:
                result["metadata"]["generation"] += 1
            return result
        if kind == "node":
            return dict(metadata=dict(uid=self.s["PRELOADS"][name]["expected_node_uid"], labels={"kubernetes.io/hostname": name}), spec={}, status=dict(conditions=[dict(type="Ready", status="True")]))
        if kind == "configmap":
            return self.cm if name == self.s["CONFIG_NAME"] else self.new
        raise AssertionError(kind)

    def call(self, phase, args, value=None, raw=False):
        if phase == "gateway_pods":
            p = dict(metadata=dict(name="p"), status=dict(phase="Running", containerStatuses=[dict(name="gateway", ready=True, imageID=self.s["RELEASE"]["field_acceptance"]["base_manifest"])]))
            return dict(items=[p] * 70)
        if phase == "transform_config":
            ns = {}
            exec(self.s["MIGRATION"], ns)
            return dict(data={"config.yaml": yaml.safe_dump(ns["migrate_configuration"](yaml.safe_load(value["config.yaml"])))})
        if phase == "verify_secret_metadata":
            return ("uid-secret\ntrue\n" + self.s["KEY"] + "\n").encode()
        raise AssertionError(phase)

    def claim_once(self):
        assert not self.claimed
        self.claimed = True

    def save(self):
        pass

    def create(self, kind, obj):
        assert self.claimed
        self.writes.append(kind)
        if self.fail == kind:
            raise self.s["Refused"]("uncertain_create")
        if kind == "configmap":
            self.new = copy.deepcopy(obj)
            self.new["metadata"]["uid"] = "uid-configmap"
        return "uid-" + kind


def test_preparation_success_no_deployment_mutation_no_secret_in_report(scope):
    c = Fake(scope)
    scope["prepare"](c, lambda _: "synthetic-private-key")
    assert c.writes == ["secret", "configmap", "job", "job"]
    assert c.report["status"] == "DATA_QA_GLM_PREPARED_CANDIDATE_ACCEPTED_NOT_DEPLOYED"
    assert "synthetic-private-key" not in json.dumps(c.report)
    assert c.report["gateway_before"] == c.report["gateway_after"]


@pytest.mark.parametrize("kind,expected", [("secret", ["secret"]), ("configmap", ["secret", "configmap"]), ("job", ["secret", "configmap", "job"]), ("drift", [])])
def test_no_retry_after_uncertain_write_or_drift(scope, kind, expected):
    c = Fake(scope, kind)
    with pytest.raises(scope["Refused"]):
        scope["prepare"](c, lambda _: "synthetic-private-key")
    assert c.writes == expected


def test_invalid_key_does_not_claim_or_create(scope):
    c = Fake(scope)
    with pytest.raises(scope["Refused"], match="key_shape"):
        scope["prepare"](c, lambda _: "bad\nkey")
    assert not c.claimed and not c.writes


@pytest.mark.parametrize("name", ["gateway", "other-container"])
def test_candidate_resource_reference_is_explicitly_remapped_or_rejected(scope, name):
    d = deployment()
    d["spec"]["template"]["spec"]["containers"][0]["env"].append(dict(name="CPU", valueFrom=dict(resourceFieldRef=dict(containerName=name, resource="limits.cpu"))))
    if name != "gateway":
        with pytest.raises(scope["Refused"], match="resource_reference"):
            scope["candidate_job"](d, "probe", "node", "config", "secret", "code")
    else:
        job = scope["candidate_job"](d, "probe", "node", "config", "secret", "code")
        assert job["spec"]["template"]["spec"]["containers"][0]["env"][1]["valueFrom"]["resourceFieldRef"]["containerName"] == "candidate"
        assert d["spec"]["template"]["spec"]["containers"][0]["env"][1]["valueFrom"]["resourceFieldRef"]["containerName"] == "gateway"


@pytest.mark.parametrize("failure", ["owner", "image", "result", "job_uid"])
def test_candidate_job_refuses_wrong_ownership_image_or_failed_result(scope, failure):
    class JobController:
        report = {"candidate_checks": []}

        def save(self):
            pass

        def get(self, kind, name):
            return dict(metadata=dict(uid="wrong" if failure == "job_uid" else "job-uid"), status=dict(succeeded=1))

        def call(self, phase, args, raw=False):
            if phase == "candidate_job_events":
                return {"items": []}
            if phase == "candidate_pods":
                image = "wrong" if failure == "image" else scope["RELEASE"]["profile"]["roles"][0]["manifest_digest"]
                return dict(items=[dict(metadata=dict(name="p", ownerReferences=[dict(uid="wrong" if failure == "owner" else "job-uid", controller=True)]), status=dict(containerStatuses=[dict(imageID=image)]))])
            return b'{"status":"FAIL","error_code":"CANDIDATE_CONFIG_HASH","provider_requests":0}'

    with pytest.raises(scope["Refused"]):
        scope["inspect_job"](JobController(), "job", "job-uid")


def test_create_records_uncertainty_before_subprocess_and_does_not_retry(scope, tmp_path):
    c = scope["Controller"](tmp_path, {"resource_intents": []}, "/never")
    calls = []

    def fail(*args, **kwargs):
        assert (tmp_path / "REPORT.json").exists()
        calls.append(args)
        raise scope["Refused"]("timeout")

    c.call = fail
    with pytest.raises(scope["Refused"]):
        c.create("secret", dict(metadata=dict(name="new"), stringData={"KEY": "hidden"}))
    assert len(calls) == 1
    assert "hidden" not in (tmp_path / "REPORT.json").read_text()
    assert c.report["resource_intents"][0]["creation_may_have_applied"] is True


def test_successful_create_persists_uid_without_secret_value(scope, tmp_path):
    c = scope["Controller"](tmp_path, {"resource_intents": []}, "/never")
    uid = "12345678-1234-1234-1234-123456789012"
    c.call = lambda *args, **kwargs: uid.encode()
    assert c.create("secret", dict(metadata=dict(name="new"), stringData={"KEY": "hidden"})) == uid
    report = json.loads((tmp_path / "REPORT.json").read_text())
    assert report["resource_intents"][0]["uid"] == uid
    assert report["resource_intents"][0]["creation_confirmed"] is True
    assert "hidden" not in json.dumps(report)


@pytest.mark.parametrize("condition_only", [False, True])
def test_empty_pod_inventory_reports_job_diagnostics_not_wrong_owner(scope, condition_only):
    class MissingPod:
        def __init__(self):
            self.report = {"candidate_checks": []}
            self.saved = False

        def save(self):
            self.saved = True

        def get(self, kind, name):
            status = {"conditions": [{"type": "Failed", "status": "True", "reason": "DeadlineExceeded", "message": "private-value"}]}
            if not condition_only:
                status["failed"] = 1
            return {"metadata": {"uid": "job-uid"}, "status": status}

        def call(self, phase, args, raw=False):
            if phase == "candidate_job_events":
                return {"items": [{"reason": "FailedCreate", "message": "private-value: forbidden: must specify requests.ephemeral-storage, exceeded quota: limits.cpu=1"}]}
            assert phase == "candidate_pods"
            return {"items": []}

    c = MissingPod()
    with pytest.raises(scope["Refused"], match="candidate_pod_absent_review_job_events"):
        scope["inspect_job"](c, "job", "job-uid")
    assert c.saved
    assert c.report["candidate_checks"] == []
    observation = c.report["candidate_job_diagnostics"][0]
    assert observation["conditions"][0]["reason"] == "DeadlineExceeded"
    assert "private-value" not in json.dumps(observation)
    assert observation["events"][0]["categories"] == ["exceeded quota", "must specify", "forbidden"]


def test_missing_events_do_not_mask_empty_pod_failure(scope):
    class NoEvents:
        report = {}

        def save(self):
            pass

        def get(self, kind, name):
            return {"metadata": {"uid": "job-uid"}, "status": {"failed": 1}}

        def call(self, phase, args, raw=False):
            if phase == "candidate_job_events":
                raise scope["Refused"]("events_unavailable")
            return {"items": []}

    c = NoEvents()
    with pytest.raises(scope["Refused"], match="candidate_pod_absent_review_job_events"):
        scope["inspect_job"](c, "job", "job-uid")
    assert c.report["candidate_job_diagnostics"][0]["event_query_failed"] is True
