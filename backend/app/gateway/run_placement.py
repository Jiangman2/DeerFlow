"""Internal, read-only selector for placing new streamed Runs on Gateway Pods.

Nginx asks this service for a target before proxying the original request. The
request body, cookies, and SSE response go directly between Nginx and the
selected Gateway; this process never authenticates a user or creates a Run.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

_KUBE_TOKEN = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_KUBE_CA = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
_KUBE_NAMESPACE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
_RUN_COUNTS_SQL = text(
    "SELECT split_part(owner_worker_id, ':', 1) AS pod_name, count(*) AS active_runs "
    "FROM runs WHERE lease_expires_at >= CURRENT_TIMESTAMP - INTERVAL '60 seconds' "
    "AND status IN ('pending', 'running') AND owner_worker_id IS NOT NULL "
    "GROUP BY 1"
)


@dataclass(frozen=True)
class ReadyPod:
    name: str
    ip: str

    @property
    def target(self) -> str:
        address = ipaddress.ip_address(self.ip)
        return f"[{address}]:8001" if address.version == 6 else f"{address}:8001"


class RunPlacementSelector:
    """Choose the least-active ready Pod, with short local reservations for ties.

    Reservations bridge the time between selection and the target's committed
    Run insert. They expire automatically, including when a client disconnects
    or the target rejects the request. PostgreSQL remains the source of truth.
    """

    def __init__(self, *, engine: AsyncEngine, client: httpx.AsyncClient, namespace: str, kube_url: str):
        self.engine = engine
        self.client = client
        self.namespace = namespace
        self.kube_url = kube_url
        self._lock = asyncio.Lock()
        self._ready_cache: tuple[ReadyPod, ...] = ()
        self._ready_until = 0.0
        self._reservations: dict[str, list[float]] = defaultdict(list)
        self._tie_cursor = 0

    async def _ready_pods(self) -> tuple[ReadyPod, ...]:
        now = time.monotonic()
        if now < self._ready_until:
            return self._ready_cache
        token = (await asyncio.to_thread(_KUBE_TOKEN.read_text, encoding="utf-8")).strip()
        if not token:
            raise RuntimeError("Kubernetes service-account token is empty")
        url = f"{self.kube_url}/api/v1/namespaces/{quote(self.namespace, safe='')}/pods"
        response = await self.client.get(url, params={"labelSelector": "app=gateway"}, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        pods: list[ReadyPod] = []
        for item in response.json().get("items", []):
            metadata = item.get("metadata") or {}
            status = item.get("status") or {}
            if metadata.get("deletionTimestamp") or status.get("phase") != "Running":
                continue
            if not any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in status.get("conditions") or []):
                continue
            if not any(entry.get("name") == "gateway" and entry.get("ready") is True for entry in status.get("containerStatuses") or []):
                continue
            name, ip = metadata.get("name"), status.get("podIP")
            if not isinstance(name, str) or not isinstance(ip, str):
                continue
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue
            pods.append(ReadyPod(name, ip))
        self._ready_cache = tuple(sorted(pods, key=lambda pod: pod.name))
        self._ready_until = now + 5.0
        return self._ready_cache

    async def _active_counts(self) -> dict[str, int]:
        async with self.engine.connect() as conn:
            result = await conn.execute(_RUN_COUNTS_SQL)
            return {str(row.pod_name): int(row.active_runs) for row in result}

    async def choose(self) -> tuple[ReadyPod, int, int]:
        async with self._lock:
            pods = await self._ready_pods()
            if not pods:
                raise RuntimeError("No ready Gateway Pod is available")
            counts = await self._active_counts()
            now = time.monotonic()
            scores: dict[str, int] = {}
            active_names = {pod.name for pod in pods}
            for old_name in self._reservations.keys() - active_names:
                del self._reservations[old_name]
            for pod in pods:
                self._reservations[pod.name] = [deadline for deadline in self._reservations[pod.name] if deadline > now]
                scores[pod.name] = counts.get(pod.name, 0) + len(self._reservations[pod.name])
            minimum = min(scores.values())
            tied = [pod for pod in pods if scores[pod.name] == minimum]
            chosen = tied[self._tie_cursor % len(tied)]
            self._tie_cursor += 1
            self._reservations[chosen.name].append(now + 5.0)
            return chosen, counts.get(chosen.name, 0), scores[chosen.name]


def _selector_from_environment() -> RunPlacementSelector:
    raw_url = os.environ["DATABASE_URL"]
    database_url = make_url(raw_url).set(drivername="postgresql+asyncpg")
    namespace = os.environ.get("POD_NAMESPACE") or _KUBE_NAMESPACE.read_text(encoding="utf-8").strip()
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0, pool_pre_ping=True)
    client = httpx.AsyncClient(verify=str(_KUBE_CA), trust_env=False, timeout=2.0)
    return RunPlacementSelector(engine=engine, client=client, namespace=namespace, kube_url=f"https://{host}:{port}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    selector = _selector_from_environment()
    app.state.selector = selector
    try:
        yield
    finally:
        await selector.client.aclose()
        await selector.engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, int]:
    try:
        selector: RunPlacementSelector = app.state.selector
        pods = await selector._ready_pods()
        await selector._active_counts()
        if not pods:
            raise RuntimeError("No ready Gateway Pod is available")
    except Exception as exc:
        logger.error("RUN_PLACEMENT event=readiness_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Run placement unavailable") from None
    return {"ready_gateway_pods": len(pods)}


@app.get("/select")
async def select() -> Response:
    try:
        pod, active, score = await app.state.selector.choose()
    except Exception as exc:
        logger.error("RUN_PLACEMENT event=select_failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Run placement unavailable") from None
    logger.info("RUN_PLACEMENT event=selected pod=%s active_runs=%d score=%d", pod.name, active, score)
    return Response(status_code=204, headers={"X-DeerFlow-Gateway-Target": pod.target, "Cache-Control": "no-store"})
