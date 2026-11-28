"""Add the shared-space invitation redirect to the deployed Nginx template."""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


K = ["kubectl", "--kubeconfig", "/home/cnooc/.kube/config", "-n", "deer-flow"]
BLOCK = """        location = /join {
            absolute_redirect off;
            return 302 /platform/organizations?invite_code=$arg_code;
        }

"""


def kubectl(*args, payload=None):
    return subprocess.run(K + list(args), input=payload, stdout=subprocess.PIPE,
                          check=True).stdout.decode("utf-8")


def with_route(conf):
    if re.search(r"(?m)^\s*location\s+(?:=\s+)?/join\s*\{", conf):
        raise ValueError("A /join location already exists; inspect the live config.")
    anchor = re.compile(r"(?m)^[ \t]*location\s+\^~\s+/platform/\s*\{")
    if len(anchor.findall(conf)) != 1:
        raise ValueError("Expected exactly one /platform/ location.")
    return anchor.sub(lambda m: BLOCK + m.group(), conf, count=1)


def main():
    dep = json.loads(kubectl("get", "deployment/nginx", "-o", "json"))
    spec = dep["spec"]["template"]["spec"]
    container, = (c for c in spec["containers"] if c["name"] == "nginx")
    mount, = (m for m in container["volumeMounts"]
              if m["mountPath"] == "/etc/nginx/nginx.conf.template" and m.get("subPath") == "nginx.conf")
    idx, = (i for i, v in enumerate(spec["volumes"]) if v["name"] == mount["name"])
    old = spec["volumes"][idx]["configMap"]["name"]
    cm = json.loads(kubectl("get", "cm", old, "-o", "json"))
    template = cm["data"]["nginx.conf"]
    candidate = with_route(template)
    # Validate against the running container's rendered upstreams (not Docker placeholders).
    mounted = kubectl("exec", "deployment/nginx", "-c", "nginx", "--",
                      "cat", "/etc/nginx/nginx.conf.template")
    if mounted != template:
        raise ValueError("Mounted template differs from ConfigMap; check rollout first.")
    rendered = kubectl("exec", "deployment/nginx", "-c", "nginx", "--", "cat", "/tmp/nginx.conf")
    kubectl("exec", "-i", "deployment/nginx", "-c", "nginx", "--", "sh", "-ec",
            'f=$(mktemp /tmp/invite-test.XXXXXX); trap \'rm -f "$f"\' EXIT; '
            'cat > "$f"; nginx -t -c "$f"', payload=with_route(rendered).encode())
    manifest = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"generateName": "deer-flow-nginx-invite-"},
                "data": dict(cm["data"], **{"nginx.conf": candidate})}
    if cm.get("binaryData"):
        manifest["binaryData"] = cm["binaryData"]
    path = "/spec/template/spec/volumes/{}/configMap/name".format(idx)
    new = kubectl("create", "-f", "-", "-o", "jsonpath={.metadata.name}",
                  payload=json.dumps(manifest).encode()).strip()
    folder = Path(tempfile.mkdtemp(prefix="nginx-invite-", dir="/home/cnooc"))
    undo = folder / "rollback.json"
    undo.write_text(json.dumps([{"op": "test", "path": path, "value": new},
                                {"op": "replace", "path": path, "value": old}]))
    print("Rollback command (original ConfigMap retained):", flush=True)
    print(" ".join(K) + " patch deployment/nginx --type=json --patch-file=" + str(undo), flush=True)
    patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": dep["metadata"]["resourceVersion"]},
             {"op": "test", "path": path, "value": old},
             {"op": "replace", "path": path, "value": new}]
    print(kubectl("patch", "deployment/nginx", "--type=json", "-p", json.dumps(patch)).strip(), flush=True)
    subprocess.run(K + ["rollout", "status", "deployment/nginx", "--timeout=5m"], check=True)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit("STOP: " + str(exc))
