import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from deerflow.utils import isolated_conversion as conversion
from deerflow.utils.bounded_work import WorkCapacity


def test_capacity_and_repeated_cancellation_hold_until_worker_exit(monkeypatch):
    entered, exiting = threading.Event(), threading.Event()

    def slow(source, converter, cancelled):
        entered.set()
        assert cancelled.wait(2)
        assert exiting.wait(2)

    capacity = WorkCapacity(active=1, waiting=0)
    monkeypatch.setattr(conversion, "CAPACITY", capacity)
    monkeypatch.setattr(conversion, "_supervise", slow)

    async def run():
        task = asyncio.create_task(conversion.convert_isolated("fake", "auto"))
        try:
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            await asyncio.wait_for(asyncio.sleep(0.01), 0.2)
            with pytest.raises(TimeoutError):
                await conversion.convert_isolated("other", "auto")
            task.cancel()
            await asyncio.sleep(0.02)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            assert capacity.active == 1
        finally:
            exiting.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert capacity.active == 0

    asyncio.run(run())


def test_waiter_timeout_and_cancellation_balance():
    capacity = WorkCapacity(active=1, waiting=1, admission_seconds=0.04)

    async def run():
        await capacity.acquire()
        with pytest.raises(TimeoutError):
            await capacity.acquire()
        assert capacity.waiting == 0
        task = asyncio.create_task(capacity.acquire())
        await asyncio.sleep(0)
        with pytest.raises(TimeoutError):
            await capacity.acquire()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        capacity.release()
        assert capacity.waiting == capacity.active == 0

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process group/resource enforcement")
def test_supervisor_kills_child_on_deadline(tmp_path, monkeypatch):
    real = subprocess.Popen
    children = []

    def sleeping(command, **kwargs):
        p = real([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(p)
        return p

    monkeypatch.setattr(conversion.subprocess, "Popen", sleeping)
    monkeypatch.setattr(conversion, "WALL_SECONDS", 0.1)
    with pytest.raises(TimeoutError):
        conversion._supervise(tmp_path / "fake", "auto", threading.Event())
    assert children[0].poll() is not None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process group/resource enforcement")
def test_real_small_xlsx_and_worker_failure_do_not_publish_partial(tmp_path):
    import openpyxl

    source = tmp_path / "table.xlsx"
    book = openpyxl.Workbook()
    book.active.append(["Name", "Value"])
    book.active.append(["测试", 123])
    book.save(source)
    text = asyncio.run(conversion.convert_isolated(source, "auto"))
    assert "测试" in text and "123" in text
    # MarkItDown detects content: plain text with an XLSX suffix can succeed.
    # A missing input deterministically exercises the real worker failure path.
    source.unlink()
    with pytest.raises(RuntimeError, match="worker failed"):
        asyncio.run(conversion.convert_isolated(source, "auto"))
    assert conversion.CAPACITY.active == 0


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process group enforcement")
@pytest.mark.parametrize("existing_output", [False, True])
def test_failed_child_partial_output_is_discarded(tmp_path, monkeypatch, existing_output):
    from deerflow.utils.file_conversion import convert_file_to_markdown

    source = tmp_path / "source.xlsx"
    original = b"synthetic input for controlled worker failure"
    source.write_bytes(original)
    published = source.with_suffix(".md")
    if existing_output:
        published.write_bytes(b"previous complete conversion")
    real_popen = subprocess.Popen
    children, private_outputs = [], []
    receipt = tmp_path / "worker-entered"

    def partial_then_fail(command, **kwargs):
        output = Path(command[4])
        private_outputs.append(output)
        child = real_popen(
            [
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'PARTIAL'); pathlib.Path(sys.argv[2]).write_text('wrote partial'); sys.exit(7)",
                str(output),
                str(receipt),
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(conversion.subprocess, "Popen", partial_then_fail)
    assert asyncio.run(convert_file_to_markdown(source)) is None
    assert receipt.read_text() == "wrote partial"
    assert len(children) == 1 and children[0].returncode == 7
    assert all(not path.parent.exists() for path in private_outputs)
    assert conversion.CAPACITY.active == 0
    assert source.read_bytes() == original
    if existing_output:
        assert published.read_bytes() == b"previous complete conversion"
    else:
        assert not published.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux resource limits")
@pytest.mark.parametrize("mode", ["memory", "output", "cpu"])
def test_worker_limits_set_before_converter_import(tmp_path, mode):
    # A substitute package exercises the real worker entry point and resource
    # limits without loading any production documents or allocating in Gateway.
    import shutil

    root = tmp_path / "harness"
    utils = root / "deerflow" / "utils"
    utils.mkdir(parents=True)
    (root / "deerflow" / "__init__.py").write_text("")
    (utils / "__init__.py").write_text("")
    code = {"memory": "return 'x' * (512 * 1024 * 1024)", "output": "return 'x' * 4096", "cpu": "while True: pass"}[mode]
    (utils / "file_conversion.py").write_text("from pathlib import Path\ndef _do_convert(*args, bounded_threads=False):\n    assert bounded_threads is True\n    Path(__file__).with_name('entered').write_text('entered')\n    " + code + "\n")
    worker = utils / "conversion_worker.py"
    shutil.copyfile(Path(conversion.__file__).with_name("conversion_worker.py"), worker)
    started = time.monotonic()
    p = subprocess.run([sys.executable, str(worker), "fake", "auto", str(tmp_path / "out"), str(128 * 1024 * 1024), "1024", "1"], capture_output=True, timeout=8)
    assert p.returncode != 0
    assert (utils / "entered").read_text() == "entered"
    assert time.monotonic() - started < 8
