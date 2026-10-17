import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import stage_nfs as m


class Fixture(unittest.TestCase):
    def setUp(self):
        # /qualify is root-owned 0700 tmpfs, never /tmp's writable ancestor.
        self.tmp = tempfile.TemporaryDirectory(dir='/qualify')
        self.base = Path(self.tmp.name)
        (self.base / 'data').mkdir(mode=0o755)
        (self.base / 'etc').mkdir(mode=0o755)
        (self.base / 'varlib').mkdir(mode=0o755)
        values = dict(MOUNT=self.base/'data', ROOT=self.base/'data/new',
                      EXPORTS=self.base/'etc/exports', EXPORT_DIR=self.base/'etc/exports.d',
                      CONFIG=self.base/'etc/exports.d/test.exports', STATE=self.base/'varlib/state', LAST={})
        self.patches = [patch.object(m, key, value) for key, value in values.items()]
        for p in self.patches:
            p.start()
        m.EXPORTS.write_bytes(b'/old/home 192.168.111.0/24(rw,sync,no_root_squash)\n')
        m.EXPORTS.chmod(0o644)
        self.exports = {('/old/home', '192.168.111.0/24'): ('no_root_squash', 'rw', 'sync')}
        self.original = copy.deepcopy(self.exports)
        self.command_calls = []
        self.mutation_number = 0
        self.fail_export = None
        self.interrupt_export = None
        self.drift_after = None

        def command(argv, *args, **kwargs):
            self.command_calls.append(argv)
            if argv == ['/mock/exportfs', '-s']:
                return ''.join(path + ' ' + client + '(' + ','.join(opts) + ')\n'
                               for (path, client), opts in sorted(self.exports.items())).encode()
            self.assertEqual(argv[:4], ['/mock/exportfs', '-i', '-o', m.OPTIONS])
            self.assertEqual(len(argv), 5)
            client, path = argv[4].split(':', 1)
            self.assertIn((path, client), m.keys())
            self.mutation_number += 1
            self.assertTrue((m.STATE / ('EXPORT-{}-REQUESTED.json'.format(self.mutation_number))).is_file())
            if self.interrupt_export == self.mutation_number:
                raise KeyboardInterrupt()
            self.exports[(path, client)] = tuple(sorted(m.OPTIONS.split(',') + ['hide', 'wdelay']))
            if self.fail_export == self.mutation_number:
                raise m.Failure('uncertain_export_after_send')
            if self.drift_after == self.mutation_number:
                self.exports[('/old/home', '192.168.111.0/24')] = ('ro',)
            return b''

        self.env_patch = patch.object(m, 'environment', return_value=(dict(exportfs='/mock/exportfs'), dict(available_bytes=999)))
        self.cmd_patch = patch.object(m, 'bounded_run', side_effect=command)
        self.env_patch.start()
        self.cmd_patch.start()

    def tearDown(self):
        self.cmd_patch.stop()
        self.env_patch.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_stage(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = m.run_once()
        return rc, output.getvalue()

    def status(self):
        output = io.StringIO()
        with patch.object(m.socket, 'gethostname', return_value=m.HOST), \
                patch.object(m, 'executable', return_value='/mock/exportfs'), \
                contextlib.redirect_stdout(output):
            m.status()
        return json.loads(output.getvalue())

    def test_success_exact_scope(self):
        original = m.EXPORTS.read_bytes()
        rc, output = self.run_stage()
        self.assertEqual(rc, 0, output)
        self.assertEqual(self.mutation_number, 4)
        self.assertEqual(m.EXPORTS.read_bytes(), original)
        self.assertEqual(self.exports[('/old/home', '192.168.111.0/24')], self.original[('/old/home', '192.168.111.0/24')])
        self.assertEqual(set(self.exports), set(self.original) | m.keys())
        for child in m.CHILDREN:
            s = (m.ROOT / child).lstat()
            self.assertEqual((s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)), (1000, 1000, 0o770))
        self.assertEqual(stat.S_IMODE(m.ROOT.stat().st_mode), 0o755)
        result = json.loads((m.STATE / 'RESULT.json').read_bytes())
        self.assertFalse(result['ready_to_scale'])
        self.assertFalse(result['ready_to_migrate'])
        self.assertFalse(result['business_data_copied'])
        self.assertEqual(result['cross_node_io'], 'NOT_TESTED')

    def test_status_after_success_has_no_mutations(self):
        self.run_stage()
        before = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in m.STATE.iterdir()}
        n = self.mutation_number
        result = self.status()
        after = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in m.STATE.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(self.mutation_number, n)
        self.assertFalse(result['files_modified'])
        self.assertEqual(len(result['target_exports']), 4)

    def test_status_before_run_does_not_create_state(self):
        value = self.status()
        self.assertFalse(m.STATE.exists())
        self.assertFalse(m.ROOT.exists())
        self.assertEqual(value['receipts'], {})

    def test_repeat_blocked_before_preflight(self):
        self.run_stage()
        with patch.object(m, 'preflight', side_effect=AssertionError('should not run')):
            with self.assertRaisesRegex(m.Failure, 'previous_attempt'):
                m.run_once()

    def test_uncertain_export_not_replayed(self):
        self.fail_export = 2
        rc, _ = self.run_stage()
        self.assertEqual(rc, 1)
        self.assertEqual(self.mutation_number, 2)
        self.assertTrue((m.STATE/'EXPORT-2-REQUESTED.json').exists())
        self.assertFalse((m.STATE/'EXPORT-2-ACK.json').exists())
        self.assertTrue((m.STATE/'FAILED.json').exists())
        value = self.status()
        self.assertEqual(len(value['target_exports']), 2)
        self.assertEqual(self.mutation_number, 2)

    def test_ctrl_c_retains_intent_no_replay(self):
        self.interrupt_export = 1
        rc, _ = self.run_stage()
        self.assertEqual(rc, 1)
        self.assertEqual(self.mutation_number, 1)
        self.assertTrue((m.STATE/'EXPORT-1-REQUESTED.json').exists())
        self.assertFalse((m.STATE/'EXPORT-1-ACK.json').exists())

    def test_external_export_drift_stops_without_rollback(self):
        self.drift_after = 1
        rc, output = self.run_stage()
        self.assertEqual(rc, 1)
        self.assertEqual(self.mutation_number, 1)
        self.assertIn('unrelated_exports_changed', output)
        self.assertEqual(self.exports[('/old/home', '192.168.111.0/24')], ('ro',))

    def test_config_drift_prevents_exports(self):
        original = m.write_new
        def write(path, raw, mode=0o600):
            original(path, raw, mode)
            if path == m.CONFIG:
                m.EXPORTS.write_bytes(b'/unrelated newhost(ro)\n')
        with patch.object(m, 'write_new', side_effect=write):
            rc, output = self.run_stage()
        self.assertEqual(rc, 1)
        self.assertIn('export_config_drift', output)
        self.assertEqual(self.mutation_number, 0)

    def test_existing_target_directory_not_adopted(self):
        m.ROOT.mkdir()
        before = m.ROOT.stat()
        with self.assertRaisesRegex(m.Failure, 'already_exists'):
            self.run_stage()
        self.assertEqual(m.ROOT.stat().st_mode, before.st_mode)
        self.assertFalse(m.STATE.exists())

    def test_symlink_target_not_followed(self):
        m.ROOT.symlink_to(self.base/'etc', target_is_directory=True)
        with self.assertRaisesRegex(m.Failure, 'already_exists'):
            self.run_stage()
        self.assertFalse(m.STATE.exists())

    def test_existing_config_not_overwritten(self):
        m.EXPORT_DIR.mkdir()
        m.CONFIG.write_bytes(b'# unrelated')
        with self.assertRaisesRegex(m.Failure, 'already_exists'):
            self.run_stage()
        self.assertEqual(m.CONFIG.read_bytes(), b'# unrelated')

    def test_new_path_in_inactive_config_blocks(self):
        m.EXPORTS.write_bytes((str(m.ROOT) + ' 192.168.111.2(rw)\n').encode())
        with self.assertRaisesRegex(m.Failure, 'overlaps'):
            self.run_stage()
        self.assertFalse(m.STATE.exists())

    def test_ancestor_active_export_blocks(self):
        self.exports[(str(m.MOUNT), '*')] = ('rw',)
        with self.assertRaisesRegex(m.Failure, 'overlaps'):
            self.run_stage()
        self.assertFalse(m.ROOT.exists())

    def test_preflight_readonly(self):
        m.preflight()
        self.assertFalse(m.STATE.exists())
        self.assertFalse(m.ROOT.exists())
        self.assertEqual(self.mutation_number, 0)

    def test_world_writable_ancestor_refused(self):
        self.base.chmod(0o777)
        try:
            with self.assertRaisesRegex(m.Failure, 'unsafe_private_ancestor'):
                m.config_inventory()
        finally:
            self.base.chmod(0o700)

    def test_symlink_config_refused(self):
        m.EXPORT_DIR.mkdir()
        (m.EXPORT_DIR/'foreign.exports').symlink_to(m.EXPORTS)
        with self.assertRaises(OSError):
            m.config_inventory()

    def test_hardlink_config_refused(self):
        m.EXPORT_DIR.mkdir()
        os.link(str(m.EXPORTS), str(m.EXPORT_DIR/'foreign.exports'))
        with self.assertRaisesRegex(m.Failure, 'unsafe_input_file'):
            m.config_inventory()

    def test_partial_mkdir_failure_is_journaled(self):
        original = os.mkdir
        def mkdir(path, *args, **kwargs):
            if str(path) == str(m.ROOT/'docreader'):
                raise OSError('SENSITIVE_RAW_DETAIL')
            return original(path, *args, **kwargs)
        with patch.object(m.os, 'mkdir', side_effect=mkdir):
            rc, output = self.run_stage()
        self.assertEqual(rc, 1)
        self.assertTrue((m.ROOT/'files').exists())
        self.assertFalse(m.CONFIG.exists())
        self.assertNotIn('SENSITIVE_RAW_DETAIL', output)
        self.assertTrue((m.STATE/'MKDIR-REQUESTED.json').exists())

    def test_status_does_not_traverse_replaced_root_symlink(self):
        m.ROOT.symlink_to(self.base/'etc', target_is_directory=True)
        value = self.status()
        self.assertEqual(len(value['paths']), 1)
        self.assertTrue(value['paths'][str(m.ROOT)]['symlink'])


class PureTests(unittest.TestCase):
    def test_python37(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3, 7))

    def test_exact_clients_and_paths(self):
        self.assertEqual(m.CLIENTS, ('192.168.111.2', '192.168.111.18'))
        self.assertEqual(str(m.ROOT), '/mnt/data/weknora-rwx-r2')
        self.assertEqual(str(m.STATE), '/var/lib/weknora-rwx-nfs-20261017-r2')
        self.assertEqual(len(m.keys()), 4)
        self.assertNotIn(b'192.168.111.0/24', m.config_bytes())
        self.assertNotIn(b'no_root_squash', m.config_bytes())

    def test_export_parsing(self):
        value = m.parse_exports(b'# comment\n/old/home 192.168.111.0/24(rw,sync,root_squash)\n')
        self.assertEqual(len(value), 1)

    def test_duplicate_export_rejected(self):
        with self.assertRaises(m.Failure):
            m.parse_exports(b'/x h(rw)\n/x h(ro)\n')

    def test_unrecognized_export_shape_rejected(self):
        for raw in (b'garbage', b'/x h', b'/x/../y h(rw)'):
            with self.assertRaises(m.Failure):
                m.parse_exports(raw)

    def test_options_cannot_weaken(self):
        base = m.OPTIONS.split(',')
        m.check_export_options(base + ['hide', 'wdelay'])
        for opt in ('no_root_squash', 'insecure', 'async', 'nohide', 'crossmnt', 'anonuid=0'):
            with self.assertRaises(m.Failure):
                m.check_export_options(base + [opt])

    def test_missing_required_option_rejected(self):
        with self.assertRaises(m.Failure):
            m.check_export_options([x for x in m.OPTIONS.split(',') if x != 'root_squash'])

    def test_preserve_old_detects_deleted_export(self):
        with self.assertRaises(m.Failure):
            m.preserve_old({('/old', 'host'): ('rw',)}, {}, set())

    def test_wrong_host_fails_before_tools(self):
        with patch.object(m.socket, 'gethostname', return_value='datax-03'), \
                patch.object(m, 'executable', side_effect=AssertionError('no calls')):
            with self.assertRaisesRegex(m.Failure, 'wrong_host'):
                m.environment()

    def test_bounded_cli_output(self):
        with self.assertRaisesRegex(m.Failure, 'output'):
            m.bounded_run([sys.executable, '-c', 'print("x"*10000)'], limit=100)

    def test_bounded_cli_timeout(self):
        with self.assertRaisesRegex(m.Failure, 'timeout'):
            m.bounded_run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.1)

    def test_failed_cli_has_no_raw_output(self):
        with self.assertRaises(m.Failure) as got:
            m.bounded_run([sys.executable, '-c', 'import sys; sys.stderr.write("SECRET"); sys.exit(5)'])
        self.assertNotIn('SECRET', str(got.exception))
        self.assertEqual(m.LAST['exit_code'], 5)


if __name__ == '__main__':
    unittest.main()
