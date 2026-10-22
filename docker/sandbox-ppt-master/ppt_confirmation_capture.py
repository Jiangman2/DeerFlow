"""Disposable offline browser calculator, never an official confirmation writer.

No credentials or project path go to the child. Only a pinned page snapshot is
sent on stdin; stdout contains one bounded proposal and stderr is discarded.
The parent owns a separate process group and enforces a wall-clock deadline.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

INPUT_LIMIT = 10 * 1024 * 1024
OUTPUT_LIMIT = 300 * 1024
DEADLINE_SECONDS = 32
ENV_KEYS = {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "XDG_CACHE_HOME", "PLAYWRIGHT_BROWSERS_PATH"}


def require(ok, label):
    if not ok:
        raise RuntimeError(label)


def pack(snapshot):
    value = {"stage": snapshot.stage, "version": snapshot.version,
             "source_digest": snapshot.source_digest, "owner_version": snapshot.owner_version,
             "recommendations": snapshot.recommendations_json.decode("utf-8"),
             "replies": {key: raw.decode("utf-8") for key, raw in snapshot.replies.items()},
             "assets": {key: raw.decode("utf-8") for key, raw in snapshot.assets.items()}}
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    require(len(raw) <= INPUT_LIMIT, "confirmation_capture_input_limit")
    return raw


def unpack(raw, proposal):
    require(len(raw) <= INPUT_LIMIT, "confirmation_capture_input_limit")
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {"stage", "version", "source_digest", "owner_version", "recommendations", "replies", "assets"}, "invalid_capture_snapshot")
    return proposal.PageSnapshot(value["stage"], value["version"], value["source_digest"], value["recommendations"].encode(),
                                 MappingProxyType({key: text.encode() for key, text in value["replies"].items()}),
                                 MappingProxyType({key: text.encode() for key, text in value["assets"].items()}), owner_version=value["owner_version"])


def capture(snapshot, *, proposal):
    source = Path(__file__).resolve()
    require(not Path(__file__).is_symlink(), "unsafe_capture_helper")
    child = subprocess.Popen([sys.executable, "-I", "-B", str(source), "--capture-child"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             cwd=str(source.parent), env={key: value for key, value in os.environ.items() if key.upper() in ENV_KEYS},
                             start_new_session=os.name == "posix", shell=False)
    try:
        raw, _ = child.communicate(pack(snapshot), timeout=DEADLINE_SECONDS)
        require(child.returncode == 0 and len(raw) <= OUTPUT_LIMIT, "confirmation_capture_failed")
        value = json.loads(raw)
        require(isinstance(value, dict) and set(value) == {"payload", "console_errors", "confirmation_submitted"}
                and type(value["console_errors"]) is int and value["console_errors"] == 0
                and isinstance(value["payload"], dict) and value["confirmation_submitted"] is False,
                "invalid_capture_result")
        payload = json.dumps(value["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        require(len(payload) <= proposal.MAX_PAYLOAD_BYTES, "confirmation_capture_output_limit")
        import hashlib

        return proposal.CapturedProposal(snapshot.version, snapshot.source_digest, hashlib.sha256(payload).hexdigest(), payload,
                                         "Official Confirm UI", 0, 1, owner_version=snapshot.owner_version)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("confirmation_capture_unavailable") from None
    finally:
        if child.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
            except ProcessLookupError:
                pass
            child.wait(timeout=3)
        for pipe in (child.stdin, child.stdout):
            if pipe is not None:
                pipe.close()


async def calculate(snapshot, proposal, *, browser_executable=None):
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        # Optional executable is used only by isolated local qualification;
        # the child entry point never accepts it from stdin/CLI/environment.
        browser = await playwright.chromium.launch(headless=True, timeout=5000, executable_path=browser_executable)
        try:
            captured = await proposal.capture_proposal(browser, snapshot)
            return {"payload": captured.payload, "console_errors": captured.console_errors,
                    "confirmation_submitted": captured.confirmation_submitted}
        finally:
            await asyncio.wait_for(browser.close(), timeout=5)


def main():
    # -I deliberately excludes ambient PYTHONPATH and user site-packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ppt_confirmation_proposal as proposal

    require(sys.argv[1:] == ["--capture-child"], "invalid_capture_command")
    snapshot = unpack(sys.stdin.buffer.read(INPUT_LIMIT + 1), proposal)
    value = asyncio.run(calculate(snapshot, proposal))
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    require(len(raw) <= OUTPUT_LIMIT, "confirmation_capture_output_limit")
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Browser diagnostics may include source/user content. Never print them.
        sys.exit(1)
