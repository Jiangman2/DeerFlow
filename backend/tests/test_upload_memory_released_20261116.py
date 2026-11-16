"""Regression boundaries for the retained, already deployed file parser."""

import asyncio
import os

import httpx
import pytest

from app.gateway import upload_document_memory_guard as guard


def test_capacity_refuses_before_starting_a_parser(monkeypatch):
    monkeypatch.setattr(guard, "_active", guard.ACTIVE_LIMIT)
    monkeypatch.setattr(guard, "_waiting", guard.WAITER_LIMIT)
    with pytest.raises(guard.parser.UploadDocumentParseError, match="busy"):
        asyncio.run(guard._acquire())
    assert guard._active == guard.ACTIVE_LIMIT
    assert guard._waiting == guard.WAITER_LIMIT


def test_cancellation_waits_for_parser_ownership_to_finish():
    async def scenario():
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def parse():
            started.set()
            try:
                await release.wait()
            finally:
                finished.set()

        owner = asyncio.create_task(guard._owned_parse(parse(), 10))
        await started.wait()
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert finished.is_set()

    asyncio.run(scenario())


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Linux file ownership primitive")
def test_real_file_is_streamed_without_buffering_and_identity_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    content = b"%PDF-" + b"x" * (guard.CHUNK_BYTES * 3)
    source.write_bytes(content)
    observed = []

    async def handler(request):
        assert request.headers["X-DeerFlow-Parse-Priority"] == "interactive"
        chunks = [chunk async for chunk in request.stream]
        assert max(len(c) for c in chunks[1:-1]) <= guard.CHUNK_BYTES
        body = b"".join(chunks)
        assert content in body and len(body) == int(request.headers["Content-Length"])
        observed.append(True)

        class ResponseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"results":{"document":{"md_content":"# Parsed"}}}'

        return httpx.Response(200, stream=ResponseStream())

    class StreamingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return await handler(request)

    async def scenario():
        async with httpx.AsyncClient(transport=StreamingTransport(), trust_env=False) as client:
            monkeypatch.setattr(guard.parser, "_get_parser_client", lambda: client)
            result = await guard.parse_conversation_document_file(endpoint="https://parser.invalid/parse", filename="source.pdf", file_path=source)
            assert result == "# Parsed"

    asyncio.run(scenario())
    assert observed == [True] and source.read_bytes() == content


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="Linux file ownership primitive")
def test_symlink_input_is_rejected_before_network_submission(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    link = tmp_path / "link.pdf"
    link.symlink_to(source)
    calls = []
    monkeypatch.setattr(guard.parser, "_get_parser_client", lambda: calls.append(True))
    with pytest.raises(guard.parser.UploadDocumentParseError):
        asyncio.run(guard.parse_conversation_document_file(endpoint="https://parser.invalid/parse", filename="source.pdf", file_path=link))
    assert calls == []
