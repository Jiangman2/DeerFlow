"""Cross-platform checksum bytes using the actual frozen build-only input."""

import hashlib
from pathlib import Path

import pytest

from package_candidate import package


def test_frozen_input_archive_emits_linux_checksum_line(tmp_path):
    source = Path(__file__).parent / "candidate-inputs-20260924-v4"
    if not source.is_dir():
        pytest.skip("requires internal frozen v4 inputs")
    archive = tmp_path / "deerflow-employee-sharing-build-inputs-20260924-v4.tar.gz"
    digest = package(source, archive)
    assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert digest == "e51335a4c692e5d8663830909c191dd81a9d95337ed4b5663c2588974c3853d1"
    raw = archive.with_name(archive.name + ".sha256").read_bytes()
    assert raw == (digest + "  " + archive.name + "\n").encode("ascii")
    assert b"\r" not in raw
    assert len(raw.splitlines()) == 1
    checksum, filename = raw[:-1].split(b"  ")
    assert checksum.decode("ascii") == digest
    assert filename.decode("ascii") == archive.name
