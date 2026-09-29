"""Archive integrity checks; no production access or application execution."""

from pathlib import Path
import tempfile
import subprocess
import unittest

import archive_local


class ArchiveTests(unittest.TestCase):
    def test_git_snapshot_preserves_raw_bytes_and_export_ignored_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            (root / ".gitattributes").write_bytes(
                b"*.cmd text eol=crlf\nhidden export-ignore\n"
            )
            (root / "windows.cmd").write_bytes(b"@echo off\necho example\n")
            (root / "hidden").write_bytes(b"must be included\n")
            subprocess.run(
                ["git", "-C", str(root), "add", "."], check=True, capture_output=True
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Archive Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    "fixture",
                ],
                check=True,
                capture_output=True,
            )
            commit = (
                subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"])
                .decode()
                .strip()
            )
            result = archive_local.source_snapshot(root, root, commit)
            self.assertEqual(result["verified_git_blobs"], 3)
            with self.assertRaises(FileExistsError):
                archive_local.source_snapshot(root, root, commit)

    def test_roundtrip_preserves_source_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.tar.gz"
            payload = b"opaque original package bytes"
            source.write_bytes(payload)
            archive = root / "bundle.tar"
            archive_local.pack([("history/original.tar.gz", source)], archive)
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(archive_local.verify(archive), 1)
            raw = archive.read_bytes()
            index = raw.index(payload)
            archive.write_bytes(raw[:index] + b"X" + raw[index + 1 :])
            with self.assertRaisesRegex(ValueError, "integrity"):
                archive_local.verify(archive)

    def test_rejects_unsafe_or_duplicate_names_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original"
            source.write_bytes(b"unchanged")
            for index, name in enumerate(
                ("../outside", "/absolute", "a\\b", "MANIFEST.json")
            ):
                with self.assertRaises(ValueError):
                    archive_local.pack([(name, source)], root / (str(index) + ".tar"))
            with self.assertRaises(ValueError):
                archive_local.pack(
                    [("x", source), ("x", source)], root / "duplicate.tar"
                )
            with self.assertRaises(FileExistsError):
                archive_local.pack([("x", source)], source)
            self.assertEqual(source.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
