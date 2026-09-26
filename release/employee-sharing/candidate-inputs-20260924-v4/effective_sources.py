"""Audited runtime overlays for disposable image builds; host Python 3.7+.

Never patches Kubernetes. The image-stage helper replays ONLY the captured
Gateway Python source mounts over a separately verified exact OCI base. The
read-only build guard checks source bindings and contents, not employee data.
An overlay-aware image is still not a deployment/backup/rollback authorization.
"""

import argparse
import base64
import copy
import datetime
import importlib.util
import json
import re
import time
from pathlib import Path

from apply_image_delta import apply, check_path, safe_path, sha, unique_object
from source_overlay_plan import digest, make_plan, source

HERE = Path(__file__).absolute().parent
FIELDS = (
    "mount_path",
    "volume_name",
    "sub_path",
    "read_only",
    "configmap_name",
    "configmap_key",
)


def auditor():
    # Importing this verified local control module performs no remote reads.
    spec = importlib.util.spec_from_file_location(
        "employee_source_auditor", HERE / "collect-source-overlays.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding_identity(binding):
    return {key: binding[key] for key in FIELDS}


def image_id(value):
    value = value.split("://")[-1].split("@")[-1]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("invalid overlay image identity")
    return value


def validate(document, components):
    """No application compilation/imports: this also runs on the 3.7 host."""
    if (
        document.get("schema") != 1
        or document.get("scope") != "AUDITED_EFFECTIVE_IMAGE_SOURCES"
        or document.get("ready_for_rollout") is not False
        or document.get("production_mutation") != "NONE"
        or set(components) != {"gateway", "frontend", "provisioner"}
    ):
        raise ValueError("invalid effective-source build input")
    report = document.get("report")
    if report is None:
        # Generic image-only input mode. Live overlays remain a hard blocker;
        # this is not a flag that can permit unknown source mounts.
        if document.get("source_report_sha256") is not None:
            raise ValueError("unexpected source report identity")
        return
    if not re.fullmatch(r"[a-f0-9]{64}", document.get("source_report_sha256", "")):
        raise ValueError("source report checksum missing")
    stamp = datetime.datetime.fromisoformat(report["collected_at"])
    if stamp.tzinfo is None:
        raise ValueError("source evidence timestamp must have a timezone")
    make_plan(report, components["gateway"])
    audit = auditor()
    configs = {}
    for name, state in report["workloads"].items():
        if (
            not isinstance(state["deployment_uid"], str)
            or not state["deployment_uid"]
            or not re.fullmatch(r"[a-f0-9]{64}", state["deployment_spec_sha256"])
            or not 1 <= len(state["instances"]) <= 16
            or len(state["source_bindings"]) > 64
        ):
            raise ValueError("incomplete workload evidence")
        ids = {image_id(item["image_id"]) for item in state["instances"]}
        if len(ids) != 1:
            raise ValueError("mixed runtime image identities")
        if name in components and (
            state["image"] != components[name]["source_image"]
            or ids != {components[name]["source_image_id"]}
        ):
            raise ValueError("overlay and candidate baseline identities differ")
        if name in ("frontend", "provisioner") and state["source_bindings"]:
            raise ValueError("additional component overlay requires separate review")
        seen = set()
        for binding in state["source_bindings"]:
            raw = source(binding)
            path = binding["mount_path"]
            if not path.startswith("/app/backend/"):
                raise ValueError("overlay is outside the reviewed backend source")
            relative = safe_path(path[len("/app/backend/") :])
            if (
                not relative.startswith(("app/", "packages/harness/"))
                or not relative.endswith(".py")
                or path.casefold() in seen
                or type(binding.get("configmap_immutable")) is not bool
                or not isinstance(binding["configmap_uid"], str)
                or not isinstance(binding["configmap_resource_version"], str)
            ):
                raise ValueError("invalid fixed source overlay")
            seen.add(path.casefold())
            audit.identifier(binding["volume_name"])
            audit.identifier(binding["configmap_name"])
            safe_path(binding["sub_path"])
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,253}", binding["configmap_key"]):
                raise ValueError("invalid source key")
            raw.decode("utf-8")
            key = binding["configmap_name"]
            identity = (
                binding["configmap_uid"],
                binding["configmap_resource_version"],
                binding["configmap_immutable"],
            )
            if key in configs and configs[key] != identity:
                raise ValueError("inconsistent shared ConfigMap identity")
            configs[key] = identity


def from_report(raw, components):
    if raw is not None and len(raw) > 16 * 1024**2:
        raise ValueError("source evidence exceeds bound")
    result = {
        "schema": 1,
        "scope": "AUDITED_EFFECTIVE_IMAGE_SOURCES",
        "ready_for_rollout": False,
        "production_mutation": "NONE",
        "source_report_sha256": None if raw is None else sha(raw),
        "report": None
        if raw is None
        else json.loads(raw, object_pairs_hook=unique_object),
    }
    validate(result, components)
    return result


def apply_sources(root, document, components, *, check_only=True):
    """Only call on an exact verified OCI base in a disposable build layer.

    The underlying file hash may differ from its mounted production version.
    Record both rather than incorrectly claiming the captured mount was baked
    into the base. Validate all paths and compile all Python before any write.
    """
    validate(document, components)
    report = document["report"]
    bindings = (
        [] if report is None else report["workloads"]["gateway"]["source_bindings"]
    )
    entries, receipts = [], []
    root = Path(root).absolute()
    for binding in bindings:
        relative = safe_path(binding["mount_path"][len("/app/backend/") :])
        raw = source(binding)
        compile(raw, relative, "exec")
        path = root / relative
        info = check_path(path)
        before = None if info is None else sha(path.read_bytes())
        entries.append(
            {
                "path": relative,
                "source_sha256": before,
                "target_sha256": binding["sha256"],
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        )
        receipts.append(
            {
                "path": relative,
                "image_sha256_before": before,
                "effective_sha256": binding["sha256"],
            }
        )
    if entries:
        apply(
            root,
            {
                "schema": 1,
                "scope": "employee-sharing-image-candidate",
                "ready_for_rollout": False,
                "component": "gateway",
                "source_image_id": components["gateway"]["source_image_id"],
                "entries": entries,
                "anchors": {},
            },
            check_only=check_only,
        )
    return {
        "effective_source_layer": "CHECKED" if check_only else "APPLIED",
        "files": receipts,
        "source_report_sha256": document["source_report_sha256"],
        "base_image_id": components["gateway"]["source_image_id"],
        "ready_for_rollout": False,
    }


class LiveGuard:
    """Read-only drift check. Hash each new container once, then at completion.

    Deployment and ConfigMap identity/content are checked on every invocation.
    Only the fixed hash reader may execute in an existing container. No Secret
    values, application imports, or remote writes; never logs source/credentials.
    Kubernetes sampling cannot provide an atomic no-change guarantee.
    """

    def __init__(self, document, components):
        validate(document, components)
        if document["report"] is None:
            raise ValueError("live overlay guard requires reviewed source evidence")
        self.document = copy.deepcopy(document)
        self.report = self.document["report"]
        self.audit = auditor()
        self.verified_instances = set()

    def check(self, deployments, pods, read, *, force_hash=False):
        deadline = time.monotonic() + 30

        def bounded_read(arguments):
            if time.monotonic() >= deadline:
                raise RuntimeError("source drift guard exceeded read budget")
            # The fixed collector command also has its own 30-second timeout:
            # a slow last query may extend this guard to at most ~60 seconds.
            return read(arguments)

        states = self.report["workloads"]
        actual = {d["metadata"]["name"]: d for d in deployments["items"]}
        if len(actual) != len(deployments["items"]) or set(actual) != set(states):
            raise ValueError("overlay workload coverage differs")
        configs, selected_instances, checked_now = {}, set(), set()
        allowed = {}

        def config(binding, *, again=False):
            name = binding["configmap_name"]
            if name not in configs or again:
                cm = bounded_read(["get", "configmap", name, "-o", "json"])
                if (
                    cm["metadata"]["name"] != name
                    or cm["metadata"]["uid"] != binding["configmap_uid"]
                    or cm["metadata"]["resourceVersion"]
                    != binding["configmap_resource_version"]
                    or cm.get("immutable", False) != binding["configmap_immutable"]
                ):
                    raise ValueError("source ConfigMap identity changed")
                configs[name] = cm
            return configs[name]

        for name, state in states.items():
            deployment = actual[name]
            if (
                deployment["metadata"]["uid"] != state["deployment_uid"]
                or digest(deployment["spec"]) != state["deployment_spec_sha256"]
            ):
                raise ValueError("audited source deployment changed: " + name)
            bindings = state["source_bindings"]
            expected = [binding_identity(b) for b in bindings]
            template = deployment["spec"]["template"]["spec"]
            if self.audit.bindings(name, template) != expected:
                raise ValueError("audited template source bindings changed")
            allowed[name] = {b["mount_path"]: binding_identity(b) for b in bindings}
            hashes = {}
            for binding in bindings:
                raw = self.audit.config_key(config(binding), binding["configmap_key"])
                if sha(raw) != binding["sha256"] or len(raw) != binding["bytes"]:
                    raise ValueError("source ConfigMap content changed")
                hashes[binding["mount_path"]] = {"sha256": sha(raw), "bytes": len(raw)}
            selector = deployment["spec"].get("selector", {})
            labels = selector.get("matchLabels", {})
            if not labels or selector.get("matchExpressions"):
                raise ValueError("unsupported source workload selector")
            selected = [
                p
                for p in pods["items"]
                if all(
                    p["metadata"].get("labels", {}).get(k) == v
                    for k, v in labels.items()
                )
            ]
            if not 1 <= len(selected) <= 16 or len(selected) != deployment["spec"].get(
                "replicas", 1
            ):
                raise ValueError("source workload Pod set unstable")
            ids = {image_id(i["image_id"]) for i in state["instances"]}
            for pod in selected:
                instance = self.audit.instance(pod, name)
                live = self.audit.bindings(name, pod["spec"])
                container = next(
                    c for c in pod["spec"]["containers"] if c["name"] == name
                )
                if (
                    live != expected
                    or container["image"] != state["image"]
                    or image_id(instance["image_id"]) not in ids
                ):
                    raise ValueError("live source binding/image changed")
                token = digest({"workload": name, "instance": instance})
                selected_instances.add(token)
                if hashes and (force_hash or token not in self.verified_instances):
                    observed = bounded_read(
                        [
                            "exec",
                            instance["pod"],
                            "-c",
                            name,
                            "--",
                            "/app/backend/.venv/bin/python",
                            "-B",
                            "-c",
                            self.audit.READER,
                            json.dumps(sorted(hashes)),
                        ]
                    )
                    if observed != hashes:
                        raise ValueError(
                            "actual mounted source differs from audited content"
                        )
                    after = bounded_read(["get", "pod", instance["pod"], "-o", "json"])
                    if instance != self.audit.instance(after, name):
                        raise ValueError("source runtime changed during hashing")
                checked_now.add(token)
        # A ConfigMap update during the slower hash reads cannot be hidden by
        # the metadata cache. Re-read only those three/few source ConfigMaps.
        for name, before in list(configs.items()):
            after = bounded_read(["get", "configmap", name, "-o", "json"])
            if digest(before) != digest(after):
                raise ValueError("source ConfigMap changed during guard")
        self.verified_instances = (
            self.verified_instances | checked_now
        ) & selected_instances
        return allowed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("inputs", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--image-build-stage", action="store_true")
    args = parser.parse_args()
    if str(args.root) != "/app/backend":
        raise ValueError("only the disposable Gateway image source root is supported")

    def load(name):
        path = args.inputs / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 16 * 1024**2
        ):
            raise ValueError("unsafe build input")
        return json.loads(path.read_bytes(), object_pairs_hook=unique_object)

    components = {
        name: load(name + "-delta.json")
        for name in ("gateway", "frontend", "provisioner")
    }
    print(
        json.dumps(
            apply_sources(
                args.root,
                load("source-overlays.json"),
                components,
                check_only=args.check_only,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
