"""Qualify installed company-space code with synthetic HTTP and denied networking."""
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

CHECKS = [
    'installed_sources_and_real_import',
    'disabled_no_requests',
    'self_token_consumer_workspace_viewer',
    'existing_viewer_no_duplicate_join',
    'selected_spaces_only',
    'private_approval_capped_rejected',
    'elevated_permissions_never_demoted',
    'nonviewer_share_rejected',
    'lost_join_reply_readback_no_repost',
    'admin_bypass_retained',
    'bounded_redacted_transport',
    'sso_hook_failure_not_cached',
    'sso_success_cached',
]
ORG = '36d52e3c-7ab1-4e28-996e-7981f0d12463'
SECOND = 'ca8d0f80-1ef9-41f5-9b07-8a99ec4c689b'
TOKEN = 'synthetic-consumer-token'
ORG_DATA = {'id': ORG, 'my_role': '', 'searchable': True, 'require_approval': False, 'member_limit': 0}
SHARED = [{'organization_id': ORG, 'permission': 'viewer', 'source_tenant_id': 999}]


class CheckFailed(Exception):
    pass


def require(value):
    if not value:
        raise CheckFailed()


def deny_network(*args, **kwargs):
    raise CheckFailed()


async def qualify(root, pins):
    outcome = {'status': 'INCOMPLETE', 'checks': [], 'production_database_access': False,
               'provider_requests': 0, 'membership_grants': 0, 'production_configuration_used': False,
               'real_http_requests': 0, 'simulated_http_requests': 0, 'candidate_only_in_job': True,
               'ready_for_rollout': False, 'business_acceptance_pending': True}
    stage = CHECKS[0]
    try:
        # The event loop is already running before network guards are installed.
        with ExitStack() as stack:
            stack.enter_context(patch.object(socket.socket, 'connect', deny_network))
            stack.enter_context(patch.object(socket.socket, 'connect_ex', deny_network))
            stack.enter_context(patch.object(socket, 'getaddrinfo', deny_network))
            stack.enter_context(patch.dict(os.environ, {'WEKNORA_COMPANY_KB_ENABLED': 'false'}, clear=True))
            paths = {'app/gateway/weknora_identity.py', 'app/gateway/weknora_company_knowledge.py'}
            require(set(pins) == paths)
            for relative in sorted(paths):
                file = root / relative
                require(file.is_file() and not file.is_symlink())
                require(hashlib.sha256(file.read_bytes()).hexdigest() == pins[relative])
            sys.path.insert(0, str(root))
            from app.gateway import weknora_company_knowledge as company
            from app.gateway import weknora_identity as identity
            import httpx
            from fastapi import HTTPException
            require(Path(company.__file__).resolve() == (root / 'app/gateway/weknora_company_knowledge.py').resolve())
            require(Path(identity.__file__).resolve() == (root / 'app/gateway/weknora_identity.py').resolve())
            require(identity.ensure_company_knowledge_access is company.ensure_company_knowledge_access)
            outcome['checks'].append(stage)
            real_client = httpx.AsyncClient
            session = SimpleNamespace(token=TOKEN, tenant={'id': 77})
            def endpoint(path):
                return 'http://synthetic.invalid/api/v1' + path

            async def policy(responses, error=None, admin=False):
                queue, calls = list(responses), []

                def handle(request):
                    require(request.url.host == 'synthetic.invalid')
                    require(request.headers.get('authorization') == 'Bearer ' + TOKEN)
                    require(request.headers.get('x-tenant-id') == '77')
                    require('x-api-key' not in request.headers)
                    calls.append(request)
                    outcome['simulated_http_requests'] += 1
                    require(bool(queue))
                    item = queue.pop(0)
                    if isinstance(item, Exception):
                        raise item
                    status, data = item
                    return httpx.Response(status, json={'success': True, 'data': data})

                def client(**kwargs):
                    require(kwargs['trust_env'] is False and kwargs['follow_redirects'] is False)
                    require(kwargs['timeout'] == 5)
                    return real_client(**kwargs, transport=httpx.MockTransport(handle))

                caught = None
                with patch.object(httpx, 'AsyncClient', client):
                    try:
                        await company.ensure_company_knowledge_access(session, endpoint, administrator=admin)
                    except HTTPException as exc:
                        caught = exc.detail.get('code')
                        require('synthetic-secret-error' not in str(exc.detail))
                require(caught == error and not queue)
                return calls

            stage = CHECKS[1]
            os.environ.pop('WEKNORA_COMPANY_KB_ENABLED')
            require(await policy([]) == [])
            outcome['checks'].append(stage)
            os.environ.update(WEKNORA_COMPANY_KB_ENABLED='true', WEKNORA_COMPANY_KB_ORGANIZATION_IDS=json.dumps([ORG]))

            stage = CHECKS[2]
            calls = await policy([(200, ORG_DATA), (200, ORG_DATA), (200, dict(ORG_DATA, my_role='viewer')), (200, SHARED)])
            require([(r.method, r.url.path) for r in calls] == [
                ('GET', '/api/v1/organizations/' + ORG), ('POST', '/api/v1/organizations/join-by-id'),
                ('GET', '/api/v1/organizations/' + ORG), ('GET', '/api/v1/shared-knowledge-bases')])
            require(json.loads(calls[1].content) == {'organization_id': ORG, 'role': 'viewer'})
            outcome['checks'].append(stage)

            stage = CHECKS[3]
            calls = await policy([(200, dict(ORG_DATA, my_role='viewer')), (200, SHARED)])
            require(all(r.method == 'GET' for r in calls))
            outcome['checks'].append(stage)

            stage = CHECKS[4]
            os.environ['WEKNORA_COMPANY_KB_ORGANIZATION_IDS'] = json.dumps([ORG, SECOND])
            calls = await policy([(200, dict(ORG_DATA, my_role='viewer')), (200, dict(ORG_DATA, id=SECOND, my_role='viewer')),
                                  (200, SHARED + [{'organization_id': 'unselected-private', 'permission': 'editor'}])])
            require(len(calls) == 3 and all(r.method == 'GET' for r in calls))
            os.environ['WEKNORA_COMPANY_KB_ORGANIZATION_IDS'] = json.dumps([ORG])
            outcome['checks'].append(stage)

            stage = CHECKS[5]
            for change in ({'searchable': False}, {'require_approval': True}, {'member_limit': 50}):
                calls = await policy([(200, dict(ORG_DATA, **change))], 'COMPANY_KB_ORGANIZATION_NOT_GLOBAL')
                require(len(calls) == 1 and calls[0].method == 'GET')
            outcome['checks'].append(stage)

            stage = CHECKS[6]
            for role in ('admin', 'editor'):
                calls = await policy([(200, dict(ORG_DATA, my_role=role))], 'COMPANY_KB_VIEWER_MEMBERSHIP_REQUIRED')
                require(len(calls) == 1 and calls[0].method == 'GET')
            outcome['checks'].append(stage)

            stage = CHECKS[7]
            await policy([(200, dict(ORG_DATA, my_role='viewer')), (200, [dict(SHARED[0], permission='editor')])],
                         'COMPANY_KB_READONLY_SHARE_REQUIRED')
            outcome['checks'].append(stage)

            stage = CHECKS[8]
            for confirmed in (True, False):
                replies = [(200, ORG_DATA), httpx.ReadTimeout('synthetic-secret-error'),
                           (200, dict(ORG_DATA, my_role='viewer' if confirmed else ''))]
                if confirmed:
                    replies.append((200, SHARED))
                calls = await policy(replies, None if confirmed else 'COMPANY_KB_VIEWER_MEMBERSHIP_REQUIRED')
                require(sum(r.method == 'POST' for r in calls) == 1)
            outcome['checks'].append(stage)

            stage = CHECKS[9]
            os.environ['WEKNORA_COMPANY_KB_ORGANIZATION_IDS'] = 'invalid'
            require(await policy([], admin=True) == [])
            os.environ['WEKNORA_COMPANY_KB_ORGANIZATION_IDS'] = json.dumps([ORG])
            outcome['checks'].append(stage)

            stage = CHECKS[10]
            await policy([httpx.ConnectError('synthetic-secret-error')], 'COMPANY_KB_TRANSPORT_FAILED')
            with patch.object(company, '_MAX_RESPONSE_BYTES', 16):
                await policy([(200, ORG_DATA)], 'COMPANY_KB_RESPONSE_TOO_LARGE')
            outcome['checks'].append(stage)

            stage = CHECKS[11]
            native_calls, grants = [], []

            def auth_handler(request):
                require(request.url.host == 'synthetic.invalid')
                native_calls.append(request.url.path)
                outcome['simulated_http_requests'] += 1
                if request.url.path == '/api/v1/auth/register':
                    return httpx.Response(400)
                require(request.url.path == '/api/v1/auth/login')
                return httpx.Response(200, json={'success': True, 'token': TOKEN, 'refresh_token': 'synthetic-refresh',
                    'user': {'id': 'synthetic-native-user'}, 'active_tenant': {'id': 77}, 'memberships': [{'tenant_id': 77, 'role': 'owner'}]})

            async def grant(s, ep, *, administrator):
                require(s.token == TOKEN and s.tenant['id'] == 77 and not administrator)
                require(ep('/organizations') == endpoint('/organizations'))
                grants.append(True)
                if len(grants) == 1:
                    raise company._unavailable('COMPANY_KB_JOIN_UNCONFIRMED')

            async def synced(user, s):
                return s

            async def noop(*args):
                pass

            with ExitStack() as mocks:
                mocks.enter_context(patch.object(httpx, 'AsyncClient', lambda **kw: real_client(**kw, transport=httpx.MockTransport(auth_handler))))
                for name, value in {
                    '_session_cache': {}, '_sso_secret': lambda: 'synthetic-secret',
                    'weknora_api_url': lambda: 'http://synthetic.invalid', 'weknora_timeout': lambda: 5,
                    '_session_cache_ttl': lambda: 900, 'sync_weknora_role': synced,
                    'ensure_company_knowledge_access': grant, 'ensure_weknora_default_models': noop,
                    'ensure_weknora_default_mineru': noop, 'ensure_weknora_default_web_search': noop,
                }.items():
                    mocks.enter_context(patch.object(identity, name, value))
                user = SimpleNamespace(id='synthetic-platform-user', email='fixture@example.invalid', system_role='user')
                rejected = False
                try:
                    await identity.ensure_weknora_session_for_deerflow_user(user)
                except HTTPException as exc:
                    rejected = exc.detail.get('code') == 'COMPANY_KB_JOIN_UNCONFIRMED'
                require(rejected and not identity._session_cache and len(grants) == 1)
                outcome['checks'].append(stage)
                stage = CHECKS[12]
                first = await identity.ensure_weknora_session_for_deerflow_user(user)
                second = await identity.ensure_weknora_session_for_deerflow_user(user)
                require(first is second and len(grants) == 2)
                require(native_calls == ['/api/v1/auth/register', '/api/v1/auth/login'] * 2)
                outcome['checks'].append(stage)
        require(outcome['checks'] == CHECKS)
        outcome['status'] = 'INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED'
    except Exception as exc:
        outcome.update(check_code=stage, error_class=type(exc).__name__)
        if isinstance(exc, ModuleNotFoundError):
            name = exc.name or ''
            if name.replace('.', '').replace('_', '').isalnum() and len(name) < 120:
                outcome['missing_python_module'] = name
    return outcome


if __name__ == '__main__':
    # Imported code may log. Only the bounded aggregate result is returned.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        result = asyncio.run(qualify(Path('/app/backend'), json.loads(sys.argv[1])))
    print(json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result['status'] == 'INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED' else 1)
