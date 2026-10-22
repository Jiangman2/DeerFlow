"""Narrow cache diagnostic fixtures. No daemon, SSH, archive or production access."""
import copy
import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('cache_diagnostic_fixture', HERE / 'collect_cache_template.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
GIB = 1024 ** 3


def digest(n):
    return 'sha256:' + format(n, '064x')


def layer(n, parent='', expanded=100):
    return dict(chain_id=digest(n), parent=parent, uncompressed_bytes_upper_bound=expanded)


def plan_fixture():
    layers = [layer(1, expanded=100), layer(2, digest(1), 200), layer(3, digest(2), 300)]
    return dict(images=[dict(role='fixture', layers=layers, expanded_groups=[])],
                chains={row['chain_id']: dict(row) for row in layers},
                blobs={digest(11): 20, digest(12): 50, digest(13): 80},
                maximum_outer_blob_bytes=80)


def sample_fixture(plan, present=(), prefix=0):
    image = dict(path='/var/lib/containerd', device=1, capacity_bytes=100 * GIB,
                 available_bytes=50 * GIB, available_inodes=200000)
    snapshots = {}
    for row in plan['images'][0]['layers'][:prefix]:
        snapshots[row['chain_id']] = dict(name=row['chain_id'], parent=row['parent'], kind='Committed')
    return dict(content=list(present), snapshots=snapshots, image_filesystems=[image],
                root=dict(image, path='/var/lib'), opt=dict(image, path='/opt', device=2))


class CacheDiagnosticTests(unittest.TestCase):
    def test_content_exact_filter_subset_empty_and_order(self):
        wanted = [digest(11), digest(12)]
        self.assertEqual(c.parse_content(b'', wanted), [])
        self.assertEqual(c.parse_content((wanted[1] + '\n' + wanted[0] + '\n').encode(), wanted), wanted)

    def test_content_duplicate_unrequested_and_nonascii_rejected(self):
        wanted = [digest(11)]
        for raw in ((wanted[0] + '\n' + wanted[0]).encode(), digest(99).encode(), b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                c.parse_content(raw, wanted)

    def test_snapshot_root_parent_projection_and_unrelated_rows(self):
        plan = plan_fixture()
        raw = ('KEY PARENT KIND\n' + digest(1) + '  Committed\n' +
               digest(2) + ' ' + digest(1) + ' Committed\n' +
               'not-relevant arbitrary untrusted columns ignored\n').encode()
        result = c.parse_snapshots(raw, plan['chains'])
        self.assertEqual(set(result), {digest(1), digest(2)})
        self.assertEqual(result[digest(1)], dict(name=digest(1), parent='', kind='Committed'))
        self.assertEqual(result[digest(2)]['parent'], digest(1))

    def test_snapshot_bad_header_parent_kind_duplicate_or_shape_rejected(self):
        plan = plan_fixture()
        rows = [b'NAME PARENT KIND\n',
                ('KEY PARENT KIND\n' + digest(2) + ' ' + digest(99) + ' Committed\n').encode(),
                ('KEY PARENT KIND\n' + digest(1) + ' Unknown\n').encode(),
                ('KEY PARENT KIND\n' + (digest(1) + ' Committed\n') * 2).encode(),
                ('KEY PARENT KIND\n' + digest(1) + ' a b c\n').encode()]
        for raw in rows:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                c.parse_snapshots(raw, plan['chains'])

    def test_all_cached_still_charges_actual_maximum_blob_scratch(self):
        plan = plan_fixture()
        sample = sample_fixture(plan, plan['blobs'], prefix=3)
        result = c.estimate(plan, sample, copy.deepcopy(sample))
        self.assertEqual(result['missing_content_bytes'], 0)
        self.assertEqual(result['missing_expanded_bytes_upper_bound'], 0)
        self.assertEqual(result['import_scratch_maximum_actual_blob'], 80)
        self.assertEqual(result['required_bytes'], 22 * GIB + 80)
        self.assertFalse(result['ready_for_import'])

    def test_credit_requires_content_at_both_and_contiguous_committed_ancestry(self):
        plan = plan_fixture()
        first = sample_fixture(plan, [digest(11), digest(12)], prefix=3)
        last = sample_fixture(plan, [digest(11), digest(13)], prefix=3)
        del last['snapshots'][digest(2)]
        result = c.estimate(plan, first, last)
        self.assertEqual(result['missing_content_bytes'], 130)
        self.assertEqual(result['missing_expanded_bytes_upper_bound'], 500)
        self.assertEqual(result['images'][0]['committed_prefix_at_both_samples'], 1)

    def test_active_or_view_snapshot_stops_credit_to_descendants(self):
        for kind in ('Active', 'View'):
            plan = plan_fixture()
            sample = sample_fixture(plan, prefix=3)
            sample['snapshots'][digest(2)]['kind'] = kind
            result = c.estimate(plan, sample, copy.deepcopy(sample))
            self.assertEqual(result['missing_expanded_bytes_upper_bound'], 500)

    def test_shared_chains_across_images_count_once(self):
        plan = plan_fixture()
        plan['images'].append(copy.deepcopy(plan['images'][0]))
        plan['images'][-1]['role'] = 'rollback'
        sample = sample_fixture(plan)
        result = c.estimate(plan, sample, copy.deepcopy(sample))
        self.assertEqual(result['missing_expanded_bytes_upper_bound'], 600)
        self.assertEqual(len(result['images']), 2)

    def test_frontend_unknown_group_uses_one_aggregate_cap_including_partial_cache(self):
        plan = plan_fixture()
        for row in plan['images'][0]['layers']:
            row['uncompressed_bytes_upper_bound'] = 1000
            plan['chains'][row['chain_id']]['uncompressed_bytes_upper_bound'] = 1000
        plan['images'][0]['expanded_groups'] = [dict(layer_positions=[0, 1, 2], uncompressed_bytes_upper_bound=1000)]
        for prefix, expected in ((0, 1000), (1, 1000), (2, 1000), (3, 0)):
            with self.subTest(prefix=prefix):
                sample = sample_fixture(plan, prefix=prefix)
                result = c.estimate(plan, sample, copy.deepcopy(sample))
                self.assertEqual(result['missing_expanded_bytes_upper_bound'], expected)
                self.assertEqual(result['images'][0]['expanded_missing_upper_bound'], expected)

    def test_overlapping_expanded_groups_rejected(self):
        plan = plan_fixture()
        plan['images'][0]['expanded_groups'] = [dict(layer_positions=[0, 1], uncompressed_bytes_upper_bound=200),
                                               dict(layer_positions=[1, 2], uncompressed_bytes_upper_bound=300)]
        sample = sample_fixture(plan)
        with self.assertRaisesRegex(ValueError, 'expanded_group_overlap'):
            c.estimate(plan, sample, sample)

    def test_same_device_or_changed_filesystem_identity_rejected(self):
        plan = plan_fixture()
        for field in ('same_device', 'image_device', 'opt_capacity', 'root_device', 'image_path'):
            first = sample_fixture(plan)
            last = copy.deepcopy(first)
            if field == 'same_device':
                first['opt']['device'] = last['opt']['device'] = 1
            elif field == 'image_device':
                last['image_filesystems'][0]['device'] = 7
            elif field == 'opt_capacity':
                last['opt']['capacity_bytes'] += 1
            elif field == 'root_device':
                last['root']['device'] = 7
            else:
                last['image_filesystems'][0]['path'] = '/changed'
            with self.subTest(field=field), self.assertRaises(ValueError):
                c.estimate(plan, first, last)

    def test_latest_space_and_inode_shortage_report_false_without_release_claim(self):
        plan = plan_fixture()
        first = sample_fixture(plan)
        last = copy.deepcopy(first)
        last['image_filesystems'][0].update(available_bytes=1, available_inodes=1)
        result = c.estimate(plan, first, last)
        self.assertFalse(result['bytes_fit_at_sample'])
        self.assertFalse(result['inode_fit_at_sample'])
        self.assertFalse(result['ready_for_import'])
        self.assertEqual(result['available_bytes'], 1)

    def test_packed_inputs_bounded_and_no_trailing_stream(self):
        import base64
        import zlib
        value = dict(example=['safe', 3])
        self.assertEqual(c.unpack(c.pack(value)), value)
        for raw in (zlib.compress(b'{}') + b'extra', zlib.compress(b'x' * (2 * 1024 ** 2 + 1))):
            with self.assertRaises(ValueError):
                c.unpack(base64.b64encode(raw).decode())


if __name__ == '__main__':
    unittest.main()
