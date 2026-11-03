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
             '_enrich_knowledge_base_counts', '_int_value', '_is_processing_knowledge', '_unwrap_list_response',
             '_knowledge_count_coverage', '_knowledge_count_summary', 'list_knowledge_bases', '_normalise_tenant_id'}
    constants = {'_COUNT_ENRICH_CONCURRENCY', '_COUNT_PAGE_SIZE', '_COUNT_MAX_DOCUMENTS', '_COUNT_ENRICH_MAX_SECONDS', '_COUNT_FIELDS'}
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in constants for t in node.targets):
            nodes.append(node)
    scope = dict(asyncio=asyncio, Any=Any, HTTPException=HTTPException, logger=logging.getLogger('counts-test'),
                 _KNOWLEDGE_COUNT_LIMITERS=weakref.WeakKeyDictionary(), Depends=lambda value: None, get_current_user_from_request=None)
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

    async def test_coverage_before_after_counts_libraries_once_without_extra_io(self):
        items = [dict(id='complete', knowledge_count=0, chunk_count=0, processing_count=0, is_processing=False),
                 dict(id='missing'), dict(id='partial', knowledge_count=201)]
        before = self.scope['_knowledge_count_coverage'](items)
        self.assertEqual(self.calls, 0)
        self.assertEqual(before['missing_libraries'], 2)
        self.assertEqual(before['missing_fields']['knowledge_count'], 1)
        self.assertEqual(before['missing_fields']['chunk_count'], 2)
        await self.enrich(items)
        calls = self.calls
        with self.assertLogs('counts-test', level='INFO') as logs:
            result = self.scope['_knowledge_count_summary'](before, items, 1)
        self.assertEqual(self.calls, calls)
        self.assertEqual((result['returned_libraries'], result['missing_before'], result['enriched'], result['missing_after']), (3, 2, 2, 0))
        self.assertEqual(result['listing_failures'], 1)
        self.assertEqual(before['missing_libraries'], 2)
        self.assertNotIn('partial', '\n'.join(logs.output))

    async def test_coverage_timeout_and_oversize_remain_missing(self):
        for blocking in (False, True):
            self.documents = 501
            self.block = blocking
            self.scope['_COUNT_ENRICH_MAX_SECONDS'] = 0.01
            items = [dict(id='large')]
            before = self.scope['_knowledge_count_coverage'](items)
            await self.enrich(items)
            summary = self.scope['_knowledge_count_summary'](before, items, 0)
            self.assertEqual((summary['missing_before'], summary['enriched'], summary['missing_after']), (1, 0, 1))
        self.assertEqual(self.active, 0)

    async def test_coverage_empty_and_present_null_match_existing_eligibility(self):
        self.assertEqual(self.scope['_knowledge_count_summary'](self.scope['_knowledge_count_coverage']([]), [], 2)['returned_libraries'], 0)
        # Presence-only is the existing gate; null/stale values are not certified valid.
        item = {key: None for key in self.scope['_COUNT_FIELDS']}
        self.assertEqual(self.scope['_knowledge_count_coverage']([item])['missing_libraries'], 0)
        self.assertEqual(self.calls, 0)

    async def test_list_response_reports_deduplicated_scope_and_partial_listing(self):
        from types import SimpleNamespace
        async def session(user):
            return SimpleNamespace(token='private-token', user={'id': 'user'})
        async def identity(session):
            return {'id': 'user'}, [{'tenant_id': 'one'}, {'tenant_id': 'two'}]
        async def request(method, path, token, **kwargs):
            self.calls += 1
            if path == '/knowledge-bases':
                return {'data': [{'id': 'same-kb'}]}
            if kwargs['tenant_id'] == 'two':
                raise HTTPException(503, 'synthetic')
            return {'data': []}
        async def fail_enrichment(items, token):
            self.fail('Knowledge lists must not call count enrichment')
        self.scope.update(ensure_weknora_session_for_deerflow_user=session, _live_weknora_identity=identity,
                          _request=request, _classify_tenant_knowledge_base=lambda item, **kwargs: dict(item),
                          _float_env=lambda name, default: default, _enrich_knowledge_base_counts=fail_enrichment)
        result = await self.scope['list_knowledge_bases'](object())
        self.assertEqual(self.calls, 4)  # Two existing list reads per membership, no diagnostic I/O.
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'], [{'id': 'same-kb'}])
        summary = result['count_summary']
        self.assertEqual((summary['returned_libraries'], summary['missing_before'], summary['enriched'], summary['missing_after'], summary['listing_failures']), (1, 1, 0, 1, 1))
        self.assertEqual(summary['scope'], 'current_user_returned_libraries')
        self.assertFalse(summary['auto_enrichment_enabled'])

    async def test_exact_paged_counts(self):
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items[0]['knowledge_count'], 201)
        self.assertEqual(items[0]['chunk_count'], 402)
        self.assertEqual(items[0]['processing_count'], 100)
        self.assertEqual(self.pages, 3)
        self.assertEqual(self.peak, 1)

    async def test_exact_maximum_500_and_preserve_known_counts_above_limit(self):
        self.documents = 500
        items = [{'id': 'kb'}]
        await self.enrich(items)
        self.assertEqual(items[0]['knowledge_count'], 500)
        self.assertEqual(items[0]['chunk_count'], 1000)
        self.documents = 501
        items = [{'id': 'kb', 'knowledge_count': 501}]
        before = self.calls
        await self.enrich(items)
        self.assertEqual(items, [{'id': 'kb', 'knowledge_count': 501}])
        self.assertEqual(self.calls - before, 1)

    async def test_limit_shared_between_requests(self):
        self.documents = 500
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
        self.documents = 501
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
