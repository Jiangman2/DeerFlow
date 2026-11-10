"""Linux integration: cancellation must reap the actual conversion process."""

import asyncio
import os
import subprocess
import sys

import pytest

from deerflow.utils import isolated_conversion as conversion
from deerflow.utils.bounded_work import WorkCapacity


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process group enforcement")
def test_real_child_cancel_releases_capacity_only_after_process_exit(tmp_path, monkeypatch):
    real = subprocess.Popen
    children = []

    def sleeping(command, **kwargs):
        child = real([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(conversion.subprocess, "Popen", sleeping)
    monkeypatch.setattr(conversion, "CAPACITY", WorkCapacity())

    async def run():
        task = asyncio.create_task(conversion.convert_isolated(tmp_path / "fake", "auto"))
        for _ in range(400):
            if children:
                break
            await asyncio.sleep(0.005)
        assert children
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        assert children[0].poll() is not None
        with pytest.raises(ProcessLookupError):
            os.kill(children[0].pid, 0)
        assert conversion.CAPACITY.active == 0

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process group enforcement")
def test_sparse_high_row_xlsx_fails_bounded_without_publishing_truncated_text(tmp_path, monkeypatch):
    import hashlib

    import openpyxl

    from deerflow.utils.file_conversion import convert_file_to_markdown

    source = tmp_path / "sparse.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "Header"
    workbook.active["AG1048570"] = "REAL_PAYLOAD_MUST_NOT_BE_DISCARDED"
    workbook.save(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(conversion, "WALL_SECONDS", 3.0)

    async def run():
        task = asyncio.create_task(convert_file_to_markdown(source))
        ticks = 0
        while not task.done():
            ticks += 1
            await asyncio.sleep(0.02)
        assert await task is None
        assert ticks >= 2
        assert conversion.CAPACITY.active == 0

    asyncio.run(asyncio.wait_for(run(), 10))
    assert not source.with_suffix(".md").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
