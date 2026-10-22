import gzip
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest

spec = importlib.util.spec_from_file_location('allocation', Path(__file__).with_name('scan_allocation.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
        folder = tarfile.TarInfo('a')
        folder.type = tarfile.DIRTYPE
        archive.addfile(folder)
        for name, size in [('a/one', 1), ('a/deep/two', 4097), ('a/.wh.gone', 0)]:
            entry = tarfile.TarInfo(name)
            entry.size = size
            if size == 1:
                entry.pax_headers = {'SCHILY.xattr.user.test': 'value'}
            archive.addfile(entry, io.BytesIO(b'x' * size))
        for name, typ in [('a/sym', tarfile.SYMTYPE), ('a/hard', tarfile.LNKTYPE)]:
            entry = tarfile.TarInfo(name)
            entry.type, entry.linkname = typ, 'a/one'
            archive.addfile(entry)
    return out.getvalue()


class Tests(unittest.TestCase):
    def test_counts_alignment_links_xattr_parents_and_padding(self):
        raw = fixture()
        reader = m.Reader(io.BytesIO(raw), len(raw))
        out = m.tar_stats(reader)
        self.assertEqual((out['regular'], out['directories'], out['symlinks'], out['hardlinks']), (3, 1, 1, 1))
        self.assertEqual(out['regular_data_bytes'], 4098)
        self.assertEqual(out['regular_4k_roundup_bytes'], 12288)
        self.assertEqual((out['xattr_records'], out['xattr_files'], out['xattr_value_bytes']), (1, 1, 5))
        self.assertEqual((out['parent_directory_paths'], out['parents_not_explicit_directories']), (2, 1))
        self.assertEqual(out['whiteout_markers'], 1)
        self.assertEqual(out['entries'], 6)
        self.assertEqual(out['headers'], 7)
        self.assertEqual(reader.hash.hexdigest(), hashlib.sha256(raw).hexdigest())

    def test_gzip_digest_and_expanded_diff_id(self):
        raw = fixture()
        compressed = gzip.compress(raw, mtime=0)
        outer = io.BytesIO()
        with tarfile.open(fileobj=outer, mode='w') as archive:
            entry = tarfile.TarInfo('blob')
            entry.size = len(compressed)
            archive.addfile(entry, io.BytesIO(compressed))
        desc = dict(size=len(compressed), digest='sha256:' + m.sha(compressed), mediaType='application/vnd.oci.image.layer.v1.tar+gzip')
        layer = dict(diff_id='sha256:' + m.sha(raw), uncompressed_bytes_upper_bound=len(raw), bound_kind='VERIFIED_EXACT')
        outer.seek(0)
        with tarfile.open(fileobj=outer, mode='r:') as archive:
            result = m.scan_blob(archive, archive.getmember('blob'), desc, layer)
            self.assertTrue(result['compressed_digest_verified'] and result['diff_id_verified'])
            layer['diff_id'] = 'sha256:' + '0' * 64
            with self.assertRaisesRegex(m.Refused, 'expanded_diff_id'):
                m.scan_blob(archive, archive.getmember('blob'), desc, layer)

    def test_unknown_special_sparse_and_oversized_headers_rejected(self):
        for kind in (tarfile.GNUTYPE_SPARSE, tarfile.CHRTYPE, b'Z'):
            entry = tarfile.TarInfo('special')
            entry.type = kind
            raw = entry.tobuf() + bytes(1024)
            with self.assertRaisesRegex(m.Refused, 'unsupported_or_sparse_header_type'):
                m.tar_stats(m.Reader(io.BytesIO(raw), len(raw)))
        entry = tarfile.TarInfo('extended')
        entry.type, entry.size = tarfile.XHDTYPE, 65537
        with self.assertRaisesRegex(m.Refused, 'extended_header_bound'):
            m.tar_stats(m.Reader(io.BytesIO(entry.tobuf()), 100000))

    def test_pax_sparse_and_path_escape_rejected(self):
        for key, value in [('GNU.sparse.size', '10'), ('SCHILY.realsize', '10'), ('SCHILY.filetype', 'sparse')]:
            body = (key + '=' + value + '\n').encode()
            count = len(body) + 3
            while count != len(str(count)) + 1 + len(body):
                count = len(str(count)) + 1 + len(body)
            with self.assertRaisesRegex(m.Refused, 'sparse_pax_rejected'):
                m.pax_parse(str(count).encode() + b' ' + body)
        for path in (b'../bad', b'/absolute', b'a/../bad', b'a\\bad'):
            with self.assertRaises(m.Refused):
                m.normalize(path)

    def test_stream_bound_truncated_and_nonzero_tail_rejected(self):
        raw = fixture()
        with self.assertRaisesRegex(m.Refused, 'stream_size_bound'):
            m.tar_stats(m.Reader(io.BytesIO(raw), 100))
        with self.assertRaisesRegex(m.Refused, 'truncated_tar'):
            m.tar_stats(m.Reader(io.BytesIO(b'x'), 100))
        with self.assertRaisesRegex(m.Refused, 'nonzero_tar_tail'):
            m.tar_stats(m.Reader(io.BytesIO(bytes(1024) + b'x'), 1025))


if __name__ == '__main__':
    unittest.main()
