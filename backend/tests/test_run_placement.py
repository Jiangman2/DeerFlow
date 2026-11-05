"""The placement decision is based on active Runs, not historic Run totals."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.gateway import run_placement
from app.gateway.run_placement import ReadyPod, RunPlacementSelector


def test_ready_pod_target_uses_only_parsed_ip_address():
    assert ReadyPod("gateway-a", "10.244.0.10").target == "10.244.0.10:8001"
    assert ReadyPod("gateway-v6", "fd00::10").target == "[fd00::10]:8001"


@pytest.mark.anyio
async def test_selector_prefers_fewer_active_runs_and_reserves_rapid_arrivals(monkeypatch):
    selector = RunPlacementSelector(engine=None, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    pods = (ReadyPod("gateway-a", "10.0.0.1"), ReadyPod("gateway-b", "10.0.0.2"))
    monkeypatch.setattr(selector, "_ready_pods", lambda: asyncio.sleep(0, result=pods))
    monkeypatch.setattr(selector, "_active_counts", lambda: asyncio.sleep(0, result={"gateway-a": 3, "gateway-b": 1}))

    first, _, _ = await selector.choose()
    second, _, _ = await selector.choose()
    third, _, _ = await selector.choose()

    assert [pod.name for pod in (first, second, third)] == ["gateway-b", "gateway-b", "gateway-a"]


@pytest.mark.anyio
async def test_burst_placements_stay_even_before_database_rows_appear(monkeypatch):
    selector = RunPlacementSelector(engine=None, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    pods = tuple(ReadyPod(f"gateway-{index}", f"10.0.0.{index + 1}") for index in range(22))
    monkeypatch.setattr(selector, "_ready_pods", lambda: asyncio.sleep(0, result=pods))
    monkeypatch.setattr(selector, "_active_counts", lambda: asyncio.sleep(0, result={}))

    selected = [await selector.choose() for _ in range(800)]
    counts = {pod.name: sum(chosen.name == pod.name for chosen, _, _ in selected) for pod in pods}
    assert max(counts.values()) - min(counts.values()) <= 1


@pytest.mark.anyio
async def test_selector_excludes_unready_or_terminating_pods(monkeypatch, tmp_path: Path):
    token_file = tmp_path / "token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setattr(run_placement, "_KUBE_TOKEN", token_file)

    def pod(name: str, ip: str, *, ready: bool = True, container_ready: bool = True, terminating: bool = False) -> dict:
        metadata = {"name": name}
        if terminating:
            metadata["deletionTimestamp"] = "now"
        return {
            "metadata": metadata,
            "status": {
                "phase": "Running",
                "podIP": ip,
                "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                "containerStatuses": [{"name": "gateway", "ready": container_ready}],
            },
        }

    payload = {
        "items": [
            pod("ready", "10.0.0.1"),
            pod("starting", "10.0.0.2", ready=False, container_ready=False),
            pod("terminating", "10.0.0.3", terminating=True),
            pod("bad-ip", "not-an-ip"),
            pod("unready-gate", "10.0.0.4", ready=False),
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.path == "/api/v1/namespaces/deer-flow/pods"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        selector = RunPlacementSelector(engine=None, client=client, namespace="deer-flow", kube_url="https://kubernetes")
        assert await selector._ready_pods() == (ReadyPod("ready", "10.0.0.1"),)


@pytest.mark.anyio
async def test_selector_fails_closed_without_a_ready_pod(monkeypatch):
    selector = RunPlacementSelector(engine=None, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    monkeypatch.setattr(selector, "_ready_pods", lambda: asyncio.sleep(0, result=()))
    with pytest.raises(RuntimeError, match="No ready Gateway Pod"):
        await selector.choose()


@pytest.mark.anyio
async def test_select_exposes_only_the_target_address(monkeypatch):
    selector = RunPlacementSelector(engine=None, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    monkeypatch.setattr(selector, "_ready_pods", lambda: asyncio.sleep(0, result=(ReadyPod("gateway-a", "10.0.0.1"),)))
    monkeypatch.setattr(selector, "_active_counts", lambda: asyncio.sleep(0, result={"gateway-a": 5}))
    monkeypatch.setattr(run_placement.app.state, "selector", selector, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=run_placement.app), base_url="http://dispatcher") as client:
        response = await client.get("/select")
    assert response.status_code == 204
    assert response.headers["x-deerflow-gateway-target"] == "10.0.0.1:8001"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_select_fails_closed_without_a_ready_gateway(monkeypatch):
    selector = RunPlacementSelector(engine=None, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    monkeypatch.setattr(selector, "_ready_pods", lambda: asyncio.sleep(0, result=()))
    monkeypatch.setattr(run_placement.app.state, "selector", selector, raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=run_placement.app), base_url="http://dispatcher") as client:
        response = await client.get("/select")
    assert response.status_code == 503
    assert "x-deerflow-gateway-target" not in response.headers
