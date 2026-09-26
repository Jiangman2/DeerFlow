"""Image-build import closure probe. No application lifespan is started.

Run with BuildKit --network=none, no production volumes or credentials. Import
failures must stop the build; do not install dependencies or mock imports here.
This does not assert SSO, database, shared-storage or business acceptance.
"""

import importlib
import json
import sys
from pathlib import Path

from apply_image_delta import prepare, sha, unique_object


def verify(root, manifest):
    prepared = prepare(root, manifest)
    for _, content, before, _ in prepared:
        if before != sha(content):
            raise ValueError("Image delta not applied")
    loaded = []
    for entry in manifest["entries"]:
        relative = entry["path"]
        name = (
            relative.removeprefix("packages/harness/")[:-3]
            .replace("/", ".")
            .removesuffix(".__init__")
        )
        module = importlib.import_module(name)
        expected = (Path(root) / relative).resolve(strict=True)
        if Path(module.__file__).resolve(strict=True) != expected:
            raise ValueError("Import resolved outside reviewed image source: " + name)
        loaded.append(name)
    return {
        "employee_image_imports": "PASS",
        "modules": loaded,
        "business_acceptance": "NOT_RUN",
        "ready_for_rollout": False,
    }


if __name__ == "__main__":
    root, manifest_path = sys.argv[1:]
    document = json.loads(
        Path(manifest_path).read_bytes(), object_pairs_hook=unique_object
    )
    print(json.dumps(verify(root, document), sort_keys=True))
