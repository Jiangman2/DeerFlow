import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unpack_rust import unpack


class UnpackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.name = "rustc-1.99.1-aarch64-unknown-linux-gnu"
        self.archive = self.root / (self.name + ".tar.xz")
        self.dest = self.root / "out"
        self.dest.mkdir()

    def write(self, rows):
        with tarfile.open(self.archive, "w:xz") as t:
            for name, kind in rows:
                e = tarfile.TarInfo(name)
                e.type = kind
                e.size = 1 if kind == tarfile.REGTYPE else 0
                e.linkname = "outside"
                e.mode = 0o7777
                t.addfile(e, io.BytesIO(b"x") if e.size else None)

    def test_extract_once_without_archive_permissions(self):
        self.write([(self.name + "/bin/rustc", tarfile.REGTYPE)])
        self.assertEqual(unpack(self.archive, self.dest)["files"], 1)
        self.assertEqual((self.dest / self.name / "bin/rustc").read_bytes(), b"x")
        with self.assertRaisesRegex(ValueError, "new_component"):
            unpack(self.archive, self.dest)

    def test_links_and_special_files_rejected_before_write(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            self.write([(self.name + "/x", kind)])
            with self.assertRaisesRegex(ValueError, "unsafe_archive"):
                unpack(self.archive, self.dest)
            self.assertEqual(list(self.dest.iterdir()), [])

    def test_traversal_and_duplicate_rejected(self):
        for name in ("/etc/x", self.name + "/../x", self.name + "\\x", self.name + "/./x", "other/x"):
            self.write([(name, tarfile.REGTYPE)])
            with self.assertRaisesRegex(ValueError, "unsafe_archive"):
                unpack(self.archive, self.dest)
        self.write([(self.name + "/x", tarfile.REGTYPE)]*2)
        with self.assertRaisesRegex(ValueError, "unsafe_archive"):
            unpack(self.archive, self.dest)


if __name__ == "__main__":
    unittest.main()
