"""Offline tests for the three-node mount guard; no host services are touched."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "enterprise_redis_mount_guard.py"
SPEC = importlib.util.spec_from_file_location("enterprise_redis_mount_guard", SOURCE)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class MountGuardTest(TestCase):
    def test_pilot_only_and_exact_disk(self) -> None:
        def fake_run(*argv: str, **_kwargs: object) -> str:
            if argv[:2] == ("systemctl", "--version"):
                return "systemd 239\n+PAM"
            if argv[0] == "findmnt":
                return "/dev/vdb1 xfs"
            if argv[0] == "blkid":
                return guard.NODE_UUIDS[guard.PILOT_NODE]
            if argv[:3] == ("containerd", "config", "dump"):
                return "root = '/mnt/data/containerd'"
            if argv[:2] == ("systemctl", "is-active"):
                return "active"
            raise AssertionError(argv)

        with TemporaryDirectory() as temp, patch.object(guard.socket, "gethostname", return_value=guard.PILOT_NODE), patch.object(guard.os, "geteuid", return_value=0, create=True), patch.object(guard, "read_fstab", return_value=True), patch.object(guard, "run", side_effect=fake_run), patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch.object(guard, "property_map", return_value={"ActiveState": "active", "MainPID": "123", "UnitFileState": "enabled"}):
            result = guard.preflight()
            self.assertEqual(result["node"], guard.PILOT_NODE)
        with patch.object(guard.socket, "gethostname", return_value="ecs-zdz-pro-9999"):
            with self.assertRaisesRegex(RuntimeError, "unsupported node"):
                guard.preflight()

    def test_disabled_kubelet_blocks_pilot(self) -> None:
        with TemporaryDirectory() as temp, patch.object(guard.socket, "gethostname", return_value=guard.PILOT_NODE), patch.object(guard.os, "geteuid", return_value=0, create=True), patch.object(guard, "read_fstab", return_value=True), patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch.object(guard, "property_map", side_effect=lambda unit: {"ActiveState": "active", "MainPID": "123", "UnitFileState": "disabled" if unit == "kubelet" else "enabled"}):
            # Isolate the boot-state gate from device checks.
            with patch.object(guard, "run", side_effect=["systemd 239", "/dev/vdb1 xfs", guard.NODE_UUIDS[guard.PILOT_NODE], "root = '/mnt/data/containerd'", "active"]):
                with self.assertRaisesRegex(RuntimeError, "kubelet is not enabled for boot"):
                    guard.preflight()

    def test_each_node_uses_its_own_disk_uuid(self) -> None:
        for node in ("ecs-zdz-pro-0014", "ecs-zdz-pro-0016"):
            disk_uuid = guard.NODE_UUIDS[node]
            with self.subTest(node=node), TemporaryDirectory() as temp, patch.object(guard.socket, "gethostname", return_value=node), patch.object(guard.os, "geteuid", return_value=0, create=True), patch.object(guard, "read_fstab", return_value=True) as fstab, patch.object(guard, "run", side_effect=["systemd 239", "/dev/vdb1 xfs", disk_uuid, "root = '/mnt/data/containerd'", "active"]), patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch.object(guard, "property_map", return_value={"ActiveState": "active", "MainPID": "123", "UnitFileState": "enabled"}):
                self.assertEqual(guard.preflight()["disk_uuid"], disk_uuid)
                fstab.assert_called_once_with(disk_uuid)

    def test_apply_requires_explicit_local_node(self) -> None:
        with patch.object(guard.sys, "argv", ["guard", "--apply"]), patch.object(guard, "preflight", return_value={"node": guard.PILOT_NODE}), patch.object(guard, "apply") as apply, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(guard.main(), 1)
            apply.assert_not_called()
            self.assertIn("--node", output.getvalue())

    def test_preflight_does_not_claim_kubernetes_or_redis_state(self) -> None:
        with TemporaryDirectory() as temp, patch.object(guard.sys, "argv", ["guard"]), patch.object(guard, "preflight", return_value={"node": guard.PILOT_NODE}), patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(guard.main(), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["kubernetes_state"], "NOT_CHECKED")
            self.assertEqual(report["redis_state"], "NOT_CHECKED")
            self.assertEqual(report["installed_units_before"], [])
            self.assertNotIn("node_uncordoned", report)
            self.assertNotIn("redis_deployed", report)

    def test_check_rejects_partial_installation(self) -> None:
        with TemporaryDirectory() as temp, patch.object(guard.sys, "argv", ["guard"]), patch.object(guard, "preflight", return_value={"node": guard.PILOT_NODE}), patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch("sys.stdout", new_callable=io.StringIO) as output:
            installed = guard.dropin_path("containerd")
            installed.parent.mkdir(parents=True)
            installed.write_text(guard.DROPIN, encoding="utf-8")
            self.assertEqual(guard.main(), 1)
            self.assertIn("partial mount-guard installation", output.getvalue())

    def test_apply_loads_dropins_without_restart(self) -> None:
        calls: list[tuple[str, ...]] = []

        def fake_run(*argv: str, **_kwargs: object) -> str:
            calls.append(argv)
            return ""

        with TemporaryDirectory() as temp, patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch.object(guard, "run", side_effect=fake_run), patch.object(guard, "verify_loaded"):
            created = guard.apply({"service_pids": {"containerd": "1", "kubelet": "2"}})
            self.assertEqual(len(created), 2)
            for path in created:
                self.assertEqual(Path(path).read_text(encoding="utf-8"), guard.DROPIN)
            self.assertEqual(calls, [("systemctl", "daemon-reload")])
            self.assertEqual(guard.apply({"service_pids": {}}), [])
            self.assertEqual(calls, [("systemctl", "daemon-reload")] * 2)

    def test_failed_verification_removes_only_new_files(self) -> None:
        with TemporaryDirectory() as temp, patch.object(guard, "dropin_path", side_effect=lambda unit: Path(temp) / unit / guard.DROPIN_NAME), patch.object(guard, "run") as run, patch.object(guard, "verify_loaded", side_effect=RuntimeError("bad dependency")):
            existing = guard.dropin_path("containerd")
            existing.parent.mkdir(parents=True)
            existing.write_text(guard.DROPIN, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "bad dependency"):
                guard.apply({"service_pids": {}})
            self.assertTrue(existing.exists())
            self.assertFalse(guard.dropin_path("kubelet").exists())
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    main()
