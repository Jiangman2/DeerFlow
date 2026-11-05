import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import frontend_slim as slim
import build_pool as pool


class SlimTests(unittest.TestCase):
    def test_base_inspection_selects_target_architecture(self):
        with patch.object(slim, 'capture', return_value=b'[{"Architecture":"arm64"}]') as capture:
            self.assertEqual(slim.inspect_platform_base('node@sha256:fixture', 'linux/arm64')['Architecture'], 'arm64')
            capture.assert_called_once_with(['docker', 'image', 'inspect', '--platform', 'linux/arm64', 'node@sha256:fixture'])
        with patch.object(slim, 'capture', return_value=b'[{"Architecture":"amd64"}]'):
            with self.assertRaisesRegex(slim.CheckFailure, 'base_image_architecture_mismatch'):
                slim.inspect_platform_base('node@sha256:fixture', 'linux/arm64')

    def test_fresh_runtime_prefix_and_layer_budget(self):
        base = {'RootFS': {'Layers': ['node-a', 'node-b']}}
        good = {'RootFS': {'Layers': ['node-a', 'node-b', 'new-app']}}
        self.assertEqual(slim.verify_fresh_layers(base, good)['added_layers'], 1)
        for layers in (['old-app', 'new-app'], ['node-a', 'node-b'] + ['old-app'] * 13):
            with self.assertRaises(slim.CheckFailure):
                slim.verify_fresh_layers(base, {'RootFS': {'Layers': layers}})

    def test_selected_runtime_sources(self):
        for name in ('frontend/src/app/page.tsx', 'frontend/public/icon.png',
                     'frontend/package.json', 'frontend/.npmrc', 'frontend/tests/unit/test.ts'):
            self.assertTrue(slim.selected(name), name)

    def test_excludes_cache_credentials_and_archives(self):
        for name in ('backend/main.py', 'frontend/node_modules/a.js', 'frontend/.next/server.js',
                     'frontend/.env', 'frontend/a/.env.production', 'frontend/server.key',
                     'frontend/coverage/x', 'frontend/release.oci.tar', 'frontend/CLAUDE.md'):
            self.assertFalse(slim.selected(name), name)

    def test_snapshot_fails_when_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'; root.mkdir()
            (root / 'frontend').mkdir()
            (root / 'frontend/a').write_bytes(b'old')
            out = Path(tmp) / 'out'; out.mkdir()
            rows = [{'path': 'frontend/a', 'bytes': 3, 'sha256': slim.sha(b'old')}]
            with patch.object(slim, 'source_inventory', side_effect=[rows, []]):
                with self.assertRaisesRegex(slim.CheckFailure, 'source_changed_during_snapshot'):
                    slim.prepare(root, out)

    def test_receipts_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'result.json'
            slim.write_json(file, {'original': True})
            with self.assertRaises(FileExistsError):
                slim.write_json(file, {})
            self.assertTrue(json.loads(file.read_text())['original'])

    def test_runtime_has_clean_base_and_explicit_copies(self):
        recipe = (SCRIPTS.parent / 'frontend/Dockerfile.slim').read_text()
        runtime = recipe.split('FROM ${NODE_IMAGE} AS runtime', 1)[1]
        self.assertIn('.next/standalone/', runtime)
        self.assertNotIn('pnpm install', runtime)
        self.assertNotIn('COPY --from=compiler /app/frontend ./', runtime)
        self.assertIn('CMD ["node", "server.js"]', runtime)
        self.assertIn('sharing=locked', recipe)
        self.assertIn('--frozen-lockfile', recipe)
        self.assertNotIn('ignoreBuildErrors', recipe)


class PoolTests(unittest.TestCase):
    def test_two_lanes_with_reserve(self):
        result = pool.budget(8, 16 * pool.GIB, 2, 2, 5, 2, 4, 2)
        self.assertEqual(result['cpu_cores'], 4)
        self.assertEqual(result['memory_bytes'], 10 * pool.GIB)

    def test_reject_overcommit(self):
        cases = [(4, 16, 2, 2, 5, 2, 4, 2), (8, 12, 2, 2, 5, 2, 4, 2),
                 (8, 16, 5, 1, 2, 2, 4, 2), (8, 16, 2, 2, 5, 0, 0, 2),
                 (8, 16, 2, 2, 5, 2, 4, 0)]
        for cpu, gib, *rest in cases:
            with self.subTest(rest=rest), self.assertRaises(RuntimeError):
                pool.budget(cpu, gib * pool.GIB, *rest)

    def test_configurable_solver_not_unlimited(self):
        self.assertIn('max-parallelism = 2', pool.config_text(2))
        self.assertIn('max-parallelism = 4', pool.config_text(4))
        for value in (0, -1, 5, True, '2'):
            with self.assertRaises(RuntimeError):
                pool.config_text(value)

    def test_create_never_switches_default_or_shares_cache_root(self):
        command = pool.create_command('deerflow-pool-test-frontend', Path('frontend.toml'),
                                      'moby/buildkit@sha256:' + 'a' * 64, 2, 5 * pool.GIB)
        self.assertNotIn('--use', command)
        self.assertIn('cpu-quota=200000', command)
        self.assertIn('memory-swap=5368709120', command)
        self.assertIn('restart-policy=no', command)

    def test_one_owner_per_lane_but_other_lane_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.lock', Path(tmp) / 'b.lock'
            with pool.lock(a):
                with pool.lock(b):
                    pass
                with self.assertRaises(OSError):
                    with pool.lock(a):
                        self.fail('same lane acquired twice')
            with pool.lock(a):
                pass

    def test_resource_drift_rejected(self):
        row = dict(container='test', memory_bytes=5 * pool.GIB, cpu=2, image_id='sha256:test')
        info = dict(State=dict(Running=True), HostConfig=dict(Memory=0, MemorySwap=0,
                    CpuPeriod=100000, CpuQuota=200000), Image='sha256:test')
        with patch.object(pool, 'docker', return_value=json.dumps([info])):
            with self.assertRaisesRegex(RuntimeError, 'lane_resource_drift'):
                pool.verify_lane(row)

    def test_production_guard(self):
        with patch.object(pool.socket, 'gethostname', return_value='datax-01'):
            with self.assertRaisesRegex(RuntimeError, 'dedicated_build_host_required'):
                pool.dedicated_host()


if __name__ == '__main__':
    unittest.main()
