"""Read only the current Nginx configuration contract; never export its body."""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import types

READER_SOURCE = '"""Add an authenticated, sandboxed reader for published energy-daily HTML only."""\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass, field\nimport re\nfrom urllib.parse import quote\n\n\nclass Refused(ValueError):\n    pass\n\n\ndef need(condition, code):\n    if not condition:\n        raise Refused(code)\n\n\n@dataclass\nclass Directive:\n    name: str\n    args: list[str]\n    start: int\n    end: int = 0\n    children: list = field(default_factory=list)\n    parent: object = None\n\n\ndef tokens(source):\n    """Read Nginx quotes/comments so regex braces are not configuration blocks."""\n    index = 0\n    while index < len(source):\n        if source[index].isspace():\n            index += 1\n            continue\n        if source[index] == \'#\':\n            end = source.find(\'\\n\', index)\n            index = len(source) if end < 0 else end + 1\n            continue\n        start = index\n        char = source[index]\n        if char in \'{};\':\n            yield char, char, start, index + 1\n            index += 1\n            continue\n        value = \'\'\n        if char in (\'"\', "\'"):\n            delimiter = char\n            index += 1\n            while index < len(source) and source[index] != delimiter:\n                if source[index] == \'\\\\\' and index + 1 < len(source):\n                    value += source[index:index + 2]\n                    index += 2\n                else:\n                    value += source[index]\n                    index += 1\n            need(index < len(source), \'NGINX_UNTERMINATED_QUOTE\')\n            index += 1\n        else:\n            while index < len(source) and not source[index].isspace() and source[index] not in \'{};#\':\n                if source[index] == \'\\\\\' and index + 1 < len(source):\n                    value += source[index:index + 2]\n                    index += 2\n                else:\n                    value += source[index]\n                    index += 1\n        need(value != \'\' or char in (\'"\', "\'"), \'NGINX_EMPTY_TOKEN\')\n        yield \'word\', value, start, index\n\n\ndef parse(source):\n    need(isinstance(source, str) and 0 < len(source.encode()) <= 2 * 1024 * 1024, \'NGINX_SOURCE_BOUND\')\n    root = Directive(\'<root>\', [], 0)\n    stack, words = [root], []\n    for kind, value, start, end in tokens(source):\n        if kind == \'word\':\n            words.append((value, start))\n        elif kind in (\'{\', \';\'):\n            need(bool(words), \'NGINX_DIRECTIVE_REQUIRED\')\n            node = Directive(words[0][0], [item[0] for item in words[1:]], words[0][1], end, parent=stack[-1])\n            stack[-1].children.append(node)\n            words = []\n            if kind == \'{\':\n                stack.append(node)\n        else:\n            need(not words and len(stack) > 1, \'NGINX_UNBALANCED_BLOCK\')\n            stack.pop().end = end\n    need(len(stack) == 1 and not words, \'NGINX_INCOMPLETE_SOURCE\')\n    return root\n\n\ndef descendants(node):\n    for child in node.children:\n        yield child\n        yield from descendants(child)\n\n\ndef inspect(source):\n    tree = parse(source)\n    apis = [node for node in descendants(tree) if node.name == \'location\' and node.args in ([\'/api/\'], [\'^~\', \'/api/\'])]\n    need(len(apis) == 1 and apis[0].parent.name == \'server\', \'UNIQUE_MAIN_API_LOCATION_REQUIRED\')\n    api, server = apis[0], apis[0].parent\n    passes = [node.args for node in api.children if node.name == \'proxy_pass\']\n    need(len(passes) == 1 and len(passes[0]) == 1 and re.fullmatch(r\'http://(?:[A-Za-z0-9_.:-]+|\\$[A-Za-z_][A-Za-z0-9_]*)\', passes[0][0]), \'FIXED_GATEWAY_PROXY_REQUIRED\')\n    headers = {node.args[0].lower(): node.args[1] for node in api.children\n               if node.name == \'proxy_set_header\' and len(node.args) == 2}\n    proto = headers.get(\'x-forwarded-proto\', \'$scheme\')\n    need(proto in (\'$scheme\', \'$forwarded_proto\'), \'FORWARDED_PROTO_CONTRACT_UNKNOWN\')\n    occupied = any(node.name == \'location\' and any(\'/reports/energy-daily\' in arg or \'@energy_daily_reader_\' in arg for arg in node.args)\n                   for node in server.children)\n    return {\'proxy_pass\': passes[0][0], \'forwarded_proto\': proto,\n            \'api_location_start\': api.start, \'server_start\': server.start,\n            \'report_reader_route_present\': occupied,\n            \'server_listens\': [node.args[0] for node in server.children if node.name == \'listen\' and node.args]}\n\n\ndef reader_block(proxy_pass, forwarded_proto=\'$forwarded_proto\'):\n    need(re.fullmatch(r\'http://(?:[A-Za-z0-9_.:-]+|\\$[A-Za-z_][A-Za-z0-9_]*)\', proxy_pass), \'FIXED_GATEWAY_PROXY_REQUIRED\')\n    need(forwarded_proto in (\'$scheme\', \'$forwarded_proto\'), \'FORWARDED_PROTO_CONTRACT_UNKNOWN\')\n    name = quote(\'油气与金融市场日报_\', safe=\'\')\n    csp = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src \'none\'; "\n           "style-src \'unsafe-inline\'; img-src data:; script-src \'none\'; connect-src \'none\'; "\n           "object-src \'none\'; base-uri \'none\'; form-action \'none\'; frame-ancestors \'self\'")\n    headers = \'\\n\'.join([\n        \'    server_tokens off;\',\n        \'    add_header X-Content-Type-Options nosniff always;\',\n        \'    add_header Referrer-Policy no-referrer always;\',\n        \'    add_header Cache-Control "private, no-store" always;\',\n        \'    add_header X-Frame-Options SAMEORIGIN always;\',\n        \'    add_header Content-Security-Policy "\' + csp + \'" always;\',\n    ])\n    result = \'\'\'# BEGIN ENERGY DAILY AUTHENTICATED HTML READER v1\nlocation /reports/energy-daily/ { server_tokens off; return 404; }\nlocation ~ "^/reports/energy-daily/(?<energy_daily_thread>[A-Za-z0-9_-]{1,64})/(?<energy_daily_date>[0-9]{4}-[0-9]{2}-[0-9]{2})\\\\.html$" {\n    limit_except GET { deny all; }\n    set $energy_daily_return /reports/energy-daily/$energy_daily_thread/$energy_daily_date.html;\n    proxy_http_version 1.1;\n    proxy_pass_request_body off;\n    proxy_set_header Content-Length "";\n    proxy_set_header Connection "";\n    proxy_set_header Cookie $http_cookie;\n    proxy_set_header Authorization "";\n    proxy_set_header X-DeerFlow-Internal-Token "";\n    proxy_set_header X-DeerFlow-Owner-User-Id "";\n    proxy_set_header Forwarded "";\n    proxy_set_header X-Forwarded-Port "";\n    proxy_set_header Host $http_host;\n    proxy_set_header X-Forwarded-Host $http_host;\n    proxy_set_header X-Forwarded-Proto __PROTO__;\n    proxy_hide_header X-Powered-By;\n    proxy_hide_header X-AspNet-Version;\n    proxy_hide_header X-AspNetMvc-Version;\n    proxy_hide_header Content-Type;\n    proxy_hide_header Content-Disposition;\n    proxy_hide_header Content-Security-Policy;\n    proxy_hide_header X-Download-Options;\n    proxy_hide_header Cache-Control;\n    proxy_hide_header Referrer-Policy;\n    proxy_hide_header X-Content-Type-Options;\n    proxy_intercept_errors on;\n    error_page 401 = @energy_daily_reader_login;\n    error_page 403 = @energy_daily_reader_denied;\n    error_page 404 = @energy_daily_reader_missing;\n    error_page 500 502 503 504 = @energy_daily_reader_unavailable;\n    proxy_connect_timeout 10s;\n    proxy_read_timeout 30s;\n    add_header Content-Type "text/html; charset=utf-8" always;\n    add_header Content-Disposition inline always;\n__HEADERS__\n    rewrite ^ /api/threads/$energy_daily_thread/artifacts/mnt/user-data/outputs/__NAME__$energy_daily_date.html? break;\n    proxy_pass __UPSTREAM__;\n}\nlocation @energy_daily_reader_login {\n    absolute_redirect off;\n    add_header Cache-Control "private, no-store" always;\n    return 302 /api/v1/auth/iam/login?next=$energy_daily_return;\n}\n__ERROR_LOCATIONS__\n# END ENERGY DAILY AUTHENTICATED HTML READER v1\n\'\'\'\n    errors = []\n    for suffix, code, message in [(\'denied\', 403, \'你没有权限查看这份日报。请使用有权访问当前对话的海能账号登录。\'),\n                                  (\'missing\', 404, \'日报文件尚未发布或已移除。\'),\n                                  (\'unavailable\', 503, \'暂时无法读取日报，请稍后重试。\')]:\n        body = \'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>日报阅读</title><body><p>\' + message + \'</p></body></html>\'\n        errors.append(\'location @energy_daily_reader_\' + suffix + \' {\\n    default_type text/html;\\n    charset utf-8;\\n\' + headers + \'\\n    return \' + str(code) + \' "\' + body.replace(\'"\', \'\\\\"\') + \'";\\n}\')\n    return result.replace(\'__PROTO__\', forwarded_proto).replace(\'__UPSTREAM__\', proxy_pass).replace(\'__HEADERS__\', headers).replace(\'__NAME__\', name).replace(\'__ERROR_LOCATIONS__\', \'\\n\'.join(errors))\n\n\ndef add_reader(source):\n    facts = inspect(source)\n    need(not facts[\'report_reader_route_present\'], \'REPORT_READER_ROUTE_ALREADY_OCCUPIED\')\n    start = source.rfind(\'\\n\', 0, facts[\'api_location_start\']) + 1\n    indent = source[start:facts[\'api_location_start\']]\n    need(not indent.strip(), \'API_LOCATION_LINE_ANCHOR_REQUIRED\')\n    block = reader_block(facts[\'proxy_pass\'], facts[\'forwarded_proto\'])\n    inserted = \'\\n\'.join(indent + line if line else \'\' for line in block.splitlines()) + \'\\n\\n\'\n    result = source[:start] + inserted + source[start:]\n    need(inspect(result)[\'report_reader_route_present\'], \'REPORT_READER_INSERTION_FAILED\')\n    return result, facts\n'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class Refused(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Refused(code)


class Inspector:
    def __init__(self):
        self.configmaps = set()
        self.queries = 0

    def query(self, args):
        allowed = args == ['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json']
        if len(args) == 5 and args[:2] == ['get', 'configmap'] and args[2] in self.configmaps and args[3:] == ['-o', 'json']:
            allowed = True
        need(allowed, 'FIXED_READONLY_COMMAND_REQUIRED')
        self.queries += 1
        result = subprocess.run(KUBE + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=25, check=False, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'})
        need(result.returncode == 0, 'READONLY_KUBECTL_FAILED')
        need(len(result.stdout) <= 4194304 and len(result.stderr) <= 65536, 'READONLY_OUTPUT_BOUND')
        return json.loads(result.stdout)

    def run(self, reader):
        rows = self.query(['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json'])['items']
        by_name = {row['metadata']['name']: row for row in rows}
        need(set(by_name) == {'nginx', 'gateway', 'frontend'}, 'DEPLOYMENT_SET_REQUIRED')
        summaries = {}
        for name, row in by_name.items():
            summaries[name] = {'uid': row['metadata']['uid'], 'generation': row['metadata']['generation'],
                               'spec_sha256': digest(canonical(row['spec'])),
                               'images': {c['name']: c['image'] for c in row['spec']['template']['spec']['containers']},
                               'desired': row['spec'].get('replicas', 1),
                               'updated': row.get('status', {}).get('updatedReplicas', 0),
                               'ready': row.get('status', {}).get('readyReplicas', 0),
                               'available': row.get('status', {}).get('availableReplicas', 0)}
        nginx = by_name['nginx']
        volumes = nginx['spec']['template']['spec'].get('volumes', [])
        sources = {v['name']: v['configMap']['name'] for v in volumes if isinstance(v.get('configMap'), dict)}
        need(0 < len(sources) <= 8, 'NGINX_CONFIGMAP_MOUNTS_REQUIRED')
        self.configmaps = set(sources.values())
        need(all(re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', value) for value in self.configmaps), 'CONFIGMAP_NAME_INVALID')
        configs = []
        for name in sorted(self.configmaps):
            cm = self.query(['get', 'configmap', name, '-o', 'json'])
            data = cm.get('data', {})
            need(isinstance(data, dict) and len(data) <= 32, 'CONFIGMAP_DATA_BOUND')
            entry = {'name': name, 'uid': cm['metadata']['uid'], 'resource_version': cm['metadata']['resourceVersion'],
                     'immutable': cm.get('immutable', False), 'data_sha256': digest(canonical(data)), 'files': []}
            for key, source in sorted(data.items()):
                need(isinstance(source, str) and len(source.encode()) <= 2097152, 'CONFIGURATION_SOURCE_BOUND')
                item = {'key': key, 'bytes': len(source.encode()), 'sha256': digest(source.encode())}
                if 'nginx' in key or key.endswith('.conf'):
                    try:
                        item['reader_contract'] = reader.inspect(source)
                    except Exception as exc:
                        item['analysis_error_class'] = type(exc).__name__
                        if isinstance(exc, reader.Refused):
                            item['analysis_error_code'] = str(exc)
                entry['files'].append(item)
            configs.append(entry)
        mounts = [{'container': c['name'], 'volume': m['name'], 'mount_path': m['mountPath'],
                   'subpath': m.get('subPath'), 'read_only': m.get('readOnly', False),
                   'configmap': sources[m['name']]}
                  for c in nginx['spec']['template']['spec']['containers']
                  for m in c.get('volumeMounts', []) if m['name'] in sources]
        fresh = self.query(['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json'])['items']
        stable = {r['metadata']['name']: digest(canonical(r['spec'])) for r in fresh} == {n: s['spec_sha256'] for n, s in summaries.items()}
        return {'deployments': summaries, 'nginx_configmaps': configs, 'nginx_config_mounts': mounts,
                'stable_spec_readback': stable,
                'status': 'NGINX_READER_PREFLIGHT_OBSERVED_NO_CHANGE' if stable else 'DEPLOYMENT_SPEC_CHANGED_DURING_READ'}


def main():
    out = {'schema': 'energy-html-reader-preflight.v1', 'status': 'INCOMPLETE', 'workload_modified': False,
           'persistent_files_modified': False, 'credentials_exported': False, 'secret_api_reads': 0,
           'provider_requests': 0, 'work_messages_sent': 0, 'configuration_body_exported': False}
    inspector = None
    try:
        need(sys.argv[1:] == ['--inspect-readonly'], 'READONLY_FLAG_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        need(bool(READER_SOURCE), 'INSPECTOR_NOT_SEALED')
        reader = types.ModuleType('_energy_nginx_reader')
        sys.modules[reader.__name__] = reader
        exec(compile(READER_SOURCE, '<sealed-nginx-contract>', 'exec'), reader.__dict__)
        inspector = Inspector()
        out.update(inspector.run(reader))
    except Exception as exc:
        out.update(error_class=type(exc).__name__)
        if isinstance(exc, Refused):
            out['error_code'] = str(exc)
    out.update(queries=inspector.queries if inspector else 0, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
