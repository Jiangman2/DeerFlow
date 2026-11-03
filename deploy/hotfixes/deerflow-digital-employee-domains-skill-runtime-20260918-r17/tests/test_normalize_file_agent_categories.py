#!/usr/bin/env python3
from __future__ import print_function

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "normalize-file-agent-categories.py"


class _Paths(object):
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.team_agents_dir = base_dir / "team_agents"
        self.agents_dir = base_dir / "agents"


def _load_script(base_dir, fail_name=None):
    allowed = types.SimpleNamespace(
        items=[
            types.SimpleNamespace(id="comprehensive-management"),
            types.SimpleNamespace(id="finance-capital"),
        ]
    )

    def safe_load(text):
        return json.loads(text)

    def writer(path):
        if fail_name is not None and path.parent.name == fail_name:
            raise OSError("simulated atomic replace failure")
        payload = json.loads(path.read_text(encoding="utf-8"))
        target = payload.get("target_category", "comprehensive-management")
        if payload.get("category_id") == target:
            return False
        payload["category_id"] = target
        temporary = path.with_name("." + path.name + ".test.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(temporary), str(path))
        return True

    modules = {
        "yaml": types.SimpleNamespace(safe_load=safe_load),
        "app": types.ModuleType("app"),
        "app.gateway": types.ModuleType("app.gateway"),
        "app.gateway.routers": types.ModuleType("app.gateway.routers"),
        "app.gateway.routers.agents": types.SimpleNamespace(
            CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES=allowed,
            _write_category_to_config_file=writer,
        ),
        "deerflow": types.ModuleType("deerflow"),
        "deerflow.config": types.ModuleType("deerflow.config"),
        "deerflow.config.paths": types.SimpleNamespace(
            get_paths=lambda: _Paths(base_dir),
        ),
    }
    previous = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        spec = importlib.util.spec_from_file_location("normalize_file_agent_categories", str(SCRIPT))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, previous
    except Exception:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        raise


def _restore_modules(previous):
    for name, value in previous.items():
        if value is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = value


class NormalizeFileAgentCategoriesTests(unittest.TestCase):
    def _write(self, base, relative, payload):
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_updates_all_three_retained_layouts_and_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            paths = [
                self._write(base, "users/u1/agents/a/config.yaml", {"name": "a", "category_id": "general", "keep": 1}),
                self._write(base, "team_agents/b/config.yaml", {"name": "b", "target_category": "finance-capital", "keep": 2}),
                self._write(base, "agents/c/config.yaml", {"name": "c", "category_id": "comprehensive-management", "keep": 3}),
            ]
            module, previous = _load_script(base)
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    module.main()
            finally:
                _restore_modules(previous)

            self.assertIn("file_agent_category_rows_seen=3", output.getvalue())
            self.assertIn("file_agent_category_rows_migrated=2", output.getvalue())
            self.assertIn("file_agent_category_normalization=PASS", output.getvalue())
            payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
            self.assertEqual([item["keep"] for item in payloads], [1, 2, 3])
            self.assertEqual(
                [item["category_id"] for item in payloads],
                ["comprehensive-management", "finance-capital", "comprehensive-management"],
            )

    def test_writer_failure_is_not_suppressed(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            self._write(base, "team_agents/fail/config.yaml", {"name": "fail", "category_id": "general"})
            module, previous = _load_script(base, fail_name="fail")
            try:
                with self.assertRaisesRegex(OSError, "simulated atomic replace failure"):
                    module.main()
            finally:
                _restore_modules(previous)


if __name__ == "__main__":
    unittest.main()
