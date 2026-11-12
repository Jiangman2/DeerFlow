"""Bound local conversion outside Gateway, with no converter stdout in memory."""

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .bounded_work import WorkCapacity, owned_call

# Per Gateway process. One child leaves headroom in the enterprise 4 GiB Pod.
# RLIMIT_AS caps virtual address space, not a guarantee of whole-Pod RSS.
MEMORY_BYTES = 1024 * 1024 * 1024
OUTPUT_BYTES = 16 * 1024 * 1024
CPU_SECONDS = 90
WALL_SECONDS = 120.0
CAPACITY = WorkCapacity()


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _supervise(source, converter, cancelled):
    if sys.platform != "linux":
        raise RuntimeError("Bounded document conversion requires Linux resource limits")
    with tempfile.TemporaryDirectory(prefix="deerflow-convert-") as directory:
        output = Path(directory) / "result.md"
        env = dict(os.environ)
        env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
        command = [sys.executable, str(Path(__file__).with_name("conversion_worker.py")), str(Path(source).resolve()), converter, str(output), str(MEMORY_BYTES), str(OUTPUT_BYTES), str(CPU_SECONDS)]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
        deadline = time.monotonic() + WALL_SECONDS
        try:
            while process.poll() is None:
                if cancelled.wait(0.05):
                    raise RuntimeError("Document conversion cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Document conversion deadline exceeded")
            if cancelled.is_set():
                raise RuntimeError("Document conversion cancelled")
            if process.returncode != 0:
                raise RuntimeError(f"Document conversion worker failed (exit {process.returncode}); resource limit or invalid document")
            with output.open("rb") as stream:
                raw = stream.read(OUTPUT_BYTES + 1)
            if len(raw) > OUTPUT_BYTES:
                raise RuntimeError("Document conversion output limit exceeded")
            return raw.decode("utf-8")
        finally:
            # Also remove any subprocesses left by a converter after leader exit.
            _kill_group(process)


async def convert_isolated(source, converter):
    await CAPACITY.acquire()
    cancelled = threading.Event()
    try:
        return await owned_call(_supervise, source, converter, cancelled, on_cancel=cancelled.set)
    finally:
        CAPACITY.release()
