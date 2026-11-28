"""Resume the interrupted R3 commission, preserving the ledger and cold assignments."""
import hashlib
import json

R3 = "3b3449647141cb8a"
OLD_LAYOUT_SHA = "a9e3dd1e021636814a34c0993915794cb85e62b42c182b4b5feb707d80a253bc"
SLOT = "df-warm-slot-000"
FAILED_UID = "f632abe1-5441-4b76-8eea-335d11d8dde6"


def preparing(ops, db):
    row = db["slots"].get(SLOT, {})
    ops.require(db.get("schema") == 1 and db.get("release") == R3 and db.get("enabled") is False
                and not db.get("verified") and db.get("commission") and db["commission"] == db.get("fixture")
                and set(db["slots"]) == {SLOT} and set(row) == {"generation", "phase"}
                and row["phase"] == "preparing", "R3 no longer has the expected unassigned first slot")
    return row


def resume(ops, z):
    cm = ops.read("cm", ops.LEDGER)
    if not cm or json.loads(cm["data"]["registry"]).get("release") != R3:
        return
    db = json.loads(cm["data"]["registry"])
    row = preparing(ops, db)
    uid = cm["metadata"]["uid"]
    ops.require(cm["metadata"].get("labels", {}).get(ops.LABEL) == "v1", "Unexpected R3 registry")
    pod = ops.read("pod", SLOT)
    meta = (pod or {}).get("metadata", {})
    labels = meta.get("labels", {})
    ops.require(meta.get("uid") == FAILED_UID and not meta.get("deletionTimestamp")
                and labels.get("df-warm-role") == "slot" and labels.get("df-pool-registry") == uid
                and labels.get("df-pool-generation") == row["generation"], "Failed slot identity changed")
    ops.require(not ops.read("service", "df-w-" + FAILED_UID) and not ops.read("pod", "df-mount-" + FAILED_UID),
                "Unexpected binding on the first R3 slot")
    manifest = json.loads(z.read("manifest.json"))
    for name in ops.WORKLOADS[1:]:
        d = ops.read("deployment", name)
        ops.require(ops.LABEL not in d["spec"]["template"]["metadata"].get("annotations", {}) and
                    d["metadata"]["generation"] == manifest[name + "/" + name]["generation"],
                    "Unexpected deployment during R3 recovery: " + name)

    base = manifest["provisioner/provisioner"]
    data = ops.items(z, "provisioner", base)
    d = ops.read("deployment", "provisioner")
    tpl = d["spec"]["template"]
    stamp = tpl["metadata"]["annotations"].get(ops.LABEL)
    old_name = "df-pool-" + R3 + "-provisioner"
    new_name = "df-pool-" + ops.TAG + "-provisioner"
    volumes = tpl["spec"]["volumes"]
    indices = [i for i, vol in enumerate(volumes) if vol["name"] == "df-pool-code"]
    ops.require(len(indices) == 1 and stamp in (R3, ops.TAG) and
                volumes[indices[0]]["configMap"]["name"] == (old_name if stamp == R3 else new_name),
                "Unexpected Provisioner overlay")
    if stamp == R3:
        original = ops.read("cm", old_name)
        expected = {k: v[1] for k, v in data.items()}
        old_files = original["data"]
        ops.require(original.get("immutable") and set(old_files) == set(expected)
                    and all(old_files[k] == v for k,v in expected.items() if k != "scoped_layout.py")
                    and hashlib.sha256(old_files["scoped_layout.py"].encode()).hexdigest() == OLD_LAYOUT_SHA,
                    "Provisioner R3 source no longer matches")
        ops.verify("provisioner", d, base, {k: (data[k][0], v) for k,v in old_files.items()})
        ops.check_imports("provisioner", data)
        ops.ensure("ConfigMap", new_name, immutable=True, data=expected)
        ops.kub("patch", "deployment", "provisioner", "--type=json", "-p", json.dumps([
            {"op": "test", "path": "/metadata/resourceVersion", "value": d["metadata"]["resourceVersion"]},
            {"op": "replace", "path": f"/spec/template/spec/volumes/{indices[0]}/configMap/name", "value": new_name},
            {"op": "replace", "path": "/spec/template/metadata/annotations/deerflow.io~1global-pool-release",
             "value": ops.TAG}]), "-o", "name", parse=False)
    def deployed():
        current = ops.read("deployment", "provisioner")
        ops.require(current["spec"]["template"]["metadata"]["annotations"].get(ops.LABEL) == ops.TAG,
                    "Concurrent Provisioner rollout")
        return ops.settled(current)
    ops.wait_for(deployed, 7200, "WAITING_FOR_FIXED_PROVISIONER")
    ops.verify("provisioner", ops.read("deployment", "provisioner"), base, data)
    def replace_failed(current):
        ops.require(preparing(ops, current) == row and current["fixture"] == db["fixture"],
                    "R3 commissioning identity changed")
        current["slots"][SLOT].update(phase="retiring", uid=FAILED_UID)
        current["release"] = ops.TAG
    ops.edit_registry(replace_failed)
    ops.report("R3_NEUTRAL_SLOT_REPLACEMENT_REQUESTED", pod=SLOT, uid=FAILED_UID)
