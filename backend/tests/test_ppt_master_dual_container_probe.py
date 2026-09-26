"""The release canary must prove AIO commands and reads, not just 8788 health."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ppt_master_dual_container_probe.py"
    spec = importlib.util.spec_from_file_location("ppt_master_dual_container_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response(io.BytesIO):
    status = 200


class _Opener:
    def __init__(self, *, command_status="completed", file_content=None):
        self.calls = []
        self.command_status = command_status
        self.file_content = file_content

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_header("Authorization"), request.data, timeout))
        if request.full_url.endswith("/v1/sandbox"):
            payload = {"status": "ok"}
        elif request.full_url.endswith("/v1/shell/exec"):
            payload = {
                "success": True,
                "data": {
                    "status": self.command_status,
                    "exit_code": 0,
                },
            }
        elif request.full_url.endswith("/v1/file/read"):
            payload = {
                "success": True,
                "data": {"content": self.file_content},
            }
        else:
            payload = {"status": "ok"}
        return _Response(json.dumps(payload).encode())


def test_canary_checks_both_containers_and_real_aio_file_api(capsys):
    module = _module()
    opener = _Opener(file_content=module.CANARY_TEXT)
    module.probe(key="test-key", opener=opener)

    assert [call[0] for call in opener.calls] == [
        "http://127.0.0.1:8080/v1/sandbox",
        "http://127.0.0.1:8080/v1/shell/exec",
        "http://127.0.0.1:8080/v1/file/read",
        "http://127.0.0.1:8788/health",
    ]
    assert all(call[1] == "Bearer test-key" for call in opener.calls[:3])
    assert opener.calls[3][1] is None
    assert all(call[3] == 10 for call in opener.calls)
    command_body = json.loads(opener.calls[1][2])
    assert module.CANARY_TEXT in command_body["command"]
    assert json.loads(opener.calls[2][2]) == {"file": module.CANARY_FILE}
    assert "ppt_master_canary_aio_read_file=PASS" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["running", "hard_timeout", "terminated"])
def test_canary_rejects_incomplete_aio_command(status):
    module = _module()
    opener = _Opener(command_status=status, file_content=module.CANARY_TEXT)
    with pytest.raises(RuntimeError, match="AIO shell command did not complete"):
        module.probe(key="test-key", opener=opener)
    assert len(opener.calls) == 2


def test_canary_rejects_wrong_file_content():
    module = _module()
    opener = _Opener(file_content="different")
    with pytest.raises(RuntimeError, match="AIO file API"):
        module.probe(key="test-key", opener=opener)
    assert len(opener.calls) == 3


def test_canary_requires_api_key():
    module = _module()
    with pytest.raises(RuntimeError, match="SANDBOX_API_KEY"):
        module.probe(key="", opener=SimpleNamespace())
