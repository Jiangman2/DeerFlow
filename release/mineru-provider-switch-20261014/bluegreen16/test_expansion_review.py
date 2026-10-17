import ast
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import expansion_review as e
import reconcile_retirement as reconcile
from package_retire import ARTIFACTS

FROZEN = ARTIFACTS/'mineru-stepped-load-20261015-r1-fix1'


def frozen():
    module = types.ModuleType('test_frozen_preflight')
    module.__file__ = str(FROZEN/'preflight_load.py')
    raw = Path(module.__file__).read_bytes()
    assert e.sha(raw) == '9cad7ebd1180899b612a8f7022c8ab72905f943ee63e2ebc7fb1dab3a41071ae'
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    with patch.dict(sys.modules), patch.object(module, 'read_private', side_effect=lambda p: p.read_bytes()):
        module.load_helpers()
    return module


def inventory():
    name = e.NODES[0]
    unit = dict(cpu='2', memory='4Gi', **{'ephemeral-storage': '4Gi'})
    spec = dict(nodeSelector={'kubernetes.io/hostname': name},
        containers=[dict(name='adapter', resources=dict(requests=unit, limits=unit))])
    node = dict(metadata=dict(name=name, uid='node-id', labels={'kubernetes.io/hostname': name}),
        spec={}, status=dict(allocatable={'cpu': '32', 'memory': '128Gi', 'ephemeral-storage': '300Gi', 'pods': '110'},
        nodeInfo=dict(architecture='arm64', operatingSystem='linux'),
        conditions=[dict(type=k, status=v) for k, v in [
            ('Ready', 'True'), ('MemoryPressure', 'False'), ('DiskPressure', 'False'), ('PIDPressure', 'False')]]))
    podspec = copy.deepcopy(spec)
    podspec['nodeName'] = name
    pods = [dict(metadata=dict(name='current-worker', uid='pod-id'), spec=podspec, status=dict(phase='Running'))]
    fs = dict(age_seconds=0, capacity_GiB=300, available_GiB=100, inodes_free=100000,
              available_bytes=100*e.GIB, planning_reserve_bytes=60*e.GIB, inodes_headroom=True)
    measured = dict(cpu_cores=2, memory_working_set_bytes=4*e.GIB, filesystems=dict(root=fs, imagefs=copy.deepcopy(fs)))
    return node, pods, spec, measured


class RuntimeTests(unittest.TestCase):
    def test_python37_grammar(self):
        ast.parse(Path(e.__file__).read_bytes(), feature_version=(3, 7))

    def test_pinned_ids_shape(self):
        self.assertEqual([len(v) for v in e.RETIRED_IDS.values()], [4, 3])
        for rows in e.RETIRED_IDS.values():
            for name, value in rows.items():
                self.assertRegex(value, '^[0-9a-f]{64}$')
                self.assertTrue(e.old_name(name))

    def test_new_pool_names_excluded(self):
        for name in ('deer-flow-mineru-dedicated-r1-w-ecs-0011-5649d4755-9cwbk',
                     'deer-flow-mineru-dedicated-dispatch-r1-79fc89f8cc-djd2d', 'another-service-x-y'):
            self.assertFalse(e.old_name(name))

    def test_inventory_binding(self):
        records = [dict(node=node, name=name, container_id='containerd://'+cid)
                   for node, rows in e.RETIRED_IDS.items() for name, cid in rows.items()]
        e.bind_runtime_ids(dict(targets=records))
        with self.assertRaises(e.ReviewFailure):
            e.bind_runtime_ids(dict(targets=records[:-1]))
        records[0]['container_id'] = 'containerd://'+'1'*64
        with self.assertRaises(e.ReviewFailure):
            e.bind_runtime_ids(dict(targets=records))

    def test_local_rejects_arbitrary_commands(self):
        with patch.object(e.subprocess, 'Popen') as popen:
            for args in (['true'], list(e.RUNTIME_COMMANDS[2])+['--quiet'],
                         list(e.CRI)+['stop', 'cid']):
                with self.assertRaises(e.ReviewFailure):
                    e.capture_local(args)
            popen.assert_not_called()

    def test_known_container_without_labels_detected(self):
        cid = next(iter(e.RETIRED_IDS['datax-02'].values()))
        ids, _, states, _ = e.selected_records('datax-02', [], [dict(id=cid, state='CONTAINER_RUNNING')])
        self.assertIn(cid, ids)
        self.assertEqual(states, ['CONTAINER_RUNNING'])

    def test_other_old_generation_detected(self):
        pod = dict(id='a'*64, metadata=dict(namespace='deer-flow',
            name='deer-flow-mineru-adapter-abc123-xyz', uid='1'*8+'-'+'2'*4+'-'+'3'*4+'-'+'4'*4+'-'+'5'*12))
        row = dict(id='b'*64, podSandboxId=pod['id'], state='CONTAINER_EXITED')
        ids, uids, states, count = e.selected_records('datax-03', [pod], [row])
        self.assertIn(row['id'], ids)
        self.assertIn(pod['id'], ids)
        self.assertIn(pod['metadata']['uid'], uids)
        self.assertEqual((states, count), (['CONTAINER_EXITED'], 1))

    def test_invalid_cri_identity_blocks(self):
        with self.assertRaises(e.ReviewFailure):
            e.selected_records('datax-03', [dict(id='bad', metadata=dict(namespace='deer-flow',
                name='deer-flow-mineru-adapter-abc123-xyz'))], [])

    def test_cri_record_in_other_namespace_excluded(self):
        _, _, states, count = e.selected_records('datax-03', [],
            [dict(id='b'*64, state='CONTAINER_RUNNING', labels={
                'io.kubernetes.pod.namespace': 'other', 'io.kubernetes.pod.name': 'deer-flow-mineru-adapter-x-y'})])
        self.assertEqual((states, count), ([], 0))

    def test_task_parser(self):
        cid = 'a'*64
        raw = ('TASK PID STATUS\n%s 21 RUNNING\n%s 22 RUNNING\n' % (cid, 'b'*64)).encode()
        self.assertEqual(e.task_matches(raw, {cid}), [dict(id=cid, pid=21, state='RUNNING')])
        self.assertEqual(e.task_matches(b'TASK PID STATUS\n', {cid}), [])
        with self.assertRaises(e.ReviewFailure):
            e.task_matches(b'TASK ID STATE\n', {cid})

    def test_cgroup_scan_finds_retired_cid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, content in [('12', '0::/kubepods/'+('a'*64)), ('13', '0::/other')]:
                (root/pid).mkdir()
                (root/pid/'cgroup').write_text(content)
            row = e.cgroup_scan({'a'*64}, set(), root)
            self.assertEqual(row['matching_cgroup_processes'], 1)
            self.assertEqual(row['unreadable_cgroup_files'], 0)

    def test_unreadable_proc_is_not_clear(self):
        item = MagicMock()
        item.name = '123'
        item.__truediv__.return_value.open.side_effect = PermissionError()
        proc = MagicMock()
        proc.iterdir.return_value = [item]
        self.assertEqual(e.cgroup_scan({'a'*64}, set(), proc)['unreadable_cgroup_files'], 1)

    def test_local_sample_empty_and_active_cases(self):
        clear = dict(scanned_processes=10, unreadable_cgroup_files=0, matching_cgroup_processes=0)
        for state, passed in ((None, True), ('CONTAINER_EXITED', True),
                              ('CONTAINER_RUNNING', False), ('CONTAINER_CREATED', False), ('UNKNOWN', False)):
            cid = next(iter(e.RETIRED_IDS['datax-02'].values()))
            containers = [] if state is None else [dict(id=cid, state=state)]
            with self.subTest(state=state), patch.object(e, 'capture_local', side_effect=[
                    b'{"items":[]}', json.dumps(dict(containers=containers)).encode(), b'TASK PID STATUS\n']) as capture, \
                    patch.object(e, 'cgroup_scan', return_value=clear):
                self.assertIs(e.local_sample('datax-02')['no_activity_observed'], passed)
                self.assertEqual([tuple(x.args[0]) for x in capture.call_args_list], list(e.RUNTIME_COMMANDS))

    def test_cgroup_read_gap_blocks_sample(self):
        with patch.object(e, 'capture_local', side_effect=[
                b'{"items":[]}', b'{"containers":[]}', b'TASK PID STATUS\n']), \
                patch.object(e, 'cgroup_scan', return_value=dict(scanned_processes=10,
                    unreadable_cgroup_files=1, matching_cgroup_processes=0)):
            self.assertFalse(e.local_sample('datax-02')['no_activity_observed'])


class CapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pf = frozen()

    def budget(self, change=None):
        node, pods, spec, measured = inventory()
        if change:
            change(node, pods, spec, measured)
        return e.slot_budget(self.pf, node, pods, {'pod-id'}, spec, measured)

    def test_four_gib_production_disk_not_one_gib_fixture(self):
        row = self.budget()
        self.assertEqual(row['additional_workers_planning_only'], 10)
        self.assertEqual(row['disk_slots'], [10, 10])

    def test_disk_headroom_eight_gib_allows_only_two(self):
        def change(n, p, s, m):
            m['filesystems']['root']['available_bytes'] = 68*e.GIB
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 2)

    def test_existing_memory_usage_counts(self):
        def change(n, p, s, m):
            m['memory_working_set_bytes'] = 100*e.GIB
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 0)

    def test_existing_ephemeral_requests_count(self):
        def change(n, p, s, m):
            p[0]['spec']['containers'][0]['resources']['requests']['ephemeral-storage'] = '238Gi'
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 0)

    def test_bad_inode_headroom_blocks(self):
        def change(n, p, s, m):
            m['filesystems']['imagefs']['inodes_headroom'] = False
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 0)

    def test_cordoned_node_gets_no_slots(self):
        def change(n, p, s, m):
            n['spec']['unschedulable'] = True
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 0)

    def test_node_disk_pressure_gets_no_slots(self):
        def change(n, p, s, m):
            n['status']['conditions'][2]['status'] = 'True'
        self.assertEqual(self.budget(change)['additional_workers_planning_only'], 0)

    def test_resource_shape_change_rejected(self):
        def change(n, p, s, m):
            s['containers'][0]['resources']['requests']['memory'] = '2Gi'
        with self.assertRaises(e.ReviewFailure):
            self.budget(change)

    def test_balanced_16_plan(self):
        rows = [dict(node=n, additional_workers_planning_only=3) for n in e.NODES]
        counts = e.proposed_counts(rows)
        self.assertEqual(sum(counts.values()), 16)
        self.assertEqual(list(counts.values()), [3, 3, 3, 3, 2, 2])
        for r in rows:
            r['additional_workers_planning_only'] = 1
        self.assertIsNone(e.proposed_counts(rows))

    def test_unbalanced_capacity_respected(self):
        rows = [dict(node=n, additional_workers_planning_only=0) for n in e.NODES]
        rows[-1]['additional_workers_planning_only'] = 10
        counts = e.proposed_counts(rows)
        self.assertEqual(counts[e.NODES[-1]], 11)

    def test_real_frozen_getters_pass_through_guard(self):
        pf = self.pf
        capture = MagicMock(side_effect=[b'{"items":[]}', b'{"node":{}}'])
        pf.guard.NODES = {n: {} for n in e.NODES}
        with patch.object(pf.inv, 'bounded_capture', e.capacity_capture(capture, reconcile)):
            self.assertEqual(pf.node_pods(e.NODES[0]), [])
            self.assertEqual(pf.inv.get('node_summary', e.NODES[0]), {'node': {}})
        self.assertEqual(capture.call_count, 2)

    def test_raw_other_urls_and_mutators_denied(self):
        inner = MagicMock()
        fn = e.capacity_capture(inner, reconcile)
        bad = [['kubectl', '--request-timeout=20s', 'get', '--raw', '/api/v1/nodes/datax-02/proxy/stats/summary'],
               ['kubectl', '--request-timeout=20s', 'get', 'pods', '--all-namespaces'],
               ['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'patch', 'deployment', 'anything'],
               ['ctr', 'tasks', 'kill', 'cid']]
        for args in bad:
            with self.assertRaises(reconcile.ReconcileFailure):
                fn(args)
        inner.assert_not_called()

    def test_real_metric_age_gate(self):
        pf = self.pf
        stamp = '2020-01-01T00:00:00Z'
        data = dict(node=dict(nodeName=e.NODES[0], cpu=dict(time=stamp, usageNanoCores=1),
            memory=dict(time=stamp, workingSetBytes=1)))
        with self.assertRaises(pf.PreflightFailure):
            pf.metrics(data, e.NODES[0], pf.utc())

    def test_controller_flow_binds_old_ids_and_leaves_writes_disabled(self):
        # Actual caller, guards, distribution and resource arithmetic; Linux/
        # Kubernetes IO and the already separately tested baseline checker mocked.
        pf = frozen()
        r, c = MagicMock(), MagicMock()
        c.readonly_capture = reconcile.readonly_capture
        c.deny_mutation = reconcile.deny_mutation
        r.s.core.pf = pf
        r.TARGETS = ('old-dispatcher', 'old-adapter')
        targets = [dict(node=node, name=name, container_id='containerd://'+cid)
                   for node, rows in e.RETIRED_IDS.items() for name, cid in rows.items()]
        state = dict(inventory=dict(targets=targets))
        c.bind_evidence.return_value = state
        nodes, created, candidates, pod_rows = [], {}, [], {}
        worker_names = []
        for i, name in enumerate(e.NODES):
            node, pods, spec, measured = inventory()
            node['metadata']['name'] = name
            node['metadata']['labels']['kubernetes.io/hostname'] = name
            spec['nodeSelector']['kubernetes.io/hostname'] = name
            pods[0]['spec']['nodeName'] = name
            pod_rows[name] = pods
            nodes.append(node)
            dep = 'worker-'+str(i)
            worker_names.append(dep)
            created[dep] = dict(spec=dict(replicas=1, template=dict(spec=spec)))
            candidates.append(dict(node=name, uid='worker-'+str(i), container='adapter'))
        r.s.WORKERS = worker_names
        r.s.core.guard.NODES = {name: dict(uid='node-id') for name in e.NODES}
        context = dict(created=created)
        r.bootstrap.return_value = (context, dict(inventory=state['inventory']), '', '')
        r.a.locked.return_value = json.dumps(dict(mineru_retirement_reconcile='PASS',
            old_pool_control_plane_retired=True, candidate_runtime_acceptance='PASS',
            public_route='candidate', new_pool_slots=6)).encode()
        r.original_snapshots.return_value = {}
        r.invariants.return_value = ({n: {} for n in r.TARGETS}, candidates)
        r.owned_state.return_value = []
        r.node_leases.return_value = []
        inv = r.s.core.inv
        inv.get.side_effect = lambda kind, *args: dict(items=nodes) if kind == 'nodes' else {}
        inv.digest.side_effect = lambda obj: e.sha(json.dumps(obj, sort_keys=True).encode())
        inv.node_projection.side_effect = lambda obj: obj['metadata']
        report = dict(ready_to_expand_16=False)
        with patch.object(e, 'load_reconcile', return_value=(c, r)), \
             patch.object(pf, 'node_pods', side_effect=lambda n: pod_rows[n]), \
             patch.object(pf, 'metrics', return_value=measured), patch.object(e, 'emit'):
            e.controller_review(Path('/unused'), report)
        self.assertEqual(report['expansion_review'], 'PASS')
        self.assertFalse(report['ready_to_expand_16'])
        self.assertEqual(sum(report['proposed_final_worker_counts'].values()), 16)
        self.assertEqual(r.invariants.call_count, 2)
        self.assertIs(r.patch_call, reconcile.deny_mutation)
        r.s.call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
