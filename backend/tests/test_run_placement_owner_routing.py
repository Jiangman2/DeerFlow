"""Offline regression gate; no real database, Kubernetes, or Gateway calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.gateway import run_placement as module

ReadyPod = module.ReadyPod
RunPlacementSelector = module.RunPlacementSelector


def run(coro):
    return asyncio.run(coro)


class Engine:
    def __init__(self, row=None, error=None):
        self.row, self.error, self.calls = row, error, []

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, parameters=None):
        self.calls.append((str(sql), parameters))
        if self.error:
            raise self.error
        return self

    def mappings(self):
        return self

    def first(self):
        return self.row

    def __iter__(self):
        return iter([SimpleNamespace(pod_name="gateway-a", active_runs=2)])


def selector(monkeypatch, row=None, pods=None, error=None):
    engine = Engine(row, error)
    target = RunPlacementSelector(engine=engine, client=None, namespace="deer-flow", kube_url="https://kubernetes")
    targets = pods if pods is not None else (ReadyPod("gateway-a", "10.0.0.1"), ReadyPod("gateway-b", "10.0.0.2"))
    refreshes = []

    async def ready(*, refresh=False):
        refreshes.append(refresh)
        return targets

    monkeypatch.setattr(target, "_ready_pods", ready)
    return target, engine, refreshes


def existing(target, **overrides):
    args = dict(
        thread_id="thread-1",
        run_id="run-1",
        operation="cancel",
        method="POST",
        action="interrupt",
    )
    args.update(overrides)
    return run(target.existing_target(**args))


def test_target_ip_parsing():
    assert ReadyPod("a", "10.0.0.1").target == "10.0.0.1:8001"
    assert ReadyPod("v6", "fd00::10").target == "[fd00::10]:8001"
    with pytest.raises(ValueError):
        _ = ReadyPod("bad", "attacker:8001/path").target


def test_least_active_selection_and_reservations_unchanged(monkeypatch):
    target, _, _ = selector(monkeypatch)
    monkeypatch.setattr(
        target,
        "_active_counts",
        lambda: asyncio.sleep(0, result={"gateway-a": 3, "gateway-b": 1}),
    )

    async def burst():
        return [await target.choose() for _ in range(3)]

    assert [item[0].name for item in run(burst())] == [
        "gateway-b",
        "gateway-b",
        "gateway-a",
    ]


def test_800_new_run_selections_stay_balanced(monkeypatch):
    pods = tuple(ReadyPod(f"gateway-{i}", f"10.0.0.{i + 1}") for i in range(22))
    target, _, _ = selector(monkeypatch, pods=pods)
    monkeypatch.setattr(target, "_active_counts", lambda: asyncio.sleep(0, result={}))

    async def burst():
        return [await target.choose() for _ in range(800)]

    choices = run(burst())
    counts = [sum(item[0].name == pod.name for item in choices) for pod in pods]
    assert max(counts) - min(counts) <= 1


def kube_pod(name, ip, *, ready=True, deleting=False, container=True, phase="Running"):
    return {
        "metadata": {
            "name": name,
            **({"deletionTimestamp": "now"} if deleting else {}),
        },
        "status": {
            "phase": phase,
            "podIP": ip,
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [{"name": "gateway", "ready": container}],
        },
    }


def test_readiness_filters_and_forced_refresh(monkeypatch, tmp_path):
    token = tmp_path / "token"
    token.write_text("test-token", encoding="utf-8")
    monkeypatch.setattr(module, "_KUBE_TOKEN", token)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.params["labelSelector"] == "app=gateway"
        return httpx.Response(
            200,
            json={
                "items": [
                    kube_pod("good", "10.0.0.1"),
                    kube_pod("bad", "not-ip"),
                    kube_pod("unready", "10.0.0.2", ready=False),
                    kube_pod("deleting", "10.0.0.3", deleting=True),
                    kube_pod("no-container", "10.0.0.4", container=False),
                    kube_pod("pending", "10.0.0.5", phase="Pending"),
                ]
            },
        )

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            target = RunPlacementSelector(
                engine=None,
                client=client,
                namespace="deer-flow",
                kube_url="https://kubernetes",
            )
            assert await target._ready_pods() == (ReadyPod("good", "10.0.0.1"),)
            await target._ready_pods()
            assert len(requests) == 1
            await target._ready_pods(refresh=True)
            assert len(requests) == 2

    run(check())


@pytest.mark.parametrize(
    "operation,action",
    [
        ("cancel", "interrupt"),
        ("cancel", ""),
        ("stream", "interrupt"),
        ("stream", "rollback"),
    ],
)
def test_cancel_uses_exact_owner_not_least_load(monkeypatch, operation, action):
    target, engine, refreshes = selector(monkeypatch, {"status": "running", "owner_worker_id": "gateway-b:worker-token"})
    target._reservations["gateway-a"] = [999999999.0]
    assert existing(target, operation=operation, action=action).name == "gateway-b"
    assert target._reservations == {"gateway-a": [999999999.0]}
    assert refreshes == [True]
    assert engine.calls == [(str(module._RUN_OWNER_SQL), {"run_id": "run-1", "thread_id": "thread-1"})]
    assert "SELECT" in engine.calls[0][0] and "UPDATE" not in engine.calls[0][0]


def test_get_stream_with_action_matches_existing_gateway_semantics(monkeypatch):
    target, engine, refreshes = selector(monkeypatch, {"status": "running", "owner_worker_id": "gateway-b:worker"})
    assert existing(target, method="GET", operation="stream", action="present").name == "gateway-b"
    assert engine.calls and refreshes == [True]


@pytest.mark.parametrize("status", ["pending", "running"])
@pytest.mark.parametrize("owner", [None, "gateway-gone:worker", "", "gateway-b:old"])
def test_live_owner_unavailable_never_reassigned(monkeypatch, status, owner):
    target, _, _ = selector(
        monkeypatch,
        {"status": status, "owner_worker_id": owner},
        pods=(ReadyPod("gateway-a", "10.0.0.1"),),
    )
    with pytest.raises(RuntimeError, match="owner is unavailable"):
        existing(target)


@pytest.mark.parametrize("status", ["success", "interrupted", "error"])
def test_terminal_missing_owner_delegates_gateway_semantics(monkeypatch, status):
    target, _, _ = selector(monkeypatch, {"status": status, "owner_worker_id": "gateway-gone:worker"})
    assert existing(target).name == "gateway-a"


def test_unknown_or_thread_mismatched_run_delegates_gateway_404(monkeypatch):
    target, engine, _ = selector(monkeypatch)
    assert existing(target, thread_id="different-thread").name == "gateway-a"
    assert engine.calls[0][1]["thread_id"] == "different-thread"


@pytest.mark.parametrize(
    "method,operation,action",
    [("GET", "stream", ""), ("POST", "stream", ""), ("GET", "cancel", "")],
)
def test_non_mutating_join_uses_redis_without_db_owner_lookup(monkeypatch, method, operation, action):
    target, engine, refreshes = selector(monkeypatch, error=RuntimeError("database must not be called"))
    assert existing(target, method=method, operation=operation, action=action).name == "gateway-a"
    assert engine.calls == [] and refreshes == [False]


def test_identifiers_use_bound_parameters(monkeypatch):
    target, engine, _ = selector(monkeypatch)
    identifier = "custom-id-' OR '1'='1"
    existing(target, thread_id=identifier)
    assert identifier not in engine.calls[0][0]
    assert engine.calls[0][1]["thread_id"] == identifier


@pytest.mark.parametrize(
    "field,value",
    [
        ("thread_id", ""),
        ("run_id", "a/b"),
        ("run_id", "a\\b"),
        ("thread_id", "x\nInjected"),
        ("thread_id", "a" * 513),
        ("operation", "delete"),
        ("method", ""),
        ("method", "a" * 17),
        ("action", "a" * 33),
    ],
)
def test_invalid_metadata_rejected_before_db(monkeypatch, field, value):
    target, engine, _ = selector(monkeypatch)
    with pytest.raises(ValueError):
        existing(target, **{field: value})
    assert engine.calls == []


def test_no_ready_pod_fails_closed(monkeypatch):
    target, _, _ = selector(monkeypatch, pods=())
    with pytest.raises(RuntimeError):
        existing(target)
    with pytest.raises(RuntimeError):
        run(target.choose())


def request(monkeypatch, target, path="/resolve", headers=None):
    monkeypatch.setattr(module.app.state, "selector", target, raising=False)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://dispatcher") as client:
            return await client.get(path, headers=headers)

    return run(check())


HEADERS = {
    "X-DeerFlow-Thread-ID": "thread-1",
    "X-DeerFlow-Run-ID": "run-1",
    "X-DeerFlow-Run-Operation": "cancel",
    "X-DeerFlow-Run-Method": "POST",
}


def test_resolve_contract_version_and_private_response(monkeypatch):
    target, _, _ = selector(monkeypatch, {"status": "running", "owner_worker_id": "gateway-b:worker"})
    response = request(monkeypatch, target, headers=HEADERS)
    assert response.status_code == 204 and not response.content
    assert response.headers["x-deerflow-gateway-target"] == "10.0.0.2:8001"
    assert response.headers["x-deerflow-owner-route-version"] == module.OWNER_ROUTE_VERSION
    assert response.headers["cache-control"] == "no-store"


def test_missing_routing_header_validation(monkeypatch):
    target, engine, _ = selector(monkeypatch)
    response = request(monkeypatch, target, headers={})
    assert response.status_code == 422 and not engine.calls
    assert "x-deerflow-gateway-target" not in response.headers


def test_resolve_error_does_not_leak_db_details(monkeypatch, caplog):
    target, _, _ = selector(monkeypatch, error=RuntimeError("password=do-not-expose"))
    response = request(monkeypatch, target, headers=HEADERS)
    assert response.status_code == 503
    assert "x-deerflow-gateway-target" not in response.headers
    assert "do-not-expose" not in response.text + caplog.text


def test_select_and_readiness_contracts_unchanged(monkeypatch):
    target, _, _ = selector(monkeypatch)
    assert request(monkeypatch, target, "/health").json() == {"status": "ok"}
    assert request(monkeypatch, target, "/ready").json() == {"ready_gateway_pods": 2}
    response = request(monkeypatch, target, "/select")
    assert response.status_code == 204
    assert response.headers["x-deerflow-gateway-target"] == "10.0.0.2:8001"
    assert "x-deerflow-owner-route-version" not in response.headers
    empty, _, _ = selector(monkeypatch, pods=())
    assert request(monkeypatch, empty, "/select").status_code == 503
