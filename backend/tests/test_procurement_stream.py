import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_procurement_markdown import report

from app.gateway.procurement_review.config import ENDPOINT, ReviewConfig
from app.gateway.procurement_review.router import router
from app.gateway.procurement_review.rules import RULE_VERSION
from app.gateway.procurement_review.service import LEGACY_RULE_VERSION, ReviewService
from app.gateway.procurement_review.stream import stream_result
from deerflow.config.paths import Paths
from deerflow.persistence.procurement_review.model import ProcurementReviewRow
from deerflow.persistence.procurement_review.sql import ReviewRepository


def job(state="running"):
    return {
        "id": "job-1",
        "request_id": "request-1",
        "state": state,
        "payload": {
            "owner_user_id": "owner",
            "assistant_id": "agent",
            "delivery_mode": "stream",
            "rule_version": RULE_VERSION,
            "thread_id": "thread",
            "run_id": "run-1",
            "deadline": time.time() + 3600,
        },
    }


def decode(frame):
    lines = frame.decode().splitlines()
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = ReviewConfig("owner", "agent", Path("/refs"), "test-key", job_timeout=3600)

    def service(self, item):
        repo = SimpleNamespace(get=AsyncMock(side_effect=lambda _: copy.deepcopy(item)))
        return ReviewService(SimpleNamespace(state=SimpleNamespace()), repo, self.config)

    async def test_disconnect_reconnect_heartbeat_and_success_bytes(self):
        item = job()
        service = self.service(item)
        frames = stream_result(service, item, poll_seconds=0, heartbeat_seconds=0)
        self.assertEqual(decode(await anext(frames))[0], "accepted")
        self.assertEqual(decode(await anext(frames))[0], "heartbeat")
        await frames.aclose()
        self.assertEqual(item["state"], "running")
        with tempfile.TemporaryDirectory() as temp:
            paths = Paths(Path(temp))
            dest = paths.thread_dir("thread", user_id="owner") / "procurement-delivery/report.md"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(report())
            item["state"] = "result_ready"
            item["payload"].update(result_status="succeeded", report_sha256=hashlib.sha256(report()).hexdigest())
            with patch("app.gateway.procurement_review.stream.get_paths", return_value=paths):
                replay = [decode(f) async for f in stream_result(service, item)]
                self.assertEqual([n for n, _ in replay], ["accepted", "result"])
                self.assertEqual(replay[-1][1]["report_markdown"].encode(), report())
                self.assertEqual(replay[-1][1]["report_bytes"], len(report()))
                self.assertEqual(replay[-1][1]["report_sha256"], hashlib.sha256(report()).hexdigest())
                # Serving does not claim receipt by the other party or modify
                # a terminal job, and therefore remains replayable.
                self.assertEqual(item["state"], "result_ready")
                dest.write_bytes(b"tampered")
                rejected = [decode(f) async for f in stream_result(service, item)]
                self.assertEqual(rejected[-1][0], "error")
                self.assertNotIn("report_markdown", rejected[-1][1])

    async def test_worker_success_and_failure_never_callback(self):
        for success in (True, False):
            item = job()
            service = self.service(item)
            run = {"thread_id": "thread", "metadata": {"procurement_review_job_id": "job-1"}, "status": "success" if success else "error"}
            service.app.state.run_store = SimpleNamespace(get=AsyncMock(return_value=run))
            service.accept_run = AsyncMock()
            service.save = AsyncMock()
            service.deliver = AsyncMock(side_effect=AssertionError("must not callback"))
            await service.step(item)
            self.assertEqual(item["state"], "result_ready")
            self.assertEqual(item["payload"]["result_status"], "succeeded" if success else "failed")
            service.deliver.assert_not_awaited()
            service.accept_run.assert_awaited_once() if success else service.accept_run.assert_not_awaited()

    async def test_failure_deadline_and_callback_guard(self):
        item = job()
        item["payload"]["deadline"] = time.time() - 1
        service = self.service(item)
        service.app.state.run_manager = SimpleNamespace(cancel=AsyncMock(return_value="lease_valid_elsewhere"))
        service.save = AsyncMock()
        await service.step(item)
        self.assertEqual(item["state"], "result_ready")
        self.assertEqual(item["payload"]["result_status"], "failed")
        frames = [decode(f) async for f in stream_result(service, item)]
        self.assertEqual(frames[-1][0], "result")
        self.assertNotIn("report_markdown", frames[-1][1])
        with self.assertRaises(ValueError):
            await service.deliver(item)

    async def test_binding_and_legacy_callback_compatibility(self):
        item = job("callback")
        service = self.service(item)
        service.save = AsyncMock()
        service.deliver = AsyncMock()
        await service.step(item)
        service.deliver.assert_not_awaited()
        self.assertEqual(item["state"], "result_ready")
        item["payload"]["owner_user_id"] = "other"
        frames = [decode(f) async for f in stream_result(service, item)]
        self.assertEqual(frames[-1][0], "error")
        legacy = job("callback")
        legacy["payload"].pop("delivery_mode")
        legacy["payload"].update(rule_version=LEGACY_RULE_VERSION, callback_url="https://partner.example/callback")
        service.config = ReviewConfig("owner", "agent", Path("/refs"), "key", "https://partner.example/callback", "legacy-key")
        await service.step(legacy)
        service.deliver.assert_awaited_once()

    async def test_read_error_and_bounded_wait(self):
        item = job()
        service = self.service(item)
        service.repo.get.side_effect = RuntimeError("private database error")
        frames = [decode(f) async for f in stream_result(service, item)]
        self.assertEqual(frames[-1][1]["code"], "RESULT_UNAVAILABLE")
        self.assertNotIn("private database", str(frames))
        service = self.service(item)
        with patch("app.gateway.procurement_review.stream.monotonic", side_effect=[0, 0, 99999]):
            frames = [decode(f) async for f in stream_result(service, item)]
        self.assertEqual(frames[-1][1]["code"], "WAIT_TIMEOUT")
        self.assertTrue(frames[-1][1]["retryable"])
        self.assertEqual(item["state"], "running")


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_multipart_sql_idempotency_auth_and_stream(self):
        import app.gateway.procurement_review.router as route_module
        import app.gateway.routers.uploads as uploads_module

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = create_async_engine("sqlite+aiosqlite:///" + str(root / "queue.db"))
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(lambda conn: ProcurementReviewRow.__table__.create(conn))
                repo = ReviewRepository(async_sessionmaker(engine, expire_on_commit=False))
                config = ReviewConfig("owner", "agent", root, "test-key", job_timeout=3600)
                service = SimpleNamespace(config=config, repo=repo, references={"manifest_sha256": "a" * 64, "version": "refs-v1"}, is_running=True)
                app = FastAPI()
                app.include_router(router)
                app.state.procurement_review_service = service
                created_ids = []
                real_create = repo.create

                async def create(**kwargs):
                    item, created = await real_create(**kwargs)
                    if created:
                        created_ids.append(item["id"])
                        claimed = await repo.claim("worker", 90)
                        claimed["state"] = "result_ready"
                        claimed["payload"].update(result_status="failed", message="synthetic failure")
                        self.assertTrue(await repo.save(claimed, "worker"))
                    return item, created

                def ensure(thread_id, user_id):
                    path = root / thread_id
                    path.mkdir()
                    return path

                async def writer(part, **kw):
                    raw = await part.read()
                    path = kw["uploads_dir"] / kw["display_filename"]
                    path.write_bytes(raw)
                    return path, len(raw), kw["total_size"] + len(raw), None, None

                with patch.object(repo, "create", side_effect=create), patch.object(route_module, "ensure_uploads_dir", side_effect=ensure), patch.object(uploads_module, "_write_upload_file_with_limits", side_effect=writer):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                        self.assertEqual((await client.post(ENDPOINT, content=b"bad")).status_code, 401)
                        self.assertEqual(created_ids, [])

                        async def send(content=b"synthetic material", extra=None, filename="material.png", request_id="business-1"):
                            return await client.post(ENDPOINT, headers={"Authorization": "Bearer test-key"}, data={"request_id": request_id, **(extra or {})}, files=[("files", (filename, content, "application/octet-stream"))])

                        first, replay = await send(), await send()
                        self.assertEqual(first.status_code, 200, first.text)
                        self.assertTrue(first.headers["content-type"].startswith("text/event-stream"))
                        self.assertEqual(first.headers["x-accel-buffering"], "no")
                        self.assertIn("event: result", first.text)
                        self.assertIn('"status": "failed"', first.text)
                        self.assertEqual(replay.status_code, 200)
                        self.assertEqual(len(created_ids), 1)
                        self.assertEqual((await send(b"changed")).status_code, 409)
                        self.assertIn((await send(extra={"callback_url": "https://bad.example"})).status_code, (400, 422))
                        self.assertEqual(len(list(root.glob("procurement-*"))), 1)
                        saved = await repo.get(created_ids[0])
                        self.assertNotIn("callback_url", saved["payload"])
                        self.assertEqual(saved["payload"]["delivery_mode"], "stream")
                        self.assertIsNone(await repo.claim("other", 90))
                        for extension in ("json", "txt", "xlsx", "xls", "csv"):
                            response = await send(filename="material." + extension, request_id="format-" + extension)
                            self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(len(created_ids), 6)
                        service.config = ReviewConfig("other", "agent", root, "test-key")
                        self.assertEqual((await send()).status_code, 409)
            finally:
                await engine.dispose()


class ConfigTests(unittest.TestCase):
    def test_no_callback_required_and_timeout_retained(self):
        env = {
            "PROCUREMENT_REVIEW_" + k: v
            for k, v in {
                "ENABLED": "true",
                "OWNER_USER_ID": "owner",
                "ASSISTANT_ID": "技术委托要求审查助手",
                "REFERENCE_ROOT": str(Path.cwd()),
                "SUBMIT_KEY": "test-key",
                "JOB_TIMEOUT_SECONDS": "3600",
            }.items()
        }
        with patch.dict(os.environ, env, clear=True):
            config = ReviewConfig.from_env()
            self.assertEqual(config.callback_url, "")
            self.assertEqual(config.job_timeout, 3600)


if __name__ == "__main__":
    unittest.main()
