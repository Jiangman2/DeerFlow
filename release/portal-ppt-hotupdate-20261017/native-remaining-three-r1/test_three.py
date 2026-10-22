import contextlib
import importlib.util
import json
from pathlib import Path
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


N = load(HERE / 'resume_three.py', 'remaining_three')
O = load(HERE.parent / 'native-remaining-preload-r1/test_remaining.py', 'prior_fixture_utilities')
G = N.load_supervisor()


def table(rows):
    width_key = max([3] + [len(row[0]) for row in rows]) + 2
    width_parent = max([6] + [len(row[1]) for row in rows]) + 2
    return ('\n'.join(key.ljust(width_key) + parent.ljust(width_parent) + kind for key, parent, kind in [('KEY','PARENT','KIND')] + rows) + '\n').encode()


class ThreeFixtures(unittest.TestCase):
    def setUp(self):
        self.case = O.RemainingFixtures(methodName='runTest'); self.case.setUp()
        self.root = self.case.root
        self.addCleanup(self.case.tearDown)

    @contextlib.contextmanager
    def main_fixture(self, **kwargs):
        with mock.patch.object(O, 'N', N), mock.patch.object(N, 'partial_boundary', return_value={}):
            with self.case.main_fixture(**kwargs) as fixture: yield fixture

    def module(self, raw):
        return types.SimpleNamespace(B=types.SimpleNamespace(CTR=['ctr'], capture=mock.Mock(return_value=raw)), A=G)

    def test_actual_parent_rows_reproduce_old_false_positive_and_new_gate_permits_only_committed_parent(self):
        image = json.loads((HERE / 'RUNNER-CHAINS.json').read_bytes())['ppt-master-runner-forward']
        rows = [(item['chain_id'], item['parent'], 'Committed') for item in image['layers'][:365]]
        rows += [('3f4d467718fe1018568fe30985d3f2ffc8a0bd67c1876b9050aba06701453fcb',
                  'sha256:cd69448ad1324f7b9a2a602f3c1549d3de7cfb2754c691066be15de0264746bd', 'Active'),
                 ('5145e9ee7b23ac919c19dcfadcae8590f7c63e62cbc9c4538541e6c73ff5eee9',
                  'sha256:9e0d810215492511ebc07ed8185ed40a8e081cc988409e12f82e32102e08c073', 'Active')]
        raw = table(rows); module = self.module(raw)
        # Original condition demonstrably confuses the parent column with key.
        chains = {row['chain_id'] for row in image['layers']}
        self.assertEqual(sum(line.split()[-1] == 'Active' and bool(set(line.split()) & chains) for line in raw.decode().splitlines()[1:]), 2)
        result = N.snapshot_gate(G, module, image)
        self.assertEqual(result['committed_prefix'], 365); self.assertEqual(result['ordinary_active_children_of_committed_parents'], 2)
        module.B.capture.return_value = table(rows[:364] + rows[365:])
        with self.assertRaisesRegex(ValueError, 'active_child_parent_not_committed'): N.snapshot_gate(G, module, image)

    def test_actual_extract_key_with_space_and_candidate_key_active_or_view_are_blocked(self):
        one, two = 'sha256:' + 'a'*64, 'sha256:' + 'b'*64
        image = dict(layers=[dict(chain_id=one, parent=''), dict(chain_id=two, parent=one)])
        for rows, error in [([(one,'','Committed'), ('extract-123 ' + two,one,'Active')], 'candidate_extraction_key_requires_review'),
                            ([('extract-123 ' + one,'','Active')], 'candidate_extraction_key_requires_review'),
                            ([(one,'','Active')], 'candidate_key_active_or_view_requires_review'),
                            ([(one,'','View')], 'candidate_key_active_or_view_requires_review')]:
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError, error): N.snapshot_gate(G, self.module(table(rows)), image)

    def test_malformed_columns_duplicates_chain_gaps_and_unknown_children_fail_closed(self):
        one, two = 'sha256:' + 'a'*64, 'sha256:' + 'b'*64
        image = dict(layers=[dict(chain_id=one, parent=''), dict(chain_id=two, parent=one)])
        for raw in (b'KEY PARENT KIND\nshort Active\n', b'KEY\tPARENT\tKIND\n', table([(one,'','Committed'),(one,'','Committed')]),
                    table([(two,one,'Committed')]), table([(one,'','Committed'),('unknown-child',one,'Active')]),
                    table([(one,'','Committed'),('f'*64,one,'View')]), table([(one,'unexpected','Committed')])):
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(ValueError): N.snapshot_gate(G, self.module(raw), image)

    def test_exact_prior_reports_supervisor_and_three_role_scope(self):
        raw = (HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json').read_bytes()
        self.assertEqual((len(raw), N.sha(raw)), (N.PARTIAL_REPORT_BYTES, N.PARTIAL_REPORT_SHA))
        self.assertEqual(N.ROLES, ('ppt-master-runner-forward','ppt-master-runner-rollback-compatible','ppt-master-agent-host-forward'))
        self.assertEqual(N.PREVIOUS, ('frontend','gateway-forward','gateway-rollback-compatible'))
        self.assertEqual(N.SUPERVISOR_Z64, O.N.SUPERVISOR_Z64)

    def test_three_children_in_order_prior_three_read_only_once_and_final_six(self):
        with self.main_fixture() as (reports, launches, checks, module):
            self.assertEqual(N.run(), 0); self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES)); self.assertEqual(checks[:3], list(N.PREVIOUS))
        self.assertEqual(checks[-6:], [row['role'] for row in O.PROFILE['images']])
        self.assertEqual(reports[0]['status'], 'COMPLETE_PRELOAD_REVIEW_REQUIRED'); self.assertEqual(len(reports[0]['final_six_images']), 6)
        self.assertFalse(reports[0]['gateway_rollback_reimported']); self.assertFalse(reports[0]['ready_for_rollout'])
        self.assertEqual(reports[1]['error_code'], 'remaining_preload_already_started')
        self.assertEqual([row['import_returncode'] for row in reports[0]['role_results']], [0,0,0])

    def test_second_role_failure_and_final_verification_failure_do_not_report_completion(self):
        with self.main_fixture(fail_role=N.ROLES[1]) as (reports, launches, checks, module): self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES[:2])); self.assertEqual(reports[0]['status'], 'INCOMPLETE')
        self.assertEqual(reports[0]['role_results'][1]['import_returncode'], 23)

    def test_final_verification_failure_after_three_successes_still_incomplete(self):
        with self.main_fixture(final_failure=True) as (reports, launches, checks, module): self.assertEqual(N.run(), 1)
        self.assertEqual(launches, list(N.ROLES)); self.assertEqual(reports[0]['status'], 'INCOMPLETE')
        self.assertEqual(reports[0]['error_code'], 'final_reference_changed'); self.assertNotEqual(reports[0]['phase_b'], 'PASS')

    def test_partial_boundary_binds_completed_gateway_and_unrequested_runner(self):
        report = json.loads((HERE.parent / 'native-remaining-preload-evidence-r1/REPORT.json').read_bytes())
        state = self.root / 'prior-four'; state.mkdir(mode=0o700)
        child = state / 'gateway-rollback-compatible'; child.mkdir(mode=0o700)
        report['evidence'] = str(state); completed = report['role_results'][0]; completed['evidence'] = str(child)
        for name, record in completed['import_receipt']['logs'].items():
            raw = ('fixture ' + name).encode(); O.B.write_new(child / name, raw)
            record.update(path=str(child / name), observed_bytes=len(raw), saved_bytes=len(raw), sha256=N.sha(raw))
        O.B.write_new(child / 'GATEWAY-IMPORT-RESULT.json', O.B.encoded(completed['import_receipt']))
        O.B.write_new(child / 'GATEWAY-IMPORT-REQUESTED.json', O.B.encoded(dict(role=completed['role'], phase='PHASE-B', automatic_retry=False)))
        O.B.write_new(state / 'gateway-rollback-compatible.PASS.json', O.B.encoded(completed))
        started = {key: report[key] for key in ('schema','operation','evidence','host_identity','prior_failure_receipts','roles_requested')}
        started.update(role_results=[],status='INCOMPLETE',target_import_requested=False)
        O.B.write_new(state / 'STARTED.json', O.B.encoded(started))
        once = self.root / 'prior.once'; O.B.write_new(once, O.B.encoded(dict(operation=N.OP,evidence=str(state),automatic_retry=False)))
        raw = O.B.encoded(report); O.B.write_new(state / 'REPORT.json', raw)
        with mock.patch.multiple(N, PARTIAL_EVIDENCE=state, PARTIAL_ONCE=once, PARTIAL_REPORT_SHA=N.sha(raw), PARTIAL_REPORT_BYTES=len(raw)):
            accepted = N.partial_boundary(G,O.F,O.PROFILE,report['host_identity'])
            self.assertTrue(accepted['runner_import_not_requested']); self.assertEqual(accepted['completed_role'],'gateway-rollback-compatible')
            (child / 'stderr.private.log').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'partial_log_changed'): N.partial_boundary(G,O.F,O.PROFILE,report['host_identity'])


if __name__ == '__main__': unittest.main(verbosity=2)
