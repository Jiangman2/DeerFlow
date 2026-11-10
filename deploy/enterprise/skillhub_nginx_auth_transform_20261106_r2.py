"""Pure, insertion-only SkillHub Nginx auth planner; no production operations.

Only the currently observed ConfigMap and live file are accepted by the public
planner. The stale mounted template is never an input. This module cannot read
files, run commands, contact Kubernetes, patch a ConfigMap or reload Nginx.

The returned candidate is NOT authorization to activate. A future caller must
first establish deployed native SkillHub authentication on every backend, replay
the seven pinned substitutions with the existing container's sed, run nginx -t,
CAS both current configuration identities, and perform runtime/browser checks.
Never publish the bearer edge bypass before native backend authentication passes.

Lexer/parser and literal startup validation derive from the audited PPT entry
resume tool; the existing retirement guard is preserved byte-for-byte. The startup parser retains the exact reviewed seven-command requirement. Python 3.7 compatible; no CLI.
"""
import hashlib
import json
import re

CURRENT_TEMPLATE_SHA = '85a9aee439cee462aaf81788594e55526ece023cbb4d63ff18b92276032dcbb4'
CURRENT_LIVE_SHA = '139221024e43ad49e706138ae740093c1eefdb46f7f807094d10a39e1ad4a755'
STALE_MOUNT_SHA = '070b256f563a5bb712183feffaec21f8f41154fe7ca2c6a7369a60214233e5dd'
MARKER = 'portal-skillhub-auth-edge-20261106-r1'
PPT_VARIABLE = '$portal_ppt_agent_new_run_retired_20261104'
BEARER_VARIABLE = '$skillhub_bearer_request'
BROWSER_AUTH = '/_internal/skillhub/browser-auth'
API_AUTH = '/_internal/skillhub/api-auth'
ASSERTION = '/_internal/skillhub/session-assertion'
BOOTSTRAP = '/platform/skillhub/api/v1/auth/session/bootstrap'
SSE = '/platform/skillhub/api/web/notifications/sse'
API_PREFIX = '/platform/skillhub/api/'
PAGE_PREFIX = '/platform/skillhub/'
OLD_ROUTE_HASHES = {
    ('location', '=', '/platform/skillhub'): 'b1fdb13d442259a0c2af484976e7f35c1a034c19eb95f1b50ff79c0a05cae4ce',
    ('location', '=', ASSERTION): '9d337f49eb8430e47b1e52f472e92cca9522cefbc3799e0c92d231b119a6fb99',
    ('location', '=', BOOTSTRAP): 'a8fa321a173cdac6344a9afa04887be75ac3d79738da63402d6a8632f8f14b58',
    ('location', '=', SSE): 'ef29bd0d875e56f11e010be20bfbcecfc4e62130b1f4cdbde1c8f48a47abb198',
    ('location', '^~', PAGE_PREFIX): '78599fe87aab294207f692816035220ee95d0890f3ea1865750aa4a40b1975ee',
}


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def lex(text):
    tokens, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "#":
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            continue
        start = i
        if text[i] in "{};":
            i += 1
        elif text[i] in "\"'":
            quote = text[i]
            i += 1
            while i < len(text) and text[i] != quote:
                i += 2 if text[i] == "\\" else 1
            require(i < len(text), "unclosed_nginx_quote")
            i += 1
        else:
            while i < len(text) and not text[i].isspace() and text[i] not in "{};#":
                if text.startswith("${", i):
                    end = text.find("}", i)
                    require(end >= 0, "unclosed_nginx_variable")
                    i = end + 1
                else:
                    i += 2 if text[i] == "\\" else 1
        tokens.append((text[start:i], start, i))
    return tokens

def parse_blocks(text):
    root = {"header": [], "children": [], "directives": []}
    stack, pending = [root], []
    for value, start, end in lex(text):
        if value == "{":
            require(bool(pending), "missing_block_header")
            node = {"header": [x[0] for x in pending], "start": pending[0][1], "open": start, "children": [], "directives": []}
            stack[-1]["children"].append(node)
            stack.append(node)
            pending = []
        elif value == "}":
            require(len(stack) > 1 and not pending, "unbalanced_nginx_block")
            stack.pop()["close"] = start
        elif value == ";":
            stack[-1]["directives"].append([x[0] for x in pending])
            pending = []
        else:
            pending.append((value, start, end))
    require(len(stack) == 1 and not pending, "incomplete_nginx_config")
    return root

"""Parse the observed Nginx startup into literal, pure sed substitutions.

No files, subprocesses, network or shell execution. Python 3.7 compatible.
The public entry point always uses the production evidence identities below.
"""
import hashlib
import json
import re
import shlex

STARTUP_SHA = 'c10f308cc45d31d3861799e706e5adef53e52f9c78d2f171bf47f13f29424cc3'
STARTUP_SHA256 = STARTUP_SHA
EXPRESSION_IDENTITIES = (
    (104, 'c8075a28f07dd1bb01130655f71f5b70dd0f7baa652d00664c389e98afda9a01'),
    (82, '2bd9315ff4063890fa8866e9f978e8229713d253dca7e5f3c756259947d480bb'),
    (85, '634bbc8cef07b7703ef17524d3ce1e10b8a96393ef82d4b7252259153a8646a1'),
    (94, '0d8927f8a540c08f427c08c3f1634ed8374bc905372c52cd359a4b62f46f4d82'),
    (72, '7bb56de719c6fd4c466c7607ecf1c10840249eadbfa6a063b68dd8e411578cc2'),
    (74, 'edb9873122fe92cd1bcc94a4d5f69b0ca3e25eddfbb43d89fb97f25be9571a5d'),
    (80, 'afc30173babdc20ef307b6217fe6de5e4d79f7d90eeba6f49d4fe514523044e9'),
)
TEMPLATE = '/etc/nginx/nginx.conf.template'
LIVE = '/tmp/nginx.conf'


def _require(ok, code):
    if not ok:
        raise ValueError(code)


def startup_digest(container):
    """Use exactly the canonical JSON scheme used by the readonly probe."""
    value = {'command': container.get('command'), 'args': container.get('args')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _literal_commands(script):
    """Accept only independent simple commands, with literal complete words.

    A quoted word cannot concatenate with another word/quote. Double quotes
    support POSIX backslash handling, but all dollar/backtick syntax is refused
    there (including escaped forms). Unquoted words are a safe ASCII subset.
    Semicolons/newlines are separators; shell operators and continuations are
    never interpreted. Comments are intentionally outside this grammar.
    """
    _require(isinstance(script, str) and len(script.encode()) <= 8192, 'startup_text_bound')
    _require(not any(ord(c) < 32 and c not in '\n\t' for c in script), 'startup_control_character')
    commands, words, i = [], [], 0
    while i < len(script):
        c = script[i]
        if c in ' \t':
            i += 1
            continue
        if c in '\n;':
            _require(c != ';' or bool(words), 'startup_empty_shell_command')
            if words:
                commands.append(words)
                words = []
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            value = []
            while i < len(script) and script[i] != quote:
                c = script[i]
                _require(c not in '\n\r\0`', 'startup_quoted_word_unsupported')
                if quote == '"':
                    _require(c != '$', 'startup_shell_expansion_forbidden')
                    if c == '\\':
                        _require(i + 1 < len(script), 'startup_unclosed_quote')
                        following = script[i + 1]
                        _require(following not in '\n\r$`', 'startup_shell_expansion_forbidden')
                        # POSIX double quotes remove backslash only before
                        # dollar, backtick, double quote, backslash or newline.
                        # Dollar/backtick/newline are already refused above.
                        if following in '\\"':
                            value.append(following)
                        else:
                            value.extend(('\\', following))
                        i += 2
                        continue
                value.append(c)
                i += 1
            _require(i < len(script) and script[i] == quote, 'startup_unclosed_quote')
            i += 1
            _require(i == len(script) or script[i] in ' \t\n;', 'startup_word_concatenation_forbidden')
            words.append(''.join(value))
        else:
            start = i
            while i < len(script) and script[i] not in ' \t\n;':
                _require(re.fullmatch(r'[A-Za-z0-9_./:+,=-]', script[i]) is not None,
                         'startup_nonliteral_shell_word')
                i += 1
            words.append(script[start:i])
        _require(len(words) <= 8 and len(commands) <= 9, 'startup_command_bound')
    if words:
        commands.append(words)
    _require(len(commands) == 9, 'startup_command_count')
    return commands


def validate_substitution(expression):
    """Prove a single sed s-command; never permit addresses/extra commands.

    Patterns and replacements remain sed's own syntax. Escaped delimiter
    handling is byte-for-byte preserved. Only g and positive occurrence-number
    flags are permitted; e/w/r, output files, newlines and trailing programs are
    rejected. Invalid regular expressions will still fail in the bounded replay.
    """
    _require(isinstance(expression, str) and 4 <= len(expression.encode()) <= 512,
             'sed_expression_bound')
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in expression), 'sed_control_character')
    _require(expression[0] == 's' and expression[1] in '/|#@:%!~,_=+', 'sed_substitution_required')
    delimiter = expression[1]
    i = 2
    fields = []
    for unused in range(2):
        start = i
        while i < len(expression):
            if expression[i] == '\\':
                _require(i + 1 < len(expression), 'sed_unfinished_escape')
                i += 2
            elif expression[i] == delimiter:
                fields.append(expression[start:i])
                i += 1
                break
            else:
                i += 1
        else:
            raise ValueError('sed_unterminated_substitution')
    _require(bool(fields[0]), 'sed_previous_pattern_forbidden')
    flags = expression[i:]
    _require(re.fullmatch(r'(?:g|[1-9][0-9]{0,5}g?|g[1-9][0-9]{0,5})?', flags) is not None,
             'sed_unsafe_or_unknown_flags')
    return expression


def _parse_startup(container, expected_startup_sha256, expected_expressions):
    """Internal test seam. Production must call parse_startup(), not this."""
    _require(isinstance(container, dict), 'startup_container_required')
    _require(container.get('command') in (['sh', '-c'], ['/bin/sh', '-c']), 'startup_command_requires_review')
    args = container.get('args')
    _require(isinstance(args, list) and len(args) == 1 and isinstance(args[0], str), 'startup_args_required')
    _require(startup_digest(container) == expected_startup_sha256, 'startup_identity_changed')
    _require(len(expected_expressions) == 7, 'seven_expression_identities_required')
    commands = _literal_commands(args[0])
    _require(commands[0] == ['cp', TEMPLATE, LIVE], 'startup_copy_requires_review')
    _require(commands[-1] == ['exec', 'nginx', '-c', LIVE, '-g', 'daemon off;'],
             'startup_nginx_exec_requires_review')
    for command in commands[1:-1]:
        _require(len(command) == 4 and command[:2] == ['sed', '-i'] and command[-1] == LIVE,
                 'startup_sed_command_requires_review')

    # The production expression hashes came from this exact shlex projection.
    # Do not silently substitute the output of a different shell-word parser.
    lexer = shlex.shlex(args[0], posix=True, punctuation_chars=';&|()<>')
    lexer.whitespace_split = True
    projected = list(lexer)
    _require(len(projected) <= 200, 'startup_projection_bound')
    projected = [word for word in projected if word != ';']
    literal = [word for command in commands for word in command]
    _require(projected == literal, 'startup_projection_literal_mismatch')

    expressions = []
    for command, identity in zip(commands[1:-1], expected_expressions):
        expression = command[2]
        raw = expression.encode()
        _require(len(raw) == identity[0] and hashlib.sha256(raw).hexdigest() == identity[1],
                 'startup_sed_identity_changed')
        expressions.append(validate_substitution(expression))
    return tuple(expressions)


def parse_startup(container):
    """Return exactly seven reviewed literal sed expressions, without execution."""
    return _parse_startup(container, STARTUP_SHA, EXPRESSION_IDENTITIES)



def walk(node):
    yield node
    for child in node['children']:
        for descendant in walk(child):
            yield descendant


def block(text, node):
    return text[node['open']:node['close'] + 1]


def one(nodes, code):
    require(len(nodes) == 1, code)
    return nodes[0]


def auth_location(path, api=False):
    # Same directives as the reviewed k8s/local-pvc nginx delta. Static,
    # path-qualified Gateway URL survives the seven startup substitutions.
    bypass = '\n            if (' + BEARER_VARIABLE + ') { return 204; }' if api else ''
    return '\n        location = ' + path + ' {\n            internal;' + bypass + """
            proxy_pass http://gateway:8001/api/v1/auth/me;
            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_set_header Cookie $http_cookie;
            proxy_set_header X-DeerFlow-Internal-Token "";
            proxy_set_header X-DeerFlow-Owner-User-Id "";
            proxy_set_header Forwarded "";
            proxy_set_header X-Forwarded-Port "";
            proxy_hide_header X-Powered-By;
            proxy_hide_header X-AspNet-Version;
            proxy_hide_header X-AspNetMvc-Version;
            proxy_set_header X-Forwarded-Host $http_host;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Proto $forwarded_proto;
        }
"""



# The production route seals were produced by the original readonly inspector's
# ordered (words, children) projection, NOT the raw source block. Keep these
# three functions identical to that inspector. The offset parser above remains
# separate because insertions must preserve original bytes and quoted spelling.
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def tokenize_config(text):
    # Preserve quoted values, comments and nested blocks without exporting values.
    pattern = r'''\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{};]|(?:\\.|[^\s{};#"'])+'''
    tokens = []
    position = 0
    for match in re.finditer(pattern, text):
        require(match.start() == position, 'CONFIG_TOKEN_GAP')
        token = match.group(0)
        position = match.end()
        if token.isspace() or token.startswith('#'):
            continue
        if token.startswith(('"', "'")):
            token = token[1:-1]
        tokens.append(token)
    require(position == len(text) and len(tokens) <= 50000, 'CONFIG_TOKEN_BOUND')
    return tokens

def parse_config(text):
    tokens = tokenize_config(text)
    position = [0]
    def block(depth):
        require(depth <= 24, 'CONFIG_DEPTH_BOUND')
        nodes, words = [], []
        while position[0] < len(tokens):
            token = tokens[position[0]]
            position[0] += 1
            if token == '}':
                require(not words and depth > 0, 'CONFIG_CLOSE')
                return nodes
            if token == '{':
                require(words, 'CONFIG_BLOCK_HEADER')
                nodes.append((words, block(depth + 1)))
                words = []
            elif token == ';':
                require(words, 'CONFIG_EMPTY_DIRECTIVE')
                nodes.append((words, None))
                words = []
            else:
                words.append(token)
        require(not words and depth == 0, 'CONFIG_UNCLOSED')
        return nodes
    return block(0)


def structured_route_hashes(text):
    """Original inspector projection for SkillHub route identities only."""
    routes = {}
    def visit(nodes):
        for words, children in nodes:
            if words[0] == 'location' and any('skillhub' in word.lower() for word in words):
                require(children is not None, 'LOCATION_SHAPE')
                key = tuple(words)
                require(key not in routes, 'duplicate_route_projection')
                routes[key] = sha(canonical((words, children)))
            if children is not None:
                visit(children)
    visit(parse_config(text))
    require(len(routes) <= 30, 'ROUTE_BOUND')
    return routes

def _transform(raw, expected_route_hashes=None):
    """Internal synthetic-test seam; callers must use plan_production_pair."""
    require(type(raw) is bytes and len(raw) <= 131072 and b'\0' not in raw, 'config_bound')
    text = raw.decode('utf-8')
    require(MARKER not in text and BEARER_VARIABLE not in text
            and all(x not in text for x in (BROWSER_AUTH, API_AUTH, '@skillhub_login')),
            'already_guarded_or_conflicting_auth_names')
    root = parse_blocks(text)
    nodes = list(walk(root))
    require(not any(d[:1] == ['include'] for n in nodes for d in n['directives']), 'includes_require_review')
    http = one([n for n in root['children'] if n['header'] == ['http']], 'one_http_required')
    server = one([n for n in http['children'] if n['header'] == ['server']
                  and any(d[:2] == ['listen', '2026'] for d in n['directives'])], 'one_entry_server_required')
    all_routes = [n for n in nodes if n['header'][:1] == ['location']
                  and any('skillhub' in word.lower() for word in n['header'])]
    require(len(all_routes) == 5 and len({tuple(n['header']) for n in all_routes}) == 5
            and {tuple(n['header']) for n in all_routes} == set(OLD_ROUTE_HASHES)
            and all(n in server['children'] for n in all_routes), 'existing_skillhub_routes_require_review')
    routes = {tuple(n['header']): n for n in all_routes}
    projected_routes = structured_route_hashes(text)
    require(set(projected_routes) == set(routes), 'route_projection_inventory_changed')
    if expected_route_hashes is not None:
        require(set(expected_route_hashes) == set(routes), 'route_inventory_changed')
        for header, digest in expected_route_hashes.items():
            require(projected_routes[header] == digest, 'existing_route_hash_changed')
    assertion = routes[('location', '=', ASSERTION)]
    bootstrap = routes[('location', '=', BOOTSTRAP)]
    sse = routes[('location', '=', SSE)]
    page = routes[('location', '^~', PAGE_PREFIX)]
    require(['internal'] in assertion['directives'], 'assertion_must_remain_internal')
    require([d for d in bootstrap['directives'] if d[:1] == ['auth_request']]
            == [['auth_request', ASSERTION]], 'bootstrap_auth_changed')
    for node in (page, sse):
        require(not node['children'] and not any(d[:1] in (['auth_request'], ['return'], ['error_page'])
                for d in node['directives']), 'existing_page_or_sse_auth_requires_review')
        require([d for d in node['directives'] if d[:1] == ['proxy_pass']]
                == [['proxy_pass', 'http://skillhub-web:80']], 'skillhub_upstream_changed')
        require(['proxy_set_header', 'X-Forwarded-Proto', '$forwarded_proto'] in node['directives'],
                'forwarded_proto_binding_changed')
        for directive in (
            ['proxy_set_header', 'Forwarded', '""'],
            ['proxy_set_header', 'X-Forwarded-Port', '""'],
            ['proxy_set_header', 'X-Forwarded-Host', '$http_host'],
            ['proxy_set_header', 'X-SkillHub-Identity', '""'],
            ['proxy_set_header', 'X-SkillHub-Signature', '""'],
            ['proxy_hide_header', 'X-Powered-By'],
            ['proxy_hide_header', 'X-AspNet-Version'],
            ['proxy_hide_header', 'X-AspNetMvc-Version'],
        ):
            require(directive in node['directives'], 'existing_header_defense_missing')
    require(PPT_VARIABLE in text and 'PPT_AGENT_RETIRED' in text, 'existing_ppt_guard_required')

    # Insert only: all original config bytes, routes, headers and PPT guards
    # remain in their original order. The API prefix clones the existing page
    # body so its header defenses and upstream behavior are preserved exactly.
    mapping = '\n    # BEGIN ' + MARKER + """
    map $http_authorization $skillhub_bearer_request {
        default 0;
        "~*^Bearer[ ]+.+$" 1;
    }
    # END """ + MARKER + '\n'
    internal = '\n        # BEGIN ' + MARKER + auth_location(BROWSER_AUTH) + auth_location(API_AUTH, True)
    internal += """
        location @skillhub_login {
            return 302 /login;
        }
        # END """ + MARKER + '\n        '
    api = ('\n        # BEGIN ' + MARKER + '\n        location ^~ ' + API_PREFIX + ' {\n'
           + '            auth_request ' + API_AUTH + ';'
           + text[page['open'] + 1:page['close']] + '}\n        # END ' + MARKER + '\n        ')
    insertions = [
        (sse['open'] + 1, '\n            auth_request ' + API_AUTH + ';'),
        (page['open'] + 1, '\n            auth_request ' + BROWSER_AUTH + ';\n            error_page 401 = @skillhub_login;'),
        (assertion['start'], internal),
        (page['start'], api),
        # Append AFTER every existing http directive, including map_hash_*.
        (http['close'], mapping),
    ]
    require(len({offset for offset, _ in insertions}) == len(insertions), 'insertion_overlap')
    result = text
    for offset, value in sorted(insertions, reverse=True):
        result = result[:offset] + value + result[offset:]
    # Reverse every insertion at its exact computed offset. This is stronger
    # than searching/replacing marker strings and proves all old bytes survive.
    shift, adjusted = 0, []
    for offset, value in sorted(insertions):
        adjusted.append((offset + shift, value))
        shift += len(value)
    restored = result
    for offset, value in reversed(adjusted):
        require(restored[offset:offset + len(value)] == value, 'insertion_roundtrip_failed')
        restored = restored[:offset] + restored[offset + len(value):]
    require(restored == text, 'unrelated_bytes_changed')
    require(len(result.encode()) <= 131072, 'candidate_config_bound')
    parse_blocks(result)
    return result.encode(), {
        'insertions': len(insertions), 'existing_bytes_preserved': True,
        'bootstrap_unchanged': True, 'ppt_guard_preserved': True,
        'new_locations': 4, 'new_maps': 1, 'existing_auth_locations_changed': 2,
        'raw_configuration_exported': False,
    }


def plan_production_pair(current_template, live, container):
    """Bind CURRENT ConfigMap/live bytes and the exact observed startup.

    Return private candidate bytes and literal replay inputs. Never print or
    publish these raw values. There is deliberately no activation-ready result.
    Native backend rollout/HTTP acceptance and replay/nginx -t remain external
    prerequisites; receipt provenance must be checked by the future controller.
    """
    require(type(current_template) is bytes and sha(current_template) == CURRENT_TEMPLATE_SHA,
            'current_configmap_hash_changed_or_stale_mount')
    require(type(live) is bytes and sha(live) == CURRENT_LIVE_SHA, 'current_live_hash_changed')
    expressions = parse_startup(container)
    require(len(expressions) == 7, 'seven_substitutions_required')
    candidate_template, template_summary = _transform(current_template, OLD_ROUTE_HASHES)
    candidate_live, live_summary = _transform(live, OLD_ROUTE_HASHES)
    return {
        'schema': 'portal-skillhub-nginx-auth-plan.v1',
        'current_template': current_template, 'current_live': live,
        'candidate_template': candidate_template, 'candidate_live': candidate_live,
        'startup_sed_expressions': expressions,
        'summary': {
            'schema': 'portal-skillhub-nginx-auth-plan-summary.v1',
            'before_template_sha256': sha(current_template), 'before_live_sha256': sha(live),
            'candidate_template_sha256': sha(candidate_template), 'candidate_live_sha256': sha(candidate_live),
            'startup_sha256': STARTUP_SHA, 'pure_substitutions': 7,
            'template': template_summary, 'live': live_summary,
            'stale_mounted_template_used': False, 'ready_for_activation': False,
            'native_server_auth_acceptance_required': True,
            'startup_replay_verified': False, 'nginx_syntax_verified': False,
            'production_runtime_accepted': False,
        },
    }


def verify_startup_replay(plan, original_replayed, candidate_replayed):
    """Check supplied bounded sed replay outputs; never execute startup/shell.

    This certifies only byte reproduction. It cannot authorize deployment or
    establish native backend authentication, Nginx syntax, or HTTP acceptance.
    """
    require(type(plan) is dict and plan.get('schema') == 'portal-skillhub-nginx-auth-plan.v1', 'plan_schema')
    expressions = plan.get('startup_sed_expressions')
    require(isinstance(expressions, tuple) and tuple((len(x.encode()), sha(x.encode())) for x in expressions)
            == EXPRESSION_IDENTITIES, 'plan_startup_expressions_changed')
    for expression in expressions:
        validate_substitution(expression)
    template, live = plan['current_template'], plan['current_live']
    require(sha(template) == CURRENT_TEMPLATE_SHA and sha(live) == CURRENT_LIVE_SHA, 'plan_before_identity')
    require(_transform(template, OLD_ROUTE_HASHES)[0] == plan['candidate_template']
            and _transform(live, OLD_ROUTE_HASHES)[0] == plan['candidate_live'], 'candidate_plan_changed')
    require(original_replayed == live, 'original_startup_does_not_reproduce_live')
    require(candidate_replayed == plan['candidate_live'], 'startup_changes_skillhub_or_ppt_guard')
    return {'startup_replay_verified': True, 'startup_shell_executed': False,
            'ready_for_activation': False, 'native_server_auth_acceptance_required': True,
            'nginx_syntax_verified': False, 'production_runtime_accepted': False}

# Fixed safe diagnostic codes only; never export arbitrary ValueError text.
POLICY_ERROR_CODES = frozenset(('CONFIG_BLOCK_HEADER', 'CONFIG_CLOSE', 'CONFIG_DEPTH_BOUND', 'CONFIG_EMPTY_DIRECTIVE', 'CONFIG_TOKEN_BOUND', 'CONFIG_TOKEN_GAP', 'CONFIG_UNCLOSED', 'LOCATION_SHAPE', 'ROUTE_BOUND', 'already_guarded_or_conflicting_auth_names', 'assertion_must_remain_internal', 'bootstrap_auth_changed', 'candidate_config_bound', 'candidate_plan_changed', 'config_bound', 'current_configmap_hash_changed_or_stale_mount', 'current_live_hash_changed', 'duplicate_route_projection', 'existing_header_defense_missing', 'existing_page_or_sse_auth_requires_review', 'existing_ppt_guard_required', 'existing_route_hash_changed', 'existing_skillhub_routes_require_review', 'forwarded_proto_binding_changed', 'includes_require_review', 'incomplete_nginx_config', 'insertion_overlap', 'insertion_roundtrip_failed', 'missing_block_header', 'one_entry_server_required', 'one_http_required', 'original_startup_does_not_reproduce_live', 'plan_before_identity', 'plan_schema', 'plan_startup_expressions_changed', 'route_inventory_changed', 'route_projection_inventory_changed', 'sed_control_character', 'sed_expression_bound', 'sed_previous_pattern_forbidden', 'sed_substitution_required', 'sed_unfinished_escape', 'sed_unsafe_or_unknown_flags', 'sed_unterminated_substitution', 'seven_expression_identities_required', 'seven_substitutions_required', 'skillhub_upstream_changed', 'startup_args_required', 'startup_changes_skillhub_or_ppt_guard', 'startup_command_bound', 'startup_command_count', 'startup_command_requires_review', 'startup_container_required', 'startup_control_character', 'startup_copy_requires_review', 'startup_empty_shell_command', 'startup_identity_changed', 'startup_nginx_exec_requires_review', 'startup_nonliteral_shell_word', 'startup_projection_bound', 'startup_projection_literal_mismatch', 'startup_quoted_word_unsupported', 'startup_sed_command_requires_review', 'startup_sed_identity_changed', 'startup_shell_expansion_forbidden', 'startup_text_bound', 'startup_unclosed_quote', 'startup_word_concatenation_forbidden', 'unbalanced_nginx_block', 'unclosed_nginx_quote', 'unclosed_nginx_variable', 'unrelated_bytes_changed'))
