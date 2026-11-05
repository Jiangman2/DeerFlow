"""Linux 3.7 filesystem/failure qualification of scoped reference migration."""

import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

FOLDER = Path(__file__).parent


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, FOLDER / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = module("migration", "migrate-data-qa-agent-references.py")
builder = module("reference_builder", "package-data-qa-reference-activation.py")


def load_release():
    n = {"__name__": "test_release"}
    exec(compile(Path(os.environ["JOINT_ACTIVATION_SCRIPT"]).read_bytes(), "<r2>", "exec"), n)
    return n


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = []
        self.originals = []
        self.pins = dict(files={}, files_checked=2, inventory_sha256="inventory")
        for i in range(2):
            p = self.root / "users" / ("user%d" % i) / "agents" / "well" / "SOUL.md"
            p.parent.mkdir(parents=True)
            raw = ("业务要求保留\r\n- 表及井 datalake_detail_qa\r\n- 指标 datalake_metrics_qa\r\n尾部不变").encode()
            p.write_bytes(raw)
            p.chmod(0o644)
            h = m.sha(str(p.relative_to(self.root)).encode())
            self.pins["files"][h] = dict(bytes=len(raw), sha256=m.sha(raw), lines=[[2, "datalake_detail_qa", m.sha(raw.splitlines()[1])], [3, "datalake_metrics_qa", m.sha(raw.splitlines()[2])]])
            self.paths.append(p)
            self.originals.append(raw)
        self.operation = "a" * 32

    def tearDown(self):
        self.temp.cleanup()

    def scanner(self, root, observer):
        for p in self.paths:
            observer(p.relative_to(root), p.read_bytes(), p.stat())
        return dict(inventory_sha256="inventory", files_checked=2)

    def invoke(self, action="apply"):
        return m.migration(self.root, self.pins, self.scanner, self.operation, action)

    def test_exact_tokens_and_newlines_preserve_business_prose(self):
        before = self.originals[0]
        after = m.transform(before, next(iter(self.pins["files"].values())))
        expected = before.replace(b"datalake_detail_qa", b"datalake_data_qa").replace(b"datalake_metrics_qa", b"datalake_data_qa")
        self.assertEqual(after, expected + b"\r\n\r\n" + m.NOTE.encode() + b"\r\n")

    def test_both_backups_metadata_verify_and_restore(self):
        self.assertEqual(self.invoke()["status"], "MIGRATION_VERIFIED")
        self.assertEqual(self.invoke("verify")["files"], 2)
        backup = self.root / m.BACKUP_NAME / self.operation
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        for p, raw in zip(self.paths, self.originals):
            h = m.sha(str(p.relative_to(self.root)).encode())
            b = backup / (h + ".original")
            self.assertEqual(b.read_bytes(), raw)
            self.assertEqual(b.stat().st_mode & 0o777, 0o600)
            self.assertEqual(p.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.invoke("restore")["status"], "RESTORED")
        self.assertEqual([p.read_bytes() for p in self.paths], self.originals)

    def test_changed_hash_line_and_extra_reference_fail(self):
        pin = next(iter(self.pins["files"].values()))
        with self.assertRaisesRegex(ValueError, "FILE_HASH"):
            m.transform(self.originals[0] + b"x", pin)
        altered = copy.deepcopy(pin)
        altered["lines"][0][2] = "0" * 64
        with self.assertRaisesRegex(ValueError, "LINE_CHANGED"):
            m.transform(self.originals[0], altered)
        extra = self.originals[0] + b"\ndatalake_metric_qa"
        altered.update(bytes=len(extra), sha256=m.sha(extra), lines=pin["lines"])
        with self.assertRaisesRegex(ValueError, "RETIRED_REFERENCE_REMAINS"):
            m.transform(extra, altered)

    def test_inventory_drift_no_original_changes(self):
        self.pins["inventory_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "INVENTORY_CHANGED"):
            self.invoke()
        self.assertEqual([p.read_bytes() for p in self.paths], self.originals)

    def test_symlink_and_hardlink_rejected(self):
        p = self.paths[0]
        other = p.parent / "original"
        p.rename(other)
        p.symlink_to(other)
        with self.assertRaises(OSError):
            self.invoke()
        p.unlink()
        os.link(str(other), str(p))
        with self.assertRaisesRegex(ValueError, "UNEXPECTED_AFFECTED_FILE"):
            self.invoke()

    def test_mode_change_rejected(self):
        self.paths[0].chmod(0o600)
        with self.assertRaisesRegex(ValueError, "UNEXPECTED_AFFECTED_FILE"):
            self.invoke()

    def test_repeat_apply_refused(self):
        self.invoke()
        with self.assertRaises(ValueError):
            self.invoke()

    def test_partial_second_write_failure_recoverable(self):
        real = m.replace_checked
        calls = []

        def fail_second(*args):
            calls.append(True)
            if len(calls) == 2:
                raise OSError("synthetic")
            return real(*args)

        with patch.object(m, "replace_checked", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertNotEqual(self.paths[0].read_bytes(), self.originals[0])
        self.assertEqual(self.paths[1].read_bytes(), self.originals[1])
        self.assertEqual(self.invoke("restore")["status"], "RESTORED")
        self.assertEqual([p.read_bytes() for p in self.paths], self.originals)

    def test_concurrent_edit_not_overwritten_on_restore(self):
        self.invoke()
        self.paths[0].write_bytes(b"operator edit")
        with self.assertRaisesRegex(ValueError, "CONCURRENT_EDIT_RESTORE_REFUSED"):
            self.invoke("restore")
        self.assertEqual(self.paths[0].read_bytes(), b"operator edit")

    def test_drift_immediately_before_replace_preserved(self):
        p = self.paths[0]
        rootfd = m.directory(None, self.root)
        fd = m.parent_fd(rootfd, p.relative_to(self.root))
        try:
            raw, info = m.read_at(fd, "SOUL.md")
            p.write_bytes(b"operator edit")
            with self.assertRaisesRegex(ValueError, "CONCURRENT_AGENT_EDIT"):
                m.replace_checked(fd, raw, info, b"migration")
            self.assertEqual(p.read_bytes(), b"operator edit")
        finally:
            os.close(fd)
            os.close(rootfd)

    def test_backup_failure_precedes_any_target_write(self):
        real = m.write_new

        def fail_backup(fd, name, raw, owner=None):
            if name.endswith('.original'):
                raise OSError('synthetic backup failure')
            return real(fd, name, raw, owner)

        with patch.object(m, 'write_new', side_effect=fail_backup):
            with self.assertRaises(OSError):
                self.invoke()
        self.assertEqual([p.read_bytes() for p in self.paths], self.originals)

    def test_modified_backup_cannot_restore(self):
        self.invoke()
        h = next(iter(self.pins['files']))
        backup = self.root / m.BACKUP_NAME / self.operation / (h + '.original')
        backup.write_bytes(b'corrupt backup')
        with self.assertRaisesRegex(ValueError, 'FILE_HASH_CHANGED'):
            self.invoke('restore')

    def test_packaged_real_scanner_and_shared_readback(self):
        probe = load_release()['REFERENCE_PROBE']
        scope = {}
        exec(compile(probe[:probe.index('\nimport contextlib,io,signal,sys')], '<real-probe>', 'exec'), scope)
        scanner = scope['agent_references']
        self.pins['inventory_sha256'] = scanner(self.root)['inventory_sha256']
        result = scope['migration'](self.root, self.pins, scanner, self.operation, 'apply')
        self.assertEqual(result['status'], 'MIGRATION_VERIFIED')
        self.assertEqual(scanner(self.root)['affected_files'], 0)
        self.assertEqual(scanner(self.root)['files_checked'], 2)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.n = load_release()

    def test_actual_four_line_pins_and_no_runtime_image_changes(self):
        self.assertEqual(sum(len(v["lines"]) for v in self.n["REFERENCE_PINS"]["files"].values()), 4)
        self.n["preload_gate"]()
        self.n["candidate_receipt_gate"](self.n["ACCEPTANCE"])
        self.assertEqual(self.n["MD"], "sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5")

    def wrapper_failure(self, patch_requested=False, returned=True, drift=False):
        class C:
            report = dict(agent_reference_apply_returned=returned, agent_reference_mutation_requested=True, patch_requested=patch_requested, patch_may_have_applied=patch_requested)
            reference_pod = {}

            def get(self, role):
                return {"drift": drift}

        c = C()
        calls = []
        self.n["execute_with_reference_hooks"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("original"))
        self.n["binding"] = lambda d: {} if drift else self.n["CURRENT_BINDING"]
        self.n["reference_call"] = lambda c, p, action: calls.append(action)
        with self.assertRaisesRegex(RuntimeError, "original"):
            self.n["execute"](c, {})
        return c.report, calls

    def test_prepatch_failure_restores_only_at_unchanged_baseline(self):
        report, calls = self.wrapper_failure()
        self.assertEqual(calls, ["restore"])
        self.assertTrue(report["agent_reference_restored"])

    def test_uncertain_patch_never_restores_old_tool_names(self):
        report, calls = self.wrapper_failure(patch_requested=True)
        self.assertEqual(calls, [])
        self.assertTrue(report["agent_reference_recovery_required"])

    def test_unknown_exec_never_races_with_restore(self):
        report, calls = self.wrapper_failure(returned=False)
        self.assertEqual(calls, [])
        self.assertTrue(report["agent_reference_recovery_required"])

    def test_changed_gateway_refuses_restore(self):
        report, calls = self.wrapper_failure(drift=True)
        self.assertEqual(calls, [])
        self.assertTrue(report["agent_reference_recovery_required"])

    def test_integrated_flow_keeps_one_paired_patch_and_70_acceptances(self):
        fixtures = module('joint_fixture', 'test_data_qa_glm_activation.py')
        c = fixtures.JointFake(self.n)
        actions = []

        def start(controller, pods):
            actions.append('apply')
            controller.reference_pod = pods[0]
            controller.report.update(agent_reference_apply_returned=True, agent_reference_mutation_requested=True)

        self.n['start_reference_migration'] = start
        self.n['reference_call'] = lambda controller, pod, action: actions.append(action)
        self.n['execute'](c, {})
        self.assertEqual(actions, ['apply', 'verify', 'verify', 'verify', 'verify'])
        self.assertEqual(len(c.patches), 1)
        self.assertEqual(c.remote_count, 72)
        self.assertEqual(c.report['data_qa_runtime_accepted'], 70)
        self.assertTrue(c.report['stage_passed'])

    def test_actual_flow_inventory_failure_restores_before_patch(self):
        fixtures = module('joint_fixture_failure', 'test_data_qa_glm_activation.py')
        c = fixtures.JointFake(self.n)
        c.old_reference = True
        actions = []

        def start(controller, pods):
            controller.reference_pod = pods[0]
            controller.report.update(agent_reference_apply_returned=True, agent_reference_mutation_requested=True)

        self.n['start_reference_migration'] = start
        self.n['reference_call'] = lambda controller, pod, action: actions.append(action)
        with self.assertRaises(self.n['Refused']):
            self.n['execute'](c, {})
        self.assertEqual(actions, ['restore'])
        self.assertEqual(c.patches, [])


if __name__ == "__main__":
    unittest.main()
