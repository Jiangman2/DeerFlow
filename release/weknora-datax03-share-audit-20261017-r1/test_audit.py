import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import audit as m


class Tests(unittest.TestCase):
    def test_python37_grammar(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3, 7))

    def test_no_mutation_api_in_source(self):
        tree = ast.parse(Path(m.__file__).read_text())
        forbidden = {'write', 'write_text', 'write_bytes', 'chmod', 'chown', 'unlink',
                     'rmdir', 'mkdir', 'remove', 'rename', 'replace', 'symlink', 'link'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # str.replace normalizes config continuation lines in memory.
                self.assertNotIn(node.func.attr, forbidden - {'replace'})

    def test_service_allowlist(self):
        raw = b'LoadState=loaded\nActiveState=active\nSubState=running\nEnvironment=TOP_SECRET\n'
        value = m.service_projection(raw)
        self.assertEqual(value['ActiveState'], 'active')
        self.assertNotIn('TOP_SECRET', json.dumps(value))

    def test_service_unknown_redacted(self):
        value = m.service_projection(b'LoadState=loaded\nActiveState=internal-secret\n')
        self.assertEqual(value['ActiveState'], 'OTHER_REDACTED')

    def test_service_missing_is_unknown(self):
        with self.assertRaises(m.Unavailable):
            m.service_projection(b'')

    def test_service_duplicate_rejected(self):
        with self.assertRaises(m.Unavailable):
            m.service_projection(b'LoadState=loaded\nLoadState=loaded\nActiveState=active\n')

    def test_relation_component_boundary(self):
        target = m.TARGETS[0]
        self.assertEqual(m.relation('/opt/deerflow-storage', target), 'ANCESTOR')
        self.assertEqual(m.relation(target, target), 'EXACT')
        self.assertEqual(m.relation(target + '/child', target), 'DESCENDANT')
        self.assertEqual(m.relation(target + '-old', target), 'UNRELATED')

    def test_export_redacts_arbitrary_paths_and_clients(self):
        raw = ('/secret/customer-path customer.internal(rw,sync,no_root_squash)\n' +
               m.TARGETS[0] + ' secret-host(rw,sync,root_squash)\n').encode()
        value = m.export_projection(raw, True)
        self.assertEqual(value['export_lines'], 2)
        self.assertEqual(len(value['target_overlaps']), 1)
        output = json.dumps(value)
        for word in ('customer', 'secret-host', 'secret/customer-path'):
            self.assertNotIn(word, output)
        self.assertTrue(value['target_overlaps'][0]['options'][0]['root_squash'])

    def test_export_continuations_and_comments(self):
        raw = b'/opt \\\n host(rw,sync) # comment\n'
        value = m.export_projection(raw, False)
        self.assertEqual(value['export_lines'], 1)
        self.assertEqual(len(value['target_overlaps'][0]['relationships']), 2)

    def test_export_empty_is_valid(self):
        self.assertEqual(m.export_projection(b'# none\n', True)['export_lines'], 0)

    def test_export_parse_errors_not_empty(self):
        for raw in (b'bad', b'/opt/../opt h(rw)', b'/opt bad-no-options', b'"/opt h(rw)'):
            with self.assertRaises((m.Unavailable, ValueError)):
                m.export_projection(raw, True)

    def test_listener_ipv4_ipv6_and_loopback(self):
        value = m.listening_projection(b'LISTEN 0 64 0.0.0.0:2049 0.0.0.0:*\n'
                                      b'LISTEN 0 64 [::]:2049 [::]:*\n'
                                      b'LISTEN 0 64 127.0.0.1:111 0.0.0.0:*\n')
        self.assertTrue(value[0]['wildcard'])
        self.assertTrue(value[1]['wildcard'])
        self.assertTrue(value[2]['loopback'])

    def test_listener_unknown_port_fails(self):
        with self.assertRaises(m.Unavailable):
            m.listening_projection(b'LISTEN 0 64 0.0.0.0:22 *:*\n')

    def test_listener_empty_is_valid(self):
        self.assertEqual(m.listening_projection(b''), [])

    def test_memory_no_guessing(self):
        value = m.memory_projection(b'MemTotal: 100 kB\nMemAvailable: 20 kB\nSwapTotal: 0 kB\n')
        self.assertEqual(value['MemAvailable_bytes'], 20480)
        with self.assertRaises(m.Unavailable):
            m.memory_projection(b'MemTotal: 100 kB\n')

    def samples(self):
        first = dict(monotonic=10, cpu=[1, 2, 3, 40, 4, 5, 6, 7],
                     disk=dict(vdb=dict(read_bytes=100, write_bytes=100, io_ms=10)),
                     net=dict(eth0=dict(rx_bytes=100, tx_bytes=0)))
        second = dict(monotonic=15, cpu=[11, 2, 3, 70, 14, 5, 6, 7],
                      disk=dict(vdb=dict(read_bytes=200, write_bytes=300, io_ms=15)),
                      net=dict(eth0=dict(rx_bytes=200, tx_bytes=50)))
        return first, second

    def test_resource_math(self):
        value = m.counter_delta(*self.samples())
        self.assertEqual(value['cpu']['busy_percent'], 20.0)
        self.assertEqual(value['cpu']['iowait_percent'], 20.0)
        self.assertEqual(value['disk']['vdb']['read_bytes_per_second'], 20)

    def test_counter_reset_not_negative_capacity(self):
        a, b = self.samples()
        b['cpu'][0] = 0
        b['disk']['vdb']['io_ms'] = 0
        value = m.counter_delta(a, b)
        self.assertIsInstance(value['cpu'], str)
        self.assertEqual(value['disk']['vdb']['state'], 'COUNTER_RESET')

    def test_interface_churn(self):
        a, b = self.samples()
        b['net'] = dict(eth1=dict(rx_bytes=1, tx_bytes=1))
        value = m.counter_delta(a, b)
        self.assertEqual(value['net']['eth0']['state'], 'APPEARED_OR_DISAPPEARED')

    def test_cpu_guest_not_double_counted(self):
        data = {'/proc/stat': b'cpu 1 2 3 4 5 6 7 8 9 10\n',
                '/proc/diskstats': b'252 1 vdb1 1 2 3 4 5 6 7 8 9 10 11\n',
                '/proc/net/dev': b'header\nheader\n eth0: 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16\n'}
        value = m.counters(lambda path: data[path])
        self.assertEqual(value['cpu'], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(value['disk']['vdb1']['read_bytes'], 1536)

    def test_subprocess_exit_and_no_stderr_leak(self):
        rc, raw = m.bounded_run([sys.executable, '-c', 'import sys; print("ok"); sys.stderr.write("SECRET"); sys.exit(4)'])
        self.assertEqual((rc, raw), (4, b'ok\n'))

    def test_subprocess_output_bound(self):
        with self.assertRaisesRegex(m.Unavailable, 'output'):
            m.bounded_run([sys.executable, '-c', 'print("x"*10000)'], limit=100)

    def test_subprocess_deadline(self):
        with self.assertRaisesRegex(m.Unavailable, 'timeout'):
            m.bounded_run([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.1)

    def test_section_error_redacted(self):
        audit = m.Audit()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            audit.section('test', lambda: (_ for _ in ()).throw(ValueError('SECRET')))
        self.assertNotIn('SECRET', output.getvalue() + json.dumps(audit.results))
        self.assertEqual(audit.results['test']['state'], 'UNAVAILABLE')

    def test_total_budget_prevents_more_commands(self):
        audit = m.Audit()
        audit.started -= 101
        with patch.object(m, 'tool', side_effect=AssertionError('must not discover')):
            with self.assertRaisesRegex(m.Unavailable, 'budget'):
                audit.command('exportfs', ['-s'])

    def test_write_commands_fail_before_spawn(self):
        audit = m.Audit()
        with patch.object(m, 'tool', side_effect=AssertionError('must not discover')):
            for name, args in [('exportfs', ['-ra']), ('exportfs', ['-u', '/opt']),
                               ('systemctl', ['start', 'nfs-server.service']),
                               ('firewall-cmd', ['--add-service=nfs']),
                               ('ip', ['link', 'set', 'eth0', 'down']),
                               ('mount', ['-t', 'nfs']), ('kubectl', ['get', 'pods'])]:
                with self.assertRaisesRegex(m.Unavailable, 'allowlisted'):
                    audit.command(name, args)

    def test_read_allowlist(self):
        self.assertTrue(m.read_command_allowed('exportfs', ['-s']))
        self.assertTrue(m.read_command_allowed('firewall-cmd', ['--zone=public', '--query-port=2049/tcp']))
        self.assertFalse(m.read_command_allowed('firewall-cmd', ['--zone=public;evil', '--query-port=2049/tcp']))
        self.assertFalse(m.read_command_allowed('findmnt', ['--json', '--target', '/', '--output', 'OPTIONS']))

    def test_optional_missing_service_has_explicit_state(self):
        raw = b'LoadState=not-found\nActiveState=inactive\nSubState=dead\nUnitFileState=\n'
        self.assertEqual(m.service_projection(raw)['LoadState'], 'not-found')

    def test_partial_services_failure_does_not_hide_other_sections(self):
        audit = m.Audit()
        with contextlib.redirect_stdout(io.StringIO()):
            audit.section('services', lambda: (_ for _ in ()).throw(OSError('SECRET')))
            audit.section('next', lambda: {'value': 1})
        self.assertEqual(audit.results['services']['state'], 'UNAVAILABLE')
        self.assertEqual(audit.results['next']['state'], 'COLLECTED')

    def test_firewall_absent_never_means_allowed(self):
        with patch.object(m, 'tool', return_value=None):
            value = m.Audit().firewall()
        self.assertEqual(value['network_access'], 'NOT_PROVEN')

    def test_firewall_queries_only(self):
        audit = m.Audit()
        commands = []
        def command(name, args, accept=(0,)):
            commands.append(args)
            if args == ['--state']:
                return 0, b'running\n'
            if args == ['--get-active-zones']:
                return 0, b'public\n  interfaces: eth0\n'
            return 1, b'no\n'
        with patch.object(m, 'tool', return_value='/usr/bin/firewall-cmd'), patch.object(audit, 'command', side_effect=command):
            value = audit.firewall()
        self.assertEqual(value['zones'][0]['query-service=nfs'], False)
        self.assertFalse(any('add' in ' '.join(cmd) or 'reload' in ' '.join(cmd) for cmd in commands))

    def test_networking_no_traffic(self):
        audit = m.Audit()
        calls = []
        def command(name, args, accept=(0,)):
            calls.append((name, args))
            if '-o' in args:
                return 0, b'2: eth0 inet 192.168.111.18/24 brd 192.168.111.255\n'
            return 0, b'[{"prefsrc":"192.168.111.18","dev":"eth0"}]'
        with patch.object(audit, 'command', side_effect=command):
            value = audit.networking()
        self.assertTrue(value['route_source_matches_reviewed_self'])
        self.assertFalse(value['network_probe_sent'])
        self.assertTrue(all(name == 'ip' for name, _ in calls))

    def test_read_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x'
            p.symlink_to('/etc/passwd')
            with self.assertRaises(OSError):
                m.read(str(p))

    def test_read_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x'
            p.write_bytes(b'12345')
            with self.assertRaises(m.Unavailable):
                m.read(str(p), limit=2)

    def test_wrong_host_before_any_collection(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['audit.py']), patch.object(m.socket, 'gethostname', return_value='wrong'), patch.object(m.Audit, 'run', side_effect=AssertionError('not allowed')), contextlib.redirect_stdout(output):
            self.assertEqual(m.main(), 1)
        self.assertIn('wrong_host', output.getvalue())

    def test_summary_never_approves_scaling_or_exports(self):
        audit = m.Audit()
        # Exercise whole summary assembly while replacing all host reads.
        def section(name, function):
            audit.results[name] = dict(state='COLLECTED', data={})
        stream = io.StringIO()
        with patch.object(audit, 'section', side_effect=section), contextlib.redirect_stdout(stream):
            self.assertEqual(audit.run(), 0)
        value = json.loads(stream.getvalue())
        for key in ('ready_to_scale', 'ready_to_export', 'ready_to_change_mounts', 'files_modified'):
            self.assertFalse(value[key])

    def test_partial_exit_not_success(self):
        audit = m.Audit()
        def section(name, function):
            audit.results[name] = dict(state='UNAVAILABLE')
        with patch.object(audit, 'section', side_effect=section), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(audit.run(), 2)


if __name__ == '__main__':
    unittest.main()
