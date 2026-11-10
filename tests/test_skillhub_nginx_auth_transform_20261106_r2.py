"""Offline insertion/replay-policy tests; synthetic config, no production access."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'deploy/enterprise/skillhub_nginx_auth_transform_20261106_r2.py'
spec = importlib.util.spec_from_file_location('skillhub_edge_transform_test', TOOL)
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)
INSPECTOR = ROOT / 'deploy/enterprise/inspect-portal-skillhub-auth-baseline-20261105-r1.py'
inspect_spec = importlib.util.spec_from_file_location('original_production_inspector_for_r2_tests', INSPECTOR)
inspector = importlib.util.module_from_spec(inspect_spec)
inspect_spec.loader.exec_module(inspector)


HEADERS = '''
            proxy_set_header Forwarded "";
            proxy_set_header X-Forwarded-Port "";
            proxy_set_header X-Forwarded-Host $http_host;
            proxy_set_header X-Forwarded-Proto $forwarded_proto;
            proxy_set_header X-SkillHub-Identity "";
            proxy_set_header X-SkillHub-Signature "";
            proxy_hide_header X-Powered-By;
            proxy_hide_header X-AspNet-Version;
            proxy_hide_header X-AspNetMvc-Version;
'''


def fixture():
    return ('''worker_processes 1;
events { worker_connections 16; }
http {
    map_hash_bucket_size 128;
    map $http_x_forwarded_proto $forwarded_proto { default $scheme; }
    server {
        listen 2026 default_server;
        server_name portal.example.test;
        add_header X-Unrelated "keep original" always;
        # Unrelated Unicode: 原有配置保持不变。
        location /ordinary/ { proxy_pass http://gateway:8001; }
        location /api/ppt-master-runtime/ {
            if ($portal_ppt_agent_new_run_retired_20261104) {
                return 409 '{"code":"PPT_AGENT_RETIRED"}';
            }
            rewrite ^/api/ppt-master-runtime/(.*)$ /$1 break;
            proxy_pass http://ppt-master-runner:8003;
        }
        location = /platform/skillhub { return 308 /platform/skillhub/; }
        location = /_internal/skillhub/session-assertion {
            internal;
            proxy_pass http://gateway:8001/internal/skillhub/session/assertion;
        }
        location = /platform/skillhub/api/v1/auth/session/bootstrap {
            auth_request /_internal/skillhub/session-assertion;
            auth_request_set $skillhub_identity $upstream_http_x_skillhub_identity;
            auth_request_set $skillhub_signature $upstream_http_x_skillhub_signature;
            proxy_set_header Cookie "";
            proxy_pass http://skillhub-web:80;
        }
        location = /platform/skillhub/api/web/notifications/sse {
            proxy_pass http://skillhub-web:80;''' + HEADERS + '''
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 660s;
        }
        location ^~ /platform/skillhub/ {
            proxy_pass http://skillhub-web:80;''' + HEADERS + '''
            proxy_http_version 1.1;
            proxy_set_header Cookie $http_cookie;
            proxy_pass_header Set-Cookie;
            proxy_set_header Accept-Encoding "";
            sub_filter_once off;
            sub_filter "frame-ancestors 'none'" "frame-ancestors 'self'";
        }
    }
    map "$request_method:$uri" $portal_ppt_agent_new_run_retired_20261104 {
        default 0;
        ~^POST:/api/ppt-master-runtime/runs/stream$ 1;
    }
}
''').encode()


def nodes(raw):
    return list(edge.walk(edge.parse_blocks(raw.decode())))


def location(raw, path):
    return edge.one([n for n in nodes(raw) if n['header'][:1] == ['location']
                     and n['header'][-1:] == [path]], 'test_location')


def body(raw, path):
    return edge.block(raw.decode(), location(raw, path)).encode()


def synthetic_startup(expressions=None):
    expressions = expressions or ['s|unused%d|replacement%d|g' % (n, n) for n in range(7)]
    script = 'cp /etc/nginx/nginx.conf.template /tmp/nginx.conf; '
    script += '; '.join('sed -i ' + shlex.quote(x) + ' /tmp/nginx.conf' for x in expressions)
    script += "; exec nginx -c /tmp/nginx.conf -g 'daemon off;'"
    container = {'command': ['sh', '-c'], 'args': [script]}
    identities = tuple((len(x.encode()), edge.sha(x.encode())) for x in expressions)
    return container, identities


def original_inspector_pins(raw):
    return {tuple(row['header']): row['block_sha256']
            for row in inspector.summarize_config(raw)['skillhub_routes']}


def production_route_fixture():
    """Rebuild only independently sealed old routes from tracked configuration.

    Historical production places its existing framework-header and forwarded-
    authority directives first. Values and remaining order are unchanged.
    Every rebuilt route must match the five immutable operator-observed hashes;
    this function never substitutes new expected hashes for that production pin.
    """
    reviewed = (ROOT / 'k8s/local-pvc/nginx.conf').read_text(encoding='utf-8')
    production = {}
    for node in edge.walk(edge.parse_blocks(reviewed)):
        header = tuple(node['header'])
        if header not in edge.OLD_ROUTE_HASHES:
            continue
        if node['children']:
            raise AssertionError('unexpected child in historical SkillHub route')
        directives = [d for d in node['directives'] if d not in (
            ['auth_request', edge.API_AUTH], ['auth_request', edge.BROWSER_AUTH],
            ['error_page', '401', '=', '@skillhub_login'])]
        hide = [d for d in directives if d[:1] == ['proxy_hide_header']
                and d[1] in ('X-Powered-By', 'X-AspNet-Version', 'X-AspNetMvc-Version')]
        forward = [d for d in directives if d[:1] == ['proxy_set_header']
                   and d[1] in ('Forwarded', 'X-Forwarded-Host', 'X-Forwarded-Port')]
        ordered = hide + forward + [d for d in directives if d not in hide and d not in forward]
        production[header] = ' '.join(header) + ' {\n' + ''.join('    '+' '.join(d)+';\n' for d in ordered) + '}'
    result = fixture().decode()
    replacements = []
    for node in edge.walk(edge.parse_blocks(result)):
        header = tuple(node['header'])
        if header in production:
            replacements.append((node['start'], node['close'] + 1, production[header]))
    for start, end, text in sorted(replacements, reverse=True):
        result = result[:start] + text + result[end:]
    raw = result.encode()
    if original_inspector_pins(raw) != edge.OLD_ROUTE_HASHES:
        raise AssertionError('tracked source no longer reproduces observed production route identities')
    return raw


class AuthTransformerTests(unittest.TestCase):
    def setUp(self):
        self.original = fixture()

    def synthetic_plan(self):
        container, identities = synthetic_startup()
        hashes = {tuple(row['header']): row['block_sha256']
                  for row in inspector.summarize_config(self.original)['skillhub_routes']}
        for name, value in (
            ('CURRENT_TEMPLATE_SHA', edge.sha(self.original)),
            ('CURRENT_LIVE_SHA', edge.sha(self.original)),
            ('STARTUP_SHA', edge.startup_digest(container)),
            ('EXPRESSION_IDENTITIES', identities),
            ('OLD_ROUTE_HASHES', hashes),
        ):
            patcher = patch.object(edge, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()
        return edge.plan_production_pair(self.original, self.original, container)

    def test_insertion_proof_and_old_ppt_routes_unchanged(self):
        candidate, summary = edge._transform(self.original)
        self.assertTrue(summary['existing_bytes_preserved'])
        self.assertEqual(summary['insertions'], 5)
        for path in ('/api/ppt-master-runtime/', '/ordinary/', edge.ASSERTION, edge.BOOTSTRAP):
            self.assertEqual(body(self.original, path), body(candidate, path))
        old_map = next(n for n in nodes(self.original) if n['header'][-1:] == [edge.PPT_VARIABLE])
        new_map = next(n for n in nodes(candidate) if n['header'][-1:] == [edge.PPT_VARIABLE])
        self.assertEqual(edge.block(self.original.decode(), old_map), edge.block(candidate.decode(), new_map))
        self.assertIn('原有配置保持不变。'.encode(), candidate)

    def test_map_after_existing_http_directives(self):
        candidate, _ = edge._transform(self.original)
        self.assertGreater(candidate.index(b'map $http_authorization'), candidate.index(b'map_hash_bucket_size 128;'))
        self.assertGreater(candidate.index(b'map $http_authorization'), candidate.index(b'map "$request_method:$uri"'))
        mapping = next(n for n in nodes(candidate) if n['header'][-1:] == [edge.BEARER_VARIABLE])
        self.assertEqual(mapping['directives'], [['default', '0'], ['"~*^Bearer[ ]+.+$"', '1']])

    def test_page_api_sse_bootstrap_separated(self):
        candidate, _ = edge._transform(self.original)
        expected = {edge.PAGE_PREFIX: edge.BROWSER_AUTH, edge.API_PREFIX: edge.API_AUTH,
                    edge.SSE: edge.API_AUTH, edge.BOOTSTRAP: edge.ASSERTION}
        for path, auth in expected.items():
            with self.subTest(path=path):
                self.assertEqual([d for d in location(candidate, path)['directives'] if d[:1] == ['auth_request']],
                                 [['auth_request', auth]])
        self.assertIn(['error_page', '401', '=', '@skillhub_login'], location(candidate, edge.PAGE_PREFIX)['directives'])
        self.assertFalse(any(d[:1] == ['error_page'] for d in location(candidate, edge.API_PREFIX)['directives']))
        self.assertEqual(location(candidate, '@skillhub_login')['directives'], [['return', '302', '/login']])

    def test_bearer_bypass_only_internal_api_check(self):
        candidate, _ = edge._transform(self.original)
        api, browser = location(candidate, edge.API_AUTH), location(candidate, edge.BROWSER_AUTH)
        self.assertIn(['internal'], api['directives'])
        self.assertIn(['internal'], browser['directives'])
        self.assertEqual(browser['children'], [])
        self.assertEqual(len(api['children']), 1)
        self.assertEqual(api['children'][0]['directives'], [['return', '204']])
        self.assertEqual(api['children'][0]['header'], ['if', '(' + edge.BEARER_VARIABLE + ')'])
        self.assertIn(['proxy_pass', 'http://gateway:8001/api/v1/auth/me'], api['directives'])

    def test_api_clone_keeps_all_existing_page_headers(self):
        candidate, _ = edge._transform(self.original)
        old = location(self.original, edge.PAGE_PREFIX)['directives']
        new = location(candidate, edge.API_PREFIX)['directives']
        self.assertEqual(new[1:], old)
        for path in (edge.API_PREFIX, edge.API_AUTH, edge.BROWSER_AUTH):
            actual = location(candidate, path)['directives']
            for name in ('X-Powered-By', 'X-AspNet-Version', 'X-AspNetMvc-Version'):
                self.assertIn(['proxy_hide_header', name], actual)
            for header, value in [('Forwarded', '""'), ('X-Forwarded-Port', '""'), ('X-Forwarded-Host', '$http_host')]:
                self.assertIn(['proxy_set_header', header, value], actual)

    def test_new_auth_locations_match_reviewed_repo_directives(self):
        reviewed = (ROOT / 'k8s/local-pvc/nginx.conf').read_bytes()
        candidate, _ = edge._transform(self.original)
        for path in (edge.BROWSER_AUTH, edge.API_AUTH, '@skillhub_login'):
            actual, expected = location(candidate, path), location(reviewed, path)
            self.assertEqual(actual['directives'], expected['directives'])
            self.assertEqual([(n['header'], n['directives']) for n in actual['children']],
                             [(n['header'], n['directives']) for n in expected['children']])

    def test_refuses_second_application_or_conflicting_names(self):
        candidate, _ = edge._transform(self.original)
        for value in (candidate, self.original + b'\n# $skillhub_bearer_request'):
            with self.subTest(value=edge.sha(value)), self.assertRaisesRegex(ValueError, 'already_guarded'):
                edge._transform(value)

    def test_refuses_missing_or_changed_required_defenses(self):
        changes = [
            (b'proxy_set_header Forwarded "";', b''),
            (b'proxy_hide_header X-Powered-By;', b''),
            (b'proxy_pass http://skillhub-web:80;', b'proxy_pass http://other:80;'),
            (b'proxy_set_header X-Forwarded-Proto $forwarded_proto;', b'proxy_set_header X-Forwarded-Proto $scheme;'),
            (b'PPT_AGENT_RETIRED', b'PPT_OTHER'),
            (b'auth_request /_internal/skillhub/session-assertion;', b'auth_request off;'),
        ]
        for before, after in changes:
            with self.subTest(before=before), self.assertRaises(ValueError):
                edge._transform(self.original.replace(before, after))

    def test_refuses_includes_unknown_routes_and_preexisting_page_auth(self):
        variants = [
            self.original.replace(b'http {', b'http { include /tmp/extra.conf;'),
            self.original.replace(b'location /ordinary/', b'location /skillhub-unknown/'),
            self.original.replace(b'location ^~ /platform/skillhub/ {', b'location ^~ /platform/skillhub/ { auth_request off;'),
            self.original.replace(b'location ^~ /platform/skillhub/ {', b'location ^~ /platform/skillhub/ { if ($uri) { return 403; }'),
        ]
        for value in variants:
            with self.subTest(digest=edge.sha(value)), self.assertRaises(ValueError):
                edge._transform(value)

    def test_current_hash_pins_reject_stale_or_unknown_bytes(self):
        self.assertNotEqual(edge.CURRENT_TEMPLATE_SHA, edge.STALE_MOUNT_SHA)
        with patch.object(edge, '_transform') as transform:
            with self.assertRaisesRegex(ValueError, 'current_configmap_hash_changed_or_stale_mount'):
                edge.plan_production_pair(self.original, self.original, {})
            transform.assert_not_called()
        with patch.object(edge, 'CURRENT_TEMPLATE_SHA', edge.sha(self.original)):
            with self.assertRaisesRegex(ValueError, 'current_live_hash_changed'):
                edge.plan_production_pair(self.original, self.original, {})

    def test_route_hashes_checked_independently(self):
        hashes = dict(edge.OLD_ROUTE_HASHES)
        with self.assertRaisesRegex(ValueError, 'existing_route_hash_changed'):
            edge._transform(self.original, hashes)

    def test_startup_exact_seven_literal_substitutions(self):
        container, identities = synthetic_startup()
        expressions = edge._parse_startup(container, edge.startup_digest(container), identities)
        self.assertEqual(len(expressions), 7)
        self.assertEqual(expressions[0], 's|unused0|replacement0|g')
        with self.assertRaisesRegex(ValueError, 'startup_identity_changed'):
            edge.parse_startup(container)

    def test_startup_rejects_six_or_eight_commands(self):
        for count in (6, 8):
            container, _ = synthetic_startup(['s|a|b|g'] * count)
            ids = tuple((len(b's|a|b|g'), edge.sha(b's|a|b|g')) for _ in range(7))
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'startup_command_count'):
                edge._parse_startup(container, edge.startup_digest(container), ids)

    def test_startup_refuses_execution_redirection_and_sed_side_effects(self):
        for expression in ('s|a|b|e', 's|a|b|w /tmp/x', 's|a|b|;q', 's|a|b|\nq', '/a/d'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                edge.validate_substitution(expression)
        for script in ('cp a b && echo c', 'echo $(id)', 'echo `id`', 'echo a > /tmp/b'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                edge._literal_commands(script)

    def test_candidate_and_replay_never_claim_activation_ready(self):
        plan = self.synthetic_plan()
        summary = plan['summary']
        self.assertFalse(summary['ready_for_activation'])
        self.assertTrue(summary['native_server_auth_acceptance_required'])
        self.assertFalse(summary['startup_replay_verified'])
        self.assertFalse(summary['nginx_syntax_verified'])
        self.assertEqual(len(plan['startup_sed_expressions']), 7)
        replay = edge.verify_startup_replay(plan, self.original, plan['candidate_live'])
        self.assertTrue(replay['startup_replay_verified'])
        self.assertFalse(replay['ready_for_activation'])
        self.assertTrue(replay['native_server_auth_acceptance_required'])

    def test_replay_mismatch_or_changed_candidate_refused(self):
        plan = self.synthetic_plan()
        with self.assertRaisesRegex(ValueError, 'original_startup'):
            edge.verify_startup_replay(plan, self.original + b' ', plan['candidate_live'])
        with self.assertRaisesRegex(ValueError, 'startup_changes'):
            edge.verify_startup_replay(plan, self.original, plan['candidate_live'] + b' ')
        plan['candidate_live'] += b' '
        with self.assertRaisesRegex(ValueError, 'candidate_plan_changed'):
            edge.verify_startup_replay(plan, self.original, plan['candidate_live'])

    def test_changed_replay_expression_refused(self):
        plan = self.synthetic_plan()
        plan['startup_sed_expressions'] = ('s|a|b|w /tmp/x',) * 7
        with self.assertRaisesRegex(ValueError, 'plan_startup_expressions_changed'):
            edge.verify_startup_replay(plan, self.original, plan['candidate_live'])

    def test_original_inspector_projection_functions_copied_exactly(self):
        original = ast.parse(INSPECTOR.read_text(encoding='utf-8'))
        current = ast.parse(TOOL.read_text(encoding='utf-8'))
        def functions(tree):
            return {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, ast.FunctionDef)}
        old, new = functions(original), functions(current)
        for name in ('canonical', 'tokenize_config', 'parse_config'):
            self.assertEqual(old[name], new[name], name)

    def test_actual_production_redirect_pin_matches_original_inspector(self):
        route = b'location = /platform/skillhub { return 308 /platform/skillhub/; }'
        expected = 'b1fdb13d442259a0c2af484976e7f35c1a034c19eb95f1b50ff79c0a05cae4ce'
        key = ('location', '=', '/platform/skillhub')
        self.assertEqual(original_inspector_pins(route)[key], expected)
        self.assertEqual(edge.structured_route_hashes(route.decode())[key], expected)
        self.assertEqual(edge.OLD_ROUTE_HASHES[key], expected)
        self.assertNotEqual(edge.sha(body(route, '/platform/skillhub')), expected)

    def test_all_five_production_route_pins_reproduced_without_rebinding(self):
        raw = production_route_fixture()
        self.assertEqual(original_inspector_pins(raw), edge.OLD_ROUTE_HASHES)
        candidate, summary = edge._transform(raw, edge.OLD_ROUTE_HASHES)
        self.assertTrue(summary['existing_bytes_preserved'])
        self.assertIn(b'auth_request /_internal/skillhub/browser-auth;', candidate)

    def test_r1_failure_and_byte_identical_candidate_after_hash_correction(self):
        path = ROOT / 'deploy/enterprise/skillhub_nginx_auth_transform_20261106.py'
        spec = importlib.util.spec_from_file_location('immutable_policy_r1_comparison', path)
        prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
        for raw in (fixture(), production_route_fixture()):
            pins = original_inspector_pins(raw)
            with self.assertRaisesRegex(ValueError, 'existing_route_hash_changed'):
                prior._transform(raw, pins)
            raw_pins = {tuple(n['header']): prior.sha(prior.block(raw.decode(), n).encode())
                for n in prior.walk(prior.parse_blocks(raw.decode())) if tuple(n['header']) in prior.OLD_ROUTE_HASHES}
            old_candidate, old_summary = prior._transform(raw, raw_pins)
            new_candidate, new_summary = edge._transform(raw, pins)
            self.assertEqual(new_candidate, old_candidate)
            self.assertEqual(new_summary, old_summary)
        self.assertEqual(prior.OLD_ROUTE_HASHES, edge.OLD_ROUTE_HASHES)
        for name in ('CURRENT_TEMPLATE_SHA', 'CURRENT_LIVE_SHA', 'STALE_MOUNT_SHA', 'STARTUP_SHA', 'EXPRESSION_IDENTITIES'):
            self.assertEqual(getattr(prior, name), getattr(edge, name))

    def test_whitespace_comments_do_not_change_structural_identity(self):
        raw = production_route_fixture(); expected = original_inspector_pins(raw)
        variant = raw.replace(b'return 308 /platform/skillhub/;', b'return \n 308   /platform/skillhub/; # comment\n')
        variant = variant.replace(b'proxy_set_header Forwarded "";', b'# preserved header\n proxy_set_header   Forwarded  "" ;')
        self.assertNotEqual(edge.sha(raw), edge.sha(variant))
        self.assertEqual(original_inspector_pins(variant), expected)
        self.assertEqual(edge.structured_route_hashes(variant.decode()), expected)
        edge._transform(variant, expected)
        with patch.object(edge, 'CURRENT_TEMPLATE_SHA', edge.sha(raw)), self.assertRaisesRegex(ValueError, 'current_configmap_hash'):
            edge.plan_production_pair(variant, raw, {})

    def test_semantic_and_directive_order_changes_reject_pinned_routes(self):
        raw = production_route_fixture()
        variants = [raw.replace(b'return 308 /platform/skillhub/;', b'return 301 /platform/skillhub/;'),
                    raw.replace(b'proxy_pass http://skillhub-web:80;', b'proxy_pass http://skillhub-web:81;'),
                    raw.replace(b'proxy_set_header Forwarded "";', b'proxy_set_header Forwarded $http_forwarded;')]
        first = b'proxy_hide_header X-Powered-By;\n    proxy_hide_header X-AspNet-Version;'
        variants.append(raw.replace(first, b'proxy_hide_header X-AspNet-Version;\n    proxy_hide_header X-Powered-By;'))
        for variant in variants:
            with self.subTest(digest=edge.sha(variant)), self.assertRaisesRegex(ValueError, 'existing_route_hash_changed'):
                edge._transform(variant, edge.OLD_ROUTE_HASHES)

    def test_quoted_escaped_unicode_and_nested_projection_matches_inspector(self):
        values = [
            'location = /skillhub-test { add_header Example "a b # c; { d }"; }',
            r'location = /skillhub-test { set $x "a\"b"; set $y a\;b; }',
            "location = /skillhub-test { set $x '中文 值'; if ($uri) { return 401; } set $y done; }",
            'location = /skillhub-test { set $x $value; # comment\n add_header Z ""; }',
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(edge.tokenize_config(value), inspector.tokenize_config(value))
                self.assertEqual(edge.parse_config(value), inspector.parse_config(value))
                self.assertEqual(edge.structured_route_hashes(value), original_inspector_pins(value.encode()))

    def test_original_projection_error_codes_and_depth_limits_preserved(self):
        values = ['location = /skillhub-test { ; }', 'location = /skillhub-test {',
                  'location = /skillhub-test { set $x "unclosed; }',
                  'a { ' * 26 + 'set x y;' + ' }' * 26]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(inspector.Refused) as original:
                    inspector.parse_config(value)
                with self.assertRaises(ValueError) as current:
                    edge.parse_config(value)
                self.assertEqual(str(current.exception), str(original.exception))
                self.assertIn(str(current.exception), edge.POLICY_ERROR_CODES)

    def test_duplicate_projection_route_rejected(self):
        value = 'location /skillhub-test { return 401; }' * 2
        with self.assertRaisesRegex(ValueError, 'duplicate_route_projection'):
            edge.structured_route_hashes(value)

    def test_proxy_pass_assumptions_match_observed_original_inspector_hashes(self):
        for value, expected in (
            ('http://skillhub-web:80', '602ad669f47baa6bff4791128d355a2ebd34679bc6ed2b46649c9558ba53fa67'),
            ('http://gateway:8001/internal/skillhub/session/assertion', '3bdcec69907ecdd3b634c0a253705c50536c5bf2b6eebbf4e2a6f1974bc223e1')):
            self.assertEqual(inspector.sha(inspector.canonical(['proxy_pass', value])), expected)
        raw = production_route_fixture()
        for path in (edge.PAGE_PREFIX, edge.SSE):
            directives = location(raw, path)['directives']
            self.assertIn(['proxy_set_header', 'X-Forwarded-Host', '$http_host'], directives)
            self.assertIn(['proxy_set_header', 'X-Forwarded-Proto', '$forwarded_proto'], directives)
            self.assertIn(['proxy_set_header', 'X-SkillHub-Identity', '""'], directives)
            self.assertIn(['proxy_set_header', 'X-SkillHub-Signature', '""'], directives)

    def test_fixed_error_whitelist_covers_only_literal_policy_diagnostics(self):
        tree = ast.parse(TOOL.read_text(encoding='utf-8')); expected = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ('require', '_require', 'one') and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    expected.add(node.args[1].value)
                if node.func.id == 'ValueError' and node.args and isinstance(node.args[0], ast.Constant):
                    expected.add(node.args[0].value)
        self.assertEqual(edge.POLICY_ERROR_CODES, frozenset(expected))
        self.assertTrue(all(isinstance(code, str) and code.replace('_', '').isalnum() for code in edge.POLICY_ERROR_CODES))
        self.assertNotIn('arbitrary configuration or credentials', edge.POLICY_ERROR_CODES)

    def test_python37_and_module_purity(self):
        source = TOOL.read_text(encoding='utf-8')
        tree = ast.parse(source, feature_version=(3, 7))
        allowed = {'hashlib', 'json', 're', 'shlex'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue({x.name for x in node.names} <= allowed)
            if isinstance(node, ast.ImportFrom):
                self.fail('Unexpected import from ' + str(node.module))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ('open', 'exec', 'eval', '__import__', 'print'))


if __name__ == '__main__':
    unittest.main()
