import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('reader_test_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


reader, preflight = load('nginx_reader'), load('preflight')
CONF = '''events { worker_connections 128; }
http {
 upstream gateway_upstream { server gateway:8001; }
 server {
  listen 2026;
  location /api/ {
   proxy_pass http://gateway_upstream;
   proxy_set_header Connection "";
   proxy_set_header X-Forwarded-Proto $scheme;
  }
  location / { return 404; }
 }
}
'''


class ReaderTests(unittest.TestCase):
    def test_quotes_comments_and_empty_header_values_are_preserved(self):
        source = CONF.replace('location / {', '# } ignored\n  location ~ "^/a/[0-9]{1,4}$" { return 404; }\n  location / {')
        patched, facts = reader.add_reader(source)
        self.assertEqual(facts['proxy_pass'], 'http://gateway_upstream')
        self.assertTrue(reader.inspect(patched)['report_reader_route_present'])
        start = patched.index('  # BEGIN ENERGY DAILY AUTHENTICATED HTML READER v1')
        end = patched.index('  location /api/', start)
        self.assertEqual(patched[:start] + patched[end:], source)

    def test_existing_repository_config_can_be_patched(self):
        source = (HERE.parents[2] / 'docker/nginx/nginx.conf').read_text(encoding='utf-8')
        patched, facts = reader.add_reader(source)
        self.assertEqual(facts['forwarded_proto'], '$forwarded_proto')
        self.assertTrue(reader.inspect(patched)['report_reader_route_present'])

    def test_ambiguous_api_locations_are_rejected(self):
        source = CONF.replace('  location / {', '  location /api/ { proxy_pass http://other; }\n  location / {')
        with self.assertRaisesRegex(reader.Refused, 'UNIQUE_MAIN_API_LOCATION_REQUIRED'):
            reader.add_reader(source)

    def test_proxy_with_credentials_or_arbitrary_uri_is_rejected(self):
        for value in ('http://user:password@gateway', 'http://gateway/path', 'https://outside.invalid'):
            with self.subTest(value=value), self.assertRaises(reader.Refused):
                reader.add_reader(CONF.replace('http://gateway_upstream', value))

    def test_existing_report_route_is_not_overwritten(self):
        patched, _ = reader.add_reader(CONF)
        with self.assertRaisesRegex(reader.Refused, 'REPORT_READER_ROUTE_ALREADY_OCCUPIED'):
            reader.add_reader(patched)

    def test_unbalanced_or_unclosed_configuration_is_rejected(self):
        for value in (CONF[:-2], 'http { server { proxy_set_header Host "unterminated; } }'):
            with self.subTest(value=value), self.assertRaises(reader.Refused):
                reader.parse(value)

    def test_preflight_disallows_mutations_and_secret_reads_before_launch(self):
        inspector = preflight.Inspector()
        with patch.object(preflight.subprocess, 'run') as run:
            for args in (['get', 'secrets', '-o', 'json'], ['patch', 'deployment', 'nginx'], ['get', 'configmap', 'unrelated', '-o', 'json']):
                with self.assertRaises(preflight.Refused):
                    inspector.query(args)
        run.assert_not_called()

    def test_preflight_returns_contract_and_hashes_without_source_body(self):
        def deployment(name):
            spec = {'replicas': 1, 'template': {'spec': {'containers': [{'name': name, 'image': 'fixture'}],
                                                       'volumes': []}}}
            if name == 'nginx':
                spec['template']['spec']['volumes'] = [{'name': 'config', 'configMap': {'name': 'nginx-config'}}]
                spec['template']['spec']['containers'][0]['volumeMounts'] = [{'name': 'config', 'mountPath': '/etc/nginx/nginx.conf', 'subPath': 'nginx.conf'}]
            return {'metadata': {'name': name, 'uid': name + '-uid', 'generation': 1}, 'spec': spec}
        listings = {'items': [deployment(name) for name in ('nginx', 'gateway', 'frontend')]}
        source = CONF + '\n# SECRET_FIXTURE_SHOULD_NOT_APPEAR\n'
        cm = {'metadata': {'uid': 'cm-uid', 'resourceVersion': '1'}, 'data': {'nginx.conf': source}}
        inspector = preflight.Inspector()
        with patch.object(inspector, 'query', side_effect=[listings, cm, copy.deepcopy(listings)]):
            result = inspector.run(reader)
        self.assertTrue(result['stable_spec_readback'])
        self.assertEqual(result['nginx_configmaps'][0]['files'][0]['reader_contract']['proxy_pass'], 'http://gateway_upstream')
        self.assertNotIn('SECRET_FIXTURE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
