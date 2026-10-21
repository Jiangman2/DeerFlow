"""Exercise the packaged remote entry with real SDK requests and offline replies."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("glm_mode_diagnostic", ROOT / "deploy/enterprise/diagnose-glm-modes.py")
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


@pytest.mark.parametrize("upstream_failure", [False, True])
def test_remote_candidate_uses_existing_key_and_keeps_live_config(monkeypatch, capsys, upstream_failure):
    profile = yaml.safe_load((ROOT / "deploy/enterprise/glm-5.3-flash-switchable.model.yaml").read_text())["models"][0]
    old = yaml.safe_load((ROOT / "deploy/enterprise/glm-5.3-flash.model.yaml").read_text())["models"][0]
    old["api_key"] = "synthetic-existing-key"
    config = AppConfig(models=[old], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    before = config.model_dump()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    monkeypatch.setenv("GLM_5_3_FLASH_API_KEY", "synthetic-existing-key")
    for name in ("SSL_CERT_FILE", "GLM_5_3_FLASH_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)
    data = dict(profile=profile,
                adapter=(ROOT / "backend/packages/harness/deerflow/models/enterprise_glm.py").read_text(),
                probe=(ROOT / "backend/app/gateway/enterprise_glm_probe.py").read_text())
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
    requests = []

    def respond(request):
        assert request.headers["authorization"] == "Bearer synthetic-existing-key"
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests) - 1
        assert body["thinking"]["type"] == ("enabled" if index in (1, 4) else "disabled")
        if upstream_failure:
            return httpx.Response(503, json={"error": {"message": "PRIVATE-UPSTREAM", "type": "fixture"}})
        delta = dict(role="assistant", content="红色" if index == 4 else "GLM_PROBE_OK")
        if index == 1:
            delta["reasoning_content"] = "synthetic reasoning"
        if index == 2:
            delta = dict(role="assistant", content="", tool_calls=[dict(index=0, id="call1", type="function", function=dict(name="probe_echo", arguments='{"value":"GLM_PROBE_OK"}'))])
        frame = dict(id="fixture", object="chat.completion.chunk", created=1, model="GLM-5.3-Flash", choices=[dict(index=0, delta=delta, finish_reason="tool_calls" if index == 2 else "stop")])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n")

    client = httpx.AsyncClient

    class OfflineClient(client):
        def __init__(self, **kwargs):
            if "verify" in kwargs:
                assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
                assert kwargs["verify"] is not False
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    # Restore the candidate module injection and logging state after this child simulation.
    import logging

    previous = sys.modules.get("deerflow.models.enterprise_glm")
    disabled = logging.root.manager.disable
    try:
        # Synthetic fixtures only: surface unexpected setup exceptions to pytest.
        remote = diagnostic.REMOTE.replace('result["error_class"]=type(e).__name__', 'raise')
        exec(compile(remote, "<remote-fixture>", "exec"), {"__name__": "diagnostic_child"})
    finally:
        sys.modules["deerflow.models.enterprise_glm"] = previous
        logging.disable(disabled)
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == ("INCOMPLETE" if upstream_failure else "CANDIDATE_MODES_AND_VISION_ACCEPTED_NOT_DEPLOYED"), result
    assert len(requests) == (1 if upstream_failure else 5)
    assert result["current_capabilities"]["thinking_always_on"] is True
    assert result["candidate_factory_modes_verified"] is True
    assert "synthetic-existing-key" not in output and "PRIVATE-UPSTREAM" not in output
    assert config.model_dump() == before
