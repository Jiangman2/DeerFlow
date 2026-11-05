"""Linux Python 3.7 qualification; no cluster or provider access."""

import copy
import json
import os
import pathlib
import unittest
from unittest.mock import Mock


def load():
    path = pathlib.Path(os.environ["GLM_ACTIVATION_SCRIPT"])
    scope = {"__name__": "qualification"}
    exec(compile(path.read_bytes(), str(path), "exec"), scope)
    return scope


def deployment(n):
    return dict(
        apiVersion="apps/v1",
        kind="Deployment",
        metadata=dict(uid=n["UID"], generation=381, resourceVersion="123"),
        spec=dict(replicas=70, template=dict(spec=dict(containers=[dict(name="gateway", image=n["OLD"], imagePullPolicy="Never")]))),
        status=dict(observedGeneration=381, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70),
    )


def runtime(fixed):
    return dict(
        http=dict(status="PASS"), glm=dict(status="PASS"), brand=dict(status="PASS"), typed_uuid_read=dict(status="PASS", id_type="UUID"), stream_completion=dict(status="PASS", fixed=fixed, sdk_version="2.32.0", provider_requests=0)
    )


def inventory(n, d):
    rows = [dict(kind="ReplicaSet", metadata=dict(uid="rs", ownerReferences=[dict(kind="Deployment", uid=n["UID"], controller=True)]), spec=dict(template=copy.deepcopy(d["spec"]["template"])))]
    for i in range(70):
        rows.append(
            dict(
                kind="Pod",
                metadata=dict(name="pod-" + str(i), uid="uid-" + str(i), ownerReferences=[dict(kind="ReplicaSet", uid="rs", controller=True)]),
                spec=dict(nodeName=sorted(n["NODES"])[i % 5], containers=copy.deepcopy(d["spec"]["template"]["spec"]["containers"])),
                status=dict(phase="Running", conditions=[dict(type="Ready", status="True")], containerStatuses=[dict(name="gateway", ready=True, imageID=n["MD"], containerID="container-" + str(i), restartCount=0)]),
            )
        )
    return rows


class FakeController:
    def __init__(self, n):
        self.n = n
        self.d = deployment(n)
        self.report = dict(operation="op", directory="/private", patch_request_count=0)
        self.n["CURRENT_BINDING"] = n["binding"](self.d)
        self.n["FRONT_BINDING"] = n["binding"](self.d)
        self.other = copy.deepcopy(self.d)
        self.helper = Mock()
        self.parent = 1
        self.patches = []
        self.remote_count = 0
        self.service = dict(metadata=dict(uid="service"))
        self.front_queries = 0
        self.front_drift = False
        self.never_ready = False

    def ready(self):
        pass

    def get(self, role):
        if role == "gateway":
            d = copy.deepcopy(self.d)
            if self.patches and self.never_ready:
                d["status"]["updatedReplicas"] = 40
            return d
        self.front_queries += role == "frontend"
        d = copy.deepcopy(self.other)
        if role == "frontend" and self.front_drift and self.front_queries > 1:
            d["metadata"]["generation"] += 1
        return d

    def save(self, *args):
        pass

    def nodes(self):
        pass

    def inventory(self):
        return inventory(self.n, self.d)

    def endpoints(self, *args):
        pass

    def remote(self, pod, payload):
        self.remote_count += 1
        return runtime(payload["stream_fixed"])

    def call(self, *args, **kwargs):
        return copy.deepcopy(self.service)

    def patch(self, patch):
        self.patches.append(patch)
        self.d["spec"]["template"]["spec"]["containers"][0]["image"] = self.n["NEW"]
        self.d["metadata"]["generation"] += 1
        self.d["status"]["observedGeneration"] += 1
        return copy.deepcopy(self.d)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.n = load()

    def test_all_five_actual_receipts(self):
        self.n["preload_gate"]()
        self.assertEqual(len({v["operation"] for v in self.n["PRELOADS"].values()}), 5)

    def test_missing_node_refused(self):
        self.n["PRELOADS"].pop("ecs-zdz-pro-0022")
        with self.assertRaises(self.n["Refused"]):
            self.n["preload_gate"]()

    def test_fresh_node_uid_and_cached_image_required(self):
        rows = []
        for name, uid in self.n["NODES"].items():
            rows.append(
                dict(
                    metadata=dict(name=name, uid=uid),
                    spec={},
                    status=dict(
                        nodeInfo=dict(architecture="arm64"),
                        images=[dict(names=[self.n["NEW"]])],
                        conditions=[dict(type=k, status=v) for k, v in [("Ready", "True"), ("DiskPressure", "False"), ("MemoryPressure", "False"), ("PIDPressure", "False")]],
                    ),
                )
            )
        self.assertEqual(len(self.n["node_gate"](rows)), 5)
        rows[0]["metadata"]["uid"] = "recreated-node"
        with self.assertRaises(self.n["Refused"]):
            self.n["node_gate"](rows)

    def test_endpoints_cannot_point_to_unaccepted_pod(self):
        d = deployment(self.n)
        pods = inventory(self.n, d)[1:]
        service = dict(metadata=dict(name="gateway", uid="service"), spec=dict(selector=dict(app="gateway")))
        endpoints = []
        for i, p in enumerate(pods):
            ip = "10.0.0." + str(i + 1)
            p["status"]["podIPs"] = [dict(ip=ip)]
            endpoints.append(dict(conditions=dict(ready=True), addresses=[ip], targetRef=dict(kind="Pod", namespace="deer-flow", uid=p["metadata"]["uid"], name=p["metadata"]["name"])))
        slices = [dict(metadata=dict(labels={"kubernetes.io/service-name": "gateway"}, ownerReferences=[dict(kind="Service", uid="service")]), endpoints=endpoints)]
        self.assertEqual(self.n["endpoint_gate"](service, slices, pods)["ready_owned_uids"], 70)
        endpoints[0]["targetRef"]["uid"] = "other-pod"
        with self.assertRaises(self.n["Refused"]):
            self.n["endpoint_gate"](service, slices, pods)

    def test_changed_image_refused(self):
        next(iter(self.n["PRELOADS"].values()))["roles"][0]["manifest_digest"] = "sha256:wrong"
        with self.assertRaises(self.n["Refused"]):
            self.n["preload_gate"]()

    def test_plan_changes_only_image_with_full_cas(self):
        d = deployment(self.n)
        patch, expected = self.n["plan"](d)
        writes = [p for p in patch if p["op"] != "test"]
        self.assertEqual(writes, [dict(op="replace", path="/spec/template/spec/containers/0/image", value=self.n["NEW"])])
        tests = {p["path"]: p["value"] for p in patch if p["op"] == "test"}
        self.assertEqual(tests["/spec"], d["spec"])
        self.assertEqual(tests["/metadata/resourceVersion"], "123")
        expected["template"]["spec"]["containers"][0]["image"] = self.n["OLD"]
        self.assertEqual(expected, d["spec"])

    def test_wrong_base_or_scale_refused(self):
        for key in ("image", "replicas"):
            d = deployment(self.n)
            if key == "image":
                d["spec"]["template"]["spec"]["containers"][0]["image"] = "other"
            else:
                d["spec"]["replicas"] = 69
            with self.assertRaises(self.n["Refused"]):
                self.n["plan"](d)

    def test_not_ready_while_rolling(self):
        d = deployment(self.n)
        self.assertTrue(self.n["all_ready"](d))
        d["status"]["updatedReplicas"] = 40
        self.assertFalse(self.n["all_ready"](d))

    def test_active_pod_closure_and_changed_digest(self):
        d = deployment(self.n)
        d["spec"]["template"]["spec"]["containers"][0]["image"] = self.n["NEW"]
        rows = inventory(self.n, d)
        self.assertEqual(len(self.n["active_pods"](d, rows, self.n["NEW"])), 70)
        rows[1]["status"]["containerStatuses"][0]["imageID"] = "sha256:wrong"
        with self.assertRaises(self.n["Refused"]):
            self.n["active_pods"](d, rows, self.n["NEW"])

    def test_pod_outside_preloaded_nodes_refused(self):
        d = deployment(self.n)
        rows = inventory(self.n, d)
        rows[1]["spec"]["nodeName"] = "unqualified"
        with self.assertRaises(self.n["Refused"]):
            self.n["active_pods"](d, rows, self.n["OLD"])

    def test_runtime_requires_done_guard_and_uuid(self):
        self.n["check_runtime"](runtime(True), True)
        for field in ("stream_completion", "typed_uuid_read"):
            value = runtime(True)
            value[field]["status"] = "FAIL"
            with self.assertRaises(self.n["Refused"]):
                self.n["check_runtime"](value, True)

    def test_all_pods_accepted_after_one_patch(self):
        c = FakeController(self.n)
        self.n["configuration_gate"] = lambda *args: None
        self.n["execute"](c, {})
        self.assertEqual(len(c.patches), 1)
        self.assertEqual(c.remote_count, 72)
        self.assertTrue(c.report["stage_passed"])
        self.assertEqual(c.report["stream_completion_runtime_accepted"], 70)
        c.helper.write_new.assert_called_once()

    def test_frontend_drift_refuses_before_patch(self):
        c = FakeController(self.n)
        c.front_drift = True
        self.n["configuration_gate"] = lambda *args: None
        with self.assertRaises(self.n["Refused"]):
            self.n["execute"](c, {})
        self.assertFalse(c.patches)
        c.helper.write_new.assert_not_called()

    def test_poll_timeout_never_repatches(self):
        c = FakeController(self.n)
        c.never_ready = True
        self.n["configuration_gate"] = lambda *args: None
        ticks = iter((0, 0, 5000))
        with self.assertRaisesRegex(self.n["Refused"], "rollout_deadline_review_required"):
            self.n["execute"](c, {}, now=lambda: next(ticks), sleep=lambda _: None)
        self.assertEqual(len(c.patches), 1)

    def test_uncertain_patch_flag_precedes_transport(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "PATCH.private.json"), "w") as f:
                json.dump([], f)
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                c = self.n["ActivationController"](None, fd, dict(commands=[], patch_request_count=0), "/usr/bin/kubectl")
                c.save = lambda *args: None

                def fail(*args, **kwargs):
                    self.assertTrue(c.report["patch_may_have_applied"])
                    self.assertEqual(c.report["patch_request_count"], 1)
                    raise self.n["Refused"]("transport_error")

                c.call = fail
                with self.assertRaises(self.n["Refused"]):
                    c.patch([])
                with self.assertRaisesRegex(self.n["Refused"], "second_patch_forbidden"):
                    c.patch([])
            finally:
                os.close(fd)

    def test_runtime_source_is_pinned_and_compiles(self):
        for k, raw in self.n["SOURCES"].items():
            self.assertEqual(self.n["pin"](raw), self.n["PINS"][k])
            compile(raw, "<" + k + ">", "exec")

    def test_unqualified_canary_digest_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            name = "portal-glm-stream-candidate-" + self.n["FIELD"]["operation"] + ".json"
            p = pathlib.Path(folder) / name
            p.write_bytes(b"x" * self.n["FIELD"]["report_bytes"])
            p.chmod(0o600)
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(self.n["Refused"]):
                    self.n["read_canary"](fd)
            finally:
                os.close(fd)


if __name__ == "__main__":
    unittest.main()
