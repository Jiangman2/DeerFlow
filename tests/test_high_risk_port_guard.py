"""Safety checks for the operator-run port guard; no host rules are changed."""
import contextlib
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guard_high_risk_ports.py"
spec = importlib.util.spec_from_file_location("port_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def policy():
    return {"reviewed": True, "interfaces": ["eth0"], "services": [
        {"name": "ssh", "protocol": "tcp", "ports": [22], "sources": ["192.0.2.10/32"]},
        {"name": "nodeport", "protocol": "tcp", "ports": [32026], "sources": ["192.0.2.20/32", "2001:db8::20/128"]},
        {"name": "unused", "protocol": "udp", "ports": [111], "sources": []},
    ]}


class ValidationTests(unittest.TestCase):
    def test_requires_explicit_review(self):
        value = policy()
        value["reviewed"] = False
        with self.assertRaises(ValueError):
            guard.validate(value)

    def test_rejects_unrestricted_sources_and_duplicate_ports(self):
        for source in ["0.0.0.0/0", "::/0"]:
            value = policy()
            value["services"][0]["sources"] = [source]
            with self.assertRaises(ValueError):
                guard.validate(value)
        value = policy()
        value["services"].append(copy.deepcopy(value["services"][0]))
        with self.assertRaises(ValueError):
            guard.validate(value)

    def test_rejects_bad_schema_and_injection(self):
        values = [[], {"reviewed": True, "interfaces": "eth0"}, policy(), policy(), policy(), policy()]
        values[2]["interfaces"] = ['eth0"; flush ruleset']
        values[3]["services"][0]["ports"] = [True]
        values[4]["services"][0]["sources"] = [123]
        values[5]["services"] = [None]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard.validate(value)

    def test_render_precedes_dnat_and_limits_local_interfaces(self):
        rules = guard.render(policy(), "123-45")
        self.assertIn("hook prerouting priority -150", rules)
        self.assertIn('iifname != { "eth0" } return', rules)
        self.assertIn("fib daddr type != local return", rules)
        self.assertIn("ip6 saddr 2001:db8::20/128 tcp dport { 32026 } counter return", rules)
        self.assertIn('udp dport { 111 } counter drop', rules)
        self.assertIn("deerflow-owner-123-45", rules)
        self.assertNotIn("flush", rules)

    def test_ssh_checks_actual_client_and_server_port(self):
        for connection in ["192.0.2.99 50000 192.0.2.2 22", "192.0.2.10 50000 192.0.2.2 2222", ""]:
            with patch.dict(os.environ, {"SSH_CONNECTION": connection}), self.assertRaises(ValueError):
                guard.check_ssh(policy())
        with patch.dict(os.environ, {"SSH_CONNECTION": "192.0.2.10 50000 192.0.2.2 22"}):
            guard.check_ssh(policy())
        with patch.dict(os.environ, {"SSH_CONNECTION": ""}):
            guard.check_ssh(policy(), console_access=True)


class TransactionTests(unittest.TestCase):
    def exercise_apply(self, fail_on=None, exists=None):
        calls = []
        def execute(argv, check=True):
            calls.append(argv)
            if fail_on and fail_on(argv):
                raise RuntimeError("injected failure")
            return subprocess.CompletedProcess(argv, 0, "backup", "")
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp) / "state"
            stack.enter_context(patch.object(guard, "STATE_ROOT", root))
            stack.enter_context(patch.object(guard, "require_root"))
            stack.enter_context(patch.object(guard, "locked", side_effect=contextlib.nullcontext))
            stack.enter_context(patch.object(guard, "check_ssh"))
            stack.enter_context(patch.object(guard.shutil, "which", return_value="/usr/bin/tool"))
            stack.enter_context(patch.object(Path, "exists", return_value=True))
            stack.enter_context(patch.object(guard, "table_exists", side_effect=exists or [False]))
            stack.enter_context(patch.object(guard, "command", side_effect=execute))
            if fail_on:
                with self.assertRaises(RuntimeError):
                    guard.apply(policy(), 10)
            else:
                guard.apply(policy(), 10)
            states = list(root.glob("*/state.json"))
            try:
                data = json.loads(states[0].read_text()) if states else None
            except FileNotFoundError:
                data = None
        return calls, data

    def test_preflight_and_timer_precede_rule_application(self):
        calls, data = self.exercise_apply()
        preflight = next(i for i, x in enumerate(calls) if x[:3] == ["nft", "-c", "-f"])
        timer = next(i for i, x in enumerate(calls) if x[0] == "systemd-run")
        apply = next(i for i, x in enumerate(calls) if x[:2] == ["nft", "-f"])
        self.assertLess(preflight, timer)
        self.assertLess(timer, apply)
        self.assertEqual(data["status"], "active")

    def test_timer_failure_cannot_apply_rules(self):
        calls, data = self.exercise_apply(lambda x: x[0] == "systemd-run")
        self.assertFalse(any(x[:2] == ["nft", "-f"] for x in calls))
        self.assertEqual(data["status"], "prepared")

    def test_nft_preflight_failure_cannot_arm_timer_or_apply(self):
        calls, data = self.exercise_apply(lambda x: x[:3] == ["nft", "-c", "-f"])
        self.assertIsNone(data)
        self.assertFalse(any(x[0] == "systemd-run" or x[:2] == ["nft", "-f"] for x in calls))

    def test_apply_failure_keeps_existing_rules_and_marks_rollback(self):
        calls, data = self.exercise_apply(lambda x: x[:2] == ["nft", "-f"], [False, False])
        self.assertEqual(data["status"], "rolled_back")
        self.assertFalse(any("flush" in x for x in calls))

    def test_old_run_cannot_remove_new_rules(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"nftables": [
            {"rule": {"comment": "deerflow-owner-999-9"}}]}), "")
        with patch.object(guard, "command", return_value=result), self.assertRaises(RuntimeError):
            guard.check_owner({"owner": "123-45"})

    def test_confirm_requires_external_verification(self):
        with patch.object(guard, "locked", side_effect=contextlib.nullcontext), \
                patch.object(guard, "read_state", return_value=(Path("/run"), {"status": "active"})), \
                patch.object(guard, "command") as execute, self.assertRaises(ValueError):
            guard.finish("confirm", "/run", False)
        execute.assert_not_called()

    def test_rollback_deletes_only_owned_table(self):
        data = {"status": "active", "owner": "123-45", "unit": "deerflow-port-guard-123-45"}
        with patch.object(guard, "locked", side_effect=contextlib.nullcontext), \
                patch.object(guard, "read_state", return_value=(Path("/run"), data)), \
                patch.object(guard, "table_exists", return_value=True), \
                patch.object(guard, "check_owner") as owner, \
                patch.object(guard, "save"), patch.object(guard, "command") as execute:
            guard.finish("rollback", "/run", False)
        owner.assert_called_once_with(data)
        execute.assert_any_call(["nft", "delete", "table", "inet", guard.TABLE])
        self.assertEqual(data["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
