"""Dependency-free tests of the actual count function bodies, with fake I/O.

These do not replace the installed-image router/integration suite.
"""
import ast
import asyncio
import logging
from pathlib import Path
from typing import Any
import unittest
import weakref


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)


def load_counts():
    root = Path(__file__).resolve().parents[1]
    path = root / 'app/gateway/routers/weknora.py'
    if not path.exists():
        path = Path(__file__).parent / 'candidate/backend/app/gateway/routers/weknora.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'_knowledge_count_limiter', '_knowledge_base_counts', '_knowledge_base_knowledge_page',
             '_enrich_knowledge_base_counts', '_int_value', '_is_processing_knowledge', '_unwrap_list_response'}
    constants = {'_COUNT_ENRICH_CONCURRENCY', '_COUNT_PAGE_SIZE', '_COUNT_MAX_DOCUMENTS', '_COUNT_ENRICH_MAX_SECONDS'}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            nodes.append(node)
    scope = dict(asyncio=asyncio, Any=Any, HTTPException=HTTPException, logger=logging.getLogger('counts-test'),
                 _KNOWLEDGE_COUNT_LIMITERS=weakref.WeakKeyDictionary())
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class CountBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scope = load_counts()
        self.active = self.peak = self.calls = self.pages = 0
        self.documents = 201
        self.fail_chunk = False
        self.block = False
        self.scope['_request'] = self.request

    async def request(self, method, path, token, **kwargs):
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0)
            if self.block:
                await asyncio.Event().wait()
            if path.startswith('/chunks/'):
                if self.fail_chunk:
                    raise HTTPException(502, 'synthetic')
                return {'total': 2}
            self.pages += 1
            start = (kwargs['params']['page'] - 1) * 100
            items = [{'id': str(i), 'parse_status': 'processing' if i % 2 else 'completed'}
                     for i in range(start, min(start + 100, self.documents))]
            return {'data': items, 'total': self.documents}
        finally:
            self.active -= 1

    async def enrich(self, items):
        await self.scope['_enrich_knowledge_base_counts'](items, 'synthetic')

    async def test_exact_paged_counts(self):
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items[0]['knowledge_count'], 201)
        self.assertEqual(items[0]['chunk_count'], 402)
        self.assertEqual(items[0]['processing_count'], 100)
        self.assertEqual(self.pages, 3)
        self.assertEqual(self.peak, 1)

    async def test_limit_shared_between_requests(self):
        self.documents = 1000
        await asyncio.gather(*(self.enrich([{'id': str(i)} for i in range(8)]) for _ in range(4)))
        self.assertLessEqual(self.peak, 8)
        self.assertEqual(self.active, 0)

    async def test_complete_upstream_counts_skip_io(self):
        await self.enrich([dict(id='kb', knowledge_count=1, chunk_count=2, processing_count=0, is_processing=False)])
        self.assertEqual(self.calls, 0)

    async def test_failure_does_not_commit_partial_result(self):
        self.fail_chunk = True
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items, [{'id': 'kb'}])

    async def test_large_total_skips_before_chunk_reads(self):
        self.documents = 5001
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(self.calls, 1)
        self.assertEqual(items, [{'id': 'kb'}])

    async def test_timeout_drains_tasks_and_releases_permits(self):
        self.block = True
        self.scope['_COUNT_ENRICH_MAX_SECONDS'] = 0.02
        await self.enrich([{'id': str(i)} for i in range(1000)])
        self.assertEqual(self.active, 0)
        self.assertLessEqual(self.calls, 8)
        self.assertFalse(self.scope['_knowledge_count_limiter']().locked())

    async def test_parent_cancellation_drains_tasks(self):
        self.block = True
        task = asyncio.create_task(self.enrich([{'id': str(i)} for i in range(20)]))
        while not self.active:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.active, 0)
        self.assertFalse(self.scope['_knowledge_count_limiter']().locked())

    async def test_empty_library_is_exact_zero(self):
        self.documents = 0
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items[0]['chunk_count'], 0)
        self.assertEqual(items[0]['knowledge_count'], 0)

    async def test_missing_total_is_not_treated_as_complete_page(self):
        async def missing(*args, **kwargs):
            return {'data': [{'id': 'doc'}]}
        self.scope['_request'] = missing
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items, [{'id': 'kb'}])

    async def test_duplicate_page_does_not_commit_counts(self):
        original = self.request
        async def duplicate(method, path, token, **kwargs):
            if not path.startswith('/chunks/'):
                kwargs['params']['page'] = 1
            return await original(method, path, token, **kwargs)
        self.scope['_request'] = duplicate
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items, [{'id': 'kb'}])

    async def test_fixed_worker_count_for_thousand_libraries(self):
        self.block = True
        before = set(asyncio.all_tasks())
        task = asyncio.create_task(self.enrich([{'id': str(i)} for i in range(1000)]))
        while self.active < 8:
            await asyncio.sleep(0)
        self.assertLessEqual(len(set(asyncio.all_tasks()) - before), 9)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.active, 0)


if __name__ == '__main__':
    unittest.main()
