import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

spec = importlib.util.spec_from_file_location('hardlinks', Path(__file__).with_name('review_hardlinks.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture(rows):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        for name, kind, value in rows:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
            else:
                member.linkname = value
                archive.addfile(member)
    raw = data.getvalue()
    return m.link_stats(m.M.Reader(io.BytesIO(raw), len(raw)))


class Tests(unittest.TestCase):
    def test_prior_regular_and_hardlink_chain(self):
        out = fixture([('a', tarfile.REGTYPE, b'12345'), ('b', tarfile.LNKTYPE, 'a'),
                       ('c', tarfile.LNKTYPE, './b'), ('sym', tarfile.SYMTYPE, '/absolute/target')])
        self.assertTrue(out['all_hardlinks_resolve_to_prior_same_layer_regular'])
        self.assertEqual(out['distinct_target_payload_bytes'], 5)
        self.assertEqual(out['target_payload_bytes_per_link_sum'], 10)
        self.assertEqual(out['rows'][1]['ultimate_regular_path'], 'a')
        self.assertEqual(out['maximum_symlink_target_bytes'], 16)

    def test_absent_forward_and_symlink_targets_stay_unresolved(self):
        out = fixture([('b', tarfile.LNKTYPE, 'later'), ('later', tarfile.REGTYPE, b'x'),
                       ('sym', tarfile.SYMTYPE, 'later'), ('c', tarfile.LNKTYPE, 'sym')])
        self.assertEqual(out['requires_lower_target_count'], 2)
        self.assertFalse(out['all_hardlinks_resolve_to_prior_same_layer_regular'])

    def test_unsafe_target_and_prior_symlink_parent_are_not_inferred(self):
        out = fixture([('a', tarfile.REGTYPE, b'x'), ('parent', tarfile.SYMTYPE, '/elsewhere'),
                       ('parent/child', tarfile.LNKTYPE, 'a'), ('bad', tarfile.LNKTYPE, '../a')])
        self.assertEqual(out['requires_lower_target_count'], 2)
        self.assertEqual(out['rows'][1]['resolution'], 'TARGET_PATH_UNSUPPORTED')


if __name__ == '__main__':
    unittest.main()
