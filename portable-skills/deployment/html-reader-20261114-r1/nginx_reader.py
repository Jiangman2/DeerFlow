"""Add an authenticated, sandboxed reader for published energy-daily HTML only."""
from __future__ import annotations

from dataclasses import dataclass, field
import re


class Refused(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Refused(code)


@dataclass
class Directive:
    name: str
    args: list[str]
    start: int
    end: int = 0
    children: list = field(default_factory=list)
    parent: object = None


def tokens(source):
    """Read Nginx quotes/comments so regex braces are not configuration blocks."""
    index = 0
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source[index] == '#':
            end = source.find('\n', index)
            index = len(source) if end < 0 else end + 1
            continue
        start = index
        char = source[index]
        if char in '{};':
            yield char, char, start, index + 1
            index += 1
            continue
        value = ''
        if char in ('"', "'"):
            delimiter = char
            index += 1
            while index < len(source) and source[index] != delimiter:
                if source[index] == '\\' and index + 1 < len(source):
                    value += source[index:index + 2]
                    index += 2
                else:
                    value += source[index]
                    index += 1
            need(index < len(source), 'NGINX_UNTERMINATED_QUOTE')
            index += 1
        else:
            while index < len(source) and not source[index].isspace() and source[index] not in '{};#':
                if source[index] == '\\' and index + 1 < len(source):
                    value += source[index:index + 2]
                    index += 2
                else:
                    value += source[index]
                    index += 1
        need(value != '' or char in ('"', "'"), 'NGINX_EMPTY_TOKEN')
        yield 'word', value, start, index


def parse(source):
    need(isinstance(source, str) and 0 < len(source.encode()) <= 2 * 1024 * 1024, 'NGINX_SOURCE_BOUND')
    root = Directive('<root>', [], 0)
    stack, words = [root], []
    for kind, value, start, end in tokens(source):
        if kind == 'word':
            words.append((value, start))
        elif kind in ('{', ';'):
            need(bool(words), 'NGINX_DIRECTIVE_REQUIRED')
            node = Directive(words[0][0], [item[0] for item in words[1:]], words[0][1], end, parent=stack[-1])
            stack[-1].children.append(node)
            words = []
            if kind == '{':
                stack.append(node)
        else:
            need(not words and len(stack) > 1, 'NGINX_UNBALANCED_BLOCK')
            stack.pop().end = end
    need(len(stack) == 1 and not words, 'NGINX_INCOMPLETE_SOURCE')
    return root


def descendants(node):
    for child in node.children:
        yield child
        yield from descendants(child)


def inspect(source):
    tree = parse(source)
    apis = [node for node in descendants(tree) if node.name == 'location' and node.args in (['/api/'], ['^~', '/api/'])]
    need(len(apis) == 1 and apis[0].parent.name == 'server', 'UNIQUE_MAIN_API_LOCATION_REQUIRED')
    api, server = apis[0], apis[0].parent
    passes = [node.args for node in api.children if node.name == 'proxy_pass']
    need(len(passes) == 1 and len(passes[0]) == 1 and re.fullmatch(r'http://(?:[A-Za-z0-9_.:-]+|\$[A-Za-z_][A-Za-z0-9_]*)', passes[0][0]), 'FIXED_GATEWAY_PROXY_REQUIRED')
    headers = {node.args[0].lower(): node.args[1] for node in api.children
               if node.name == 'proxy_set_header' and len(node.args) == 2}
    proto = headers.get('x-forwarded-proto', '$scheme')
    need(proto in ('$scheme', '$forwarded_proto'), 'FORWARDED_PROTO_CONTRACT_UNKNOWN')
    occupied = any(node.name == 'location' and any('/reports/energy-daily' in arg or '@energy_daily_reader_' in arg for arg in node.args)
                   for node in server.children)
    return {'proxy_pass': passes[0][0], 'forwarded_proto': proto,
            'api_location_start': api.start, 'server_start': server.start,
            'report_reader_route_present': occupied,
            'server_listens': [node.args[0] for node in server.children if node.name == 'listen' and node.args]}


def reader_block(proxy_pass, forwarded_proto='$forwarded_proto'):
    need(re.fullmatch(r'http://(?:[A-Za-z0-9_.:-]+|\$[A-Za-z_][A-Za-z0-9_]*)', proxy_pass), 'FIXED_GATEWAY_PROXY_REQUIRED')
    need(forwarded_proto in ('$scheme', '$forwarded_proto'), 'FORWARDED_PROTO_CONTRACT_UNKNOWN')
    # Nginx escapes the rewritten UTF-8 URI exactly once when proxying.
    # Pre-encoded percent signs here would become %25 and break the filename.
    name = '油气与金融市场日报_'
    csp = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
           "style-src 'unsafe-inline'; img-src data:; script-src 'none'; connect-src 'none'; "
           "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
    headers = '\n'.join([
        '    server_tokens off;',
        '    add_header X-Content-Type-Options nosniff always;',
        '    add_header Referrer-Policy no-referrer always;',
        '    add_header Cache-Control "private, no-store" always;',
        '    add_header X-Frame-Options SAMEORIGIN always;',
        '    add_header Content-Security-Policy "' + csp + '" always;',
    ])
    result = '''# BEGIN ENERGY DAILY AUTHENTICATED HTML READER v1
location /reports/energy-daily/ { server_tokens off; return 404; }
location ~ "^/reports/energy-daily/(?<energy_daily_thread>[A-Za-z0-9_-]{1,64})/(?<energy_daily_date>[0-9]{4}-[0-9]{2}-[0-9]{2})\\.html$" {
    limit_except GET { deny all; }
    set $energy_daily_return /reports/energy-daily/$energy_daily_thread/$energy_daily_date.html;
    proxy_http_version 1.1;
    proxy_method GET;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Connection "";
    proxy_set_header Cookie $http_cookie;
    proxy_set_header Authorization "";
    proxy_set_header X-DeerFlow-Internal-Token "";
    proxy_set_header X-DeerFlow-Owner-User-Id "";
    proxy_set_header Forwarded "";
    proxy_set_header X-Forwarded-Port "";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Host $http_host;
    proxy_set_header X-Forwarded-Proto __PROTO__;
    proxy_hide_header X-Powered-By;
    proxy_hide_header X-AspNet-Version;
    proxy_hide_header X-AspNetMvc-Version;
    proxy_hide_header Content-Type;
    proxy_hide_header Content-Disposition;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Download-Options;
    proxy_hide_header Cache-Control;
    proxy_hide_header Referrer-Policy;
    proxy_hide_header X-Content-Type-Options;
    proxy_intercept_errors on;
    error_page 401 = @energy_daily_reader_login;
    error_page 403 = @energy_daily_reader_denied;
    error_page 404 = @energy_daily_reader_missing;
    error_page 500 502 503 504 = @energy_daily_reader_unavailable;
    proxy_connect_timeout 10s;
    proxy_read_timeout 30s;
    add_header Content-Type "text/html; charset=utf-8" always;
    add_header Content-Disposition inline always;
__HEADERS__
    rewrite ^ /api/threads/$energy_daily_thread/artifacts/mnt/user-data/outputs/__NAME__$energy_daily_date.html? break;
    proxy_pass __UPSTREAM__;
}
location @energy_daily_reader_login {
    absolute_redirect off;
    add_header Cache-Control "private, no-store" always;
    return 302 /api/v1/auth/iam/login?next=$energy_daily_return;
}
__ERROR_LOCATIONS__
# END ENERGY DAILY AUTHENTICATED HTML READER v1
'''
    errors = []
    for suffix, code, message in [('denied', 403, '你没有权限查看这份日报。请使用有权访问当前对话的海能账号登录。'),
                                  ('missing', 404, '日报文件尚未发布或已移除。'),
                                  ('unavailable', 503, '暂时无法读取日报，请稍后重试。')]:
        body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>日报阅读</title><body><p>' + message + '</p></body></html>'
        errors.append('location @energy_daily_reader_' + suffix + ' {\n    default_type text/html;\n    charset utf-8;\n' + headers + '\n    return ' + str(code) + ' "' + body.replace('"', '\\"') + '";\n}')
    return result.replace('__PROTO__', forwarded_proto).replace('__UPSTREAM__', proxy_pass).replace('__HEADERS__', headers).replace('__NAME__', name).replace('__ERROR_LOCATIONS__', '\n'.join(errors))


def add_reader(source):
    facts = inspect(source)
    need(not facts['report_reader_route_present'], 'REPORT_READER_ROUTE_ALREADY_OCCUPIED')
    start = source.rfind('\n', 0, facts['api_location_start']) + 1
    indent = source[start:facts['api_location_start']]
    need(not indent.strip(), 'API_LOCATION_LINE_ANCHOR_REQUIRED')
    block = reader_block(facts['proxy_pass'], facts['forwarded_proto'])
    inserted = '\n'.join(indent + line if line else '' for line in block.splitlines()) + '\n\n'
    result = source[:start] + inserted + source[start:]
    need(inspect(result)['report_reader_route_present'], 'REPORT_READER_INSERTION_FAILED')
    return result, facts
