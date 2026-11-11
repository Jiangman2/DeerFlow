"""Linux-only real subprocess/private-copy integration, with synthetic HTTP only.

Run inside the cached test image with --network=none. The bootstrap additionally
refuses every real socket connection, so this test cannot contact the provider.
"""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import fresh_target_probe as probe


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL = ROOT / '.codex-energy-daily-chatfix-20261110/skills/public/energy-market-daily'
KEY = 'synthetic-linux-subprocess-credential'


BOOTSTRAP = r'''
import io,json,socket,sys
import requests
def no_socket(*args,**kwargs):
 raise AssertionError('REAL_NETWORK_FORBIDDEN')
socket.socket.connect=no_socket
socket.socket.connect_ex=no_socket
socket.create_connection=no_socket
socket.getaddrinfo=no_socket
def synthetic_send(adapter,request,**kwargs):
 from urllib.parse import parse_qs,urlsplit
 import fetch_market_history as original
 parts=urlsplit(request.url)
 assert parts._replace(query='').geturl()=='http://ip91.jkx.com.cn:6688/jkx_eecc.aspx'
 assert kwargs['proxies']=={'http':'http://10.68.211.1:8080'}
 assert kwargs['timeout']==30
 values=parse_qs(parts.query)
 assert set(values)=={'p','pdate'}
 assert values['p']==['synthetic-linux-subprocess-credential']
 assert values['pdate']==[sys.argv[2]]
 # Exercise the actual installed audit hook without opening a socket.
 sys.audit('socket.connect',None,('10.68.211.1',8080))
 data=[]
 for code in list(original.SPECS)+original.FREIGHT:
  unit=original.SPECS[code][2] if code in original.SPECS else 'synthetic-unit'
  data.append({'code_name':code,'value_date':values['pdate'][0],
               'close':'123.45','unit':unit,'item':'synthetic-item'})
 response=requests.Response()
 response.status_code=200
 response.encoding='utf-8'
 response.request=request
 response.url=request.url
 response.raw=io.BytesIO(json.dumps({'errcode':0,'data':data},ensure_ascii=False).encode('utf-8'))
 return response
requests.adapters.HTTPAdapter.send=synthetic_send
'''


def tree_hashes(folder):
    return {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()}


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux private-copy subprocess integration')
class LinuxFreshTargetIntegrationTests(unittest.TestCase):
    def test_real_private_copy_subprocess_and_summary_are_accepted_and_cleaned(self):
        spec = importlib.util.spec_from_file_location('fresh_linux_collector', HERE / 'energy_market_collector.py')
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        collector.SKILL = SKILL
        before = tree_hashes(SKILL)
        day = (dt.datetime.now(collector.TZ).date() - dt.timedelta(days=1)).isoformat()
        sealed_hash = hashlib.sha256((HERE / 'energy_market_collector.py').read_bytes()).hexdigest()
        real_popen = subprocess.Popen
        children = []
        captures = []

        def launch(args, **kwargs):
            self.assertEqual(args[:4], [sys.executable, '-I', '-B', '-c'])
            self.assertEqual(args[-2:], [day, '2'])
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(kwargs['env']['ENERGY_MARKET_HTTP_PROXY'], probe.PROXY)
            self.assertEqual(kwargs['env']['JKX_API_PASSWORD'], KEY)
            self.assertFalse({'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                              'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'} & set(kwargs['env']))
            args = list(args)
            args[4] = BOOTSTRAP + args[4]
            child = real_popen(args, **kwargs)
            children.append(child)
            captures.append((Path(args[5]).parent.parent, kwargs['stdout']))
            return child

        with tempfile.TemporaryDirectory(prefix='energy-fresh-integration-') as temporary:
            collector.PRIVATE = Path(temporary) / 'private'
            collector.PRIVATE.mkdir(mode=0o700)
            entry = collector.load_entry()
            self.assertEqual(len(entry.original_files()), 158)
            checked = []
            read_summaries = []
            real_verify = entry.verify_tree
            real_read = entry.read

            def verify(folder, expected, working):
                checked.append((Path(folder), working))
                return real_verify(folder, expected, working)

            def read(path, *args):
                raw = real_read(path, *args)
                if Path(path).name == 'fresh-observation.json':
                    read_summaries.append((Path(path), json.loads(raw)))
                return raw

            result = {'status': 'INCOMPLETE'}
            with mock.patch.object(probe, 'SKILL', SKILL), \
                 mock.patch.object(probe, 'verified_code', return_value=collector) as verified, \
                 mock.patch.object(collector, 'credential', return_value={
                     'JKX_API_ENDPOINT': 'http://ip91.jkx.com.cn:6688/jkx_eecc.aspx',
                     'JKX_API_PASSWORD': KEY}), \
                 mock.patch.object(collector, 'load_entry', return_value=entry), \
                 mock.patch.object(entry, 'verify_tree', side_effect=verify), \
                 mock.patch.object(entry, 'read', side_effect=read), \
                 mock.patch.object(probe.subprocess, 'Popen', side_effect=launch):
                probe.probe(sealed_hash, day, result)
            verified.assert_called_once_with(sealed_hash)
            self.assertEqual(len(children), 1)
            self.assertNotEqual(children[0].pid, os.getpid())
            self.assertEqual(children[0].returncode, 0)
            self.assertEqual(len(read_summaries), 1)
            summary_path, summary = read_summaries[0]
            self.assertEqual(summary_path, captures[0][0] / 'fresh-observation.json')
            self.assertTrue(summary['target_response_fresh'])
            self.assertEqual(summary['http_requests'], 1)
            self.assertEqual(summary['http_statuses'], [200])
            self.assertEqual(len(summary['quotes']), 14)
            self.assertEqual(result['status'], 'FRESH_TARGET_RESPONSE_VALIDATED')
            self.assertTrue(result['target_received_via_proxy'])
            self.assertTrue(result['private_target_cache_absent_before_fetch'])
            self.assertEqual(result['proxy_connections'], 1)
            self.assertEqual(result['quote_counts'], {'available': 14, 'missing': 0, 'non_trading': 0})
            self.assertTrue(result['all_required_quotes_available'])
            self.assertTrue(result['temporary_copy_cleaned'])
            self.assertFalse(list(collector.PRIVATE.iterdir()))
            self.assertFalse(summary_path.exists())
            working_checks = [(folder, flag) for folder, flag in checked if folder.name == 'engine']
            self.assertEqual([flag for _, flag in working_checks], [False, True])
            self.assertEqual(tree_hashes(SKILL), before)
            self.assertNotIn(KEY, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
