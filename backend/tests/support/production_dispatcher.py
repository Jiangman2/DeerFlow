"""Load the separately released Dispatcher, not Gateway's dormant old copy."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def load_dispatcher():
    root = Path(__file__).resolve().parents[3]
    source = root / "release/production-source-20261004/gateway-run-dispatcher/run_placement.py"
    manifest = json.loads(source.with_name("manifest.json").read_text(encoding="utf-8"))
    raw = source.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == manifest["sha256"], "Dispatcher source lock mismatch"
    name = "_production_gateway_run_dispatcher"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, source)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    module = sys.modules[name]
    assert module.OWNER_ROUTE_VERSION == manifest["owner_route_version"]
    return module
