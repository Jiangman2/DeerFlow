import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import readiness_wait
import test_delivery


class ReadinessTests(unittest.TestCase):
    def deployment(self, ready=70, available=70):
        d = test_delivery.ReleaseTests().deployment()
        d['metadata']['generation'] = 427
        d['spec']['replicas'] = 70
        d['status'] = {'observedGeneration': 427, 'updatedReplicas': 70, 'readyReplicas': ready,
                       'availableReplicas': available, 'replicas': 70}
        return d

    def run_wait(self, values, seconds=30, pods=None):
        clock = [0]
        snapshots = iter(values)
        latest = [None]

        def kube(*args):
            latest[0] = next(snapshots, latest[0])
            return copy.deepcopy(latest[0])

        def sleep(seconds):
            clock[0] += seconds

        with tempfile.TemporaryDirectory() as temp, patch.object(readiness_wait.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(readiness_wait.time, 'sleep', side_effect=sleep), redirect_stdout(io.StringIO()):
            result = readiness_wait.wait_ready(Path(temp), seconds, kube, test_delivery.release.stable,
                                               pods or Mock(return_value=['pod']), test_delivery.release.spec_hash)
            evidence = [json.loads(line) for line in (Path(temp) / 'readiness.jsonl').read_text().splitlines()]
            return result, evidence, clock[0]

    def test_saved_69_ready_68_available_waits_then_recovers(self):
        _, evidence, elapsed = self.run_wait([self.deployment(69, 68), self.deployment()])
        self.assertEqual(elapsed, 10)
        self.assertEqual(evidence[0]['readyReplicas'], 69)
        self.assertEqual(evidence[0]['availableReplicas'], 68)
        self.assertEqual(evidence[-1]['stage'], 'GATEWAY_PREFLIGHT_READY')

    def test_all_ready_does_not_delay(self):
        _, _, elapsed = self.run_wait([self.deployment()])
        self.assertEqual(elapsed, 0)

    def test_persistent_degradation_stops_without_lowering_threshold(self):
        with self.assertRaisesRegex(RuntimeError, 'PREFLIGHT_READINESS_TIMEOUT'):
            self.run_wait([self.deployment(69, 68)], seconds=20)

    def test_ready_status_but_pod_not_ready_retries(self):
        pods = Mock(side_effect=[RuntimeError('POD_NOT_READY'), ['pod']])
        _, evidence, elapsed = self.run_wait([self.deployment()], pods=pods)
        self.assertEqual(elapsed, 10)
        self.assertEqual(evidence[0]['pod_check'], 'POD_NOT_READY')

    def test_spec_drift_stops(self):
        changed = self.deployment()
        changed['spec']['replicas'] = 69
        with self.assertRaisesRegex(RuntimeError, 'DEPLOYMENT_CHANGED_WHILE_WAITING'):
            self.run_wait([self.deployment(69, 68), changed])

    def test_paused_and_real_pod_error_do_not_retry(self):
        paused = self.deployment()
        paused['spec']['paused'] = True
        with self.assertRaisesRegex(RuntimeError, 'DEPLOYMENT_PAUSED'):
            self.run_wait([paused])
        with self.assertRaisesRegex(RuntimeError, 'POD_IMAGE_CHANGED'):
            self.run_wait([self.deployment()], pods=Mock(side_effect=RuntimeError('POD_IMAGE_CHANGED')))
