"""Exercise real ASGI admission, cancellation and streaming size bounds."""

import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.routing import APIRouter
from starlette.requests import Request as RawRequest

from app.gateway import weknora_relay_budget as budget


class RelayBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        budget.LIMITERS.clear()
        self.env = patch.dict(os.environ, {"WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def application(self, endpoint):
        router = APIRouter()
        for path in ("/first", "/second"):
            router.add_api_route(path, endpoint, methods=["POST"], route_class_override=budget.EmbeddingBudgetRoute)
        app = FastAPI()
        app.include_router(router)
        return app

    async def invoke(self, app, path="/first", receive=None, send=None):
        events = []

        async def default_receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def default_send(event):
            events.append(event)

        await app(
            {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [], "server": ("test", 80), "client": ("test", 1)},
            receive or default_receive,
            send or default_send,
        )
        return events

    async def test_shared_admission_rejects_without_reading_body_and_releases_cancel(self):
        started = asyncio.Event()

        async def endpoint(request: Request):
            await request.body()
            started.set()
            await asyncio.Event().wait()

        app = self.application(endpoint)
        task = asyncio.create_task(self.invoke(app))
        await asyncio.wait_for(started.wait(), 2)

        async def forbidden_read():
            self.fail("Rejected request body was read")

        try:
            events = await self.invoke(app, "/second", receive=forbidden_read)
            self.assertEqual(events[0]["status"], 429)
            self.assertIn((b"retry-after", b"2"), events[0]["headers"])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(budget.embedding_limiter()._value, 1)

    async def test_slow_send_retains_admission(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def endpoint():
            return {"data": [1, 2, 3]}

        async def slow_send(event):
            if event["type"] == "http.response.body":
                started.set()
                await release.wait()

        app = self.application(endpoint)
        task = asyncio.create_task(self.invoke(app, send=slow_send))
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.assertEqual((await self.invoke(app, "/second"))[0]["status"], 429)
        finally:
            release.set()
            await task
        self.assertEqual((await self.invoke(app))[0]["status"], 200)

    async def test_auth_failure_returns_original_status_and_releases(self):
        async def endpoint():
            raise HTTPException(401, "required")

        app = self.application(endpoint)
        for _ in range(2):
            self.assertEqual((await self.invoke(app))[0]["status"], 401)
        self.assertEqual(budget.embedding_limiter()._value, 1)

    async def test_limit_clamped_and_stable_while_in_use(self):
        with patch.dict(os.environ, {"WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY": "200"}):
            limiter = budget.embedding_limiter()
            self.assertEqual(limiter._value, 8)
            await limiter.acquire()
        self.assertIs(budget.embedding_limiter(), limiter)
        self.assertEqual(limiter._value, 7)
        limiter.release()

    async def read(self, chunks, headers=(), max_bytes=8 * 1024 * 1024):
        iterator = iter(chunks)

        async def receive():
            body = next(iterator)
            return {"type": "http.request", "body": body, "more_body": bool(body)}

        request = RawRequest({"type": "http", "headers": list(headers)}, receive)
        return await budget.bounded_request_json(request, label="test", max_bytes=max_bytes)

    async def test_chunked_json_and_non_object(self):
        self.assertEqual(await self.read([b'{"a":', b"1}", b""]), {"a": 1})
        for body in (b"[]", b"", b"invalid"):
            with self.assertRaises(HTTPException) as ctx:
                await self.read([body, b""])
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_body_bounds_do_not_trust_declared_length(self):
        for headers in ((), ((b"content-length", b"1"),)):
            with self.assertRaises(HTTPException) as ctx:
                await self.read([b"1234", b"5678", b""], headers, max_bytes=7)
            self.assertEqual(ctx.exception.status_code, 413)
        # Environment callers cannot raise the hard maximum.
        with self.assertRaises(HTTPException) as ctx:
            await self.read([], ((b"content-length", str(budget.MAX_REQUEST_BYTES + 1).encode()),), max_bytes=100 * 1024 * 1024)
        self.assertEqual(ctx.exception.status_code, 413)

    async def test_body_read_deadline(self):
        async def stalled():
            await asyncio.Event().wait()

        request = RawRequest({"type": "http", "headers": []}, stalled)
        with patch.object(budget, "BODY_READ_SECONDS", 0.01):
            with self.assertRaises(HTTPException) as ctx:
                await budget.bounded_request_json(request, label="test", max_bytes=100)
        self.assertEqual(ctx.exception.status_code, 408)

    async def test_upstream_bound_closes_before_unbounded_read(self):
        class Client:
            closed = False
            read = 0
            status_code = 200
            headers = {}

            @asynccontextmanager
            async def stream(self, method, url, **kwargs):
                self.sent_headers = kwargs["headers"]
                try:
                    yield self
                finally:
                    self.closed = True

            async def aiter_raw(self, chunk_size):
                for chunk in (b"1234", b"5678", b"never"):
                    self.read += 1
                    yield chunk

        client = Client()
        with patch.object(budget, "MAX_RESPONSE_BYTES", 7):
            with self.assertRaises(HTTPException) as ctx:
                await budget.bounded_post(client, "http://synthetic", headers={"Authorization": "Bearer fake"})
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(client.read, 2)
        self.assertTrue(client.closed)
        self.assertEqual(client.sent_headers["Accept-Encoding"], "identity")
        client.read, client.closed = 0, False
        client.headers = {"content-encoding": "gzip"}
        with self.assertRaises(HTTPException):
            await budget.bounded_post(client, "http://synthetic", headers={})
        self.assertEqual(client.read, 0)
        self.assertTrue(client.closed)

    async def test_successful_raw_stream_json_and_buffer_release(self):
        import httpx

        class Stream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b'{"data":'
                yield b"[]}"

            async def aclose(self):
                self.closed = True

        stream = Stream()

        async def handle(request):
            self.assertEqual(request.headers["accept-encoding"], "identity")
            return httpx.Response(200, stream=stream)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            response = await budget.bounded_post(client, "http://synthetic", headers={})
            self.assertTrue(stream.closed)
            self.assertEqual(response.json(), {"data": []})
            self.assertEqual(len(response.raw), 0)


if __name__ == "__main__":
    unittest.main()
