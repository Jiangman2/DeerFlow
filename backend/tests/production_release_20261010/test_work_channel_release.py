"""Regression tests for the exact released Work repair; use synthetic temporary files."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import AsyncMock,patch

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
from app.channels.runtime_config_store import ChannelRuntimeConfigStore,ChannelRuntimeConfigConflict
from app.gateway.routers import channel_connections as route
from fastapi import HTTPException

DOMAIN='https://open.w.cnooc.com.cn'

class NativeStore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'runtime.json'
        self.env=patch.dict(os.environ,{'DEERFLOW_FEISHU_DOMAIN':DOMAIN});self.env.start()
        self.store=ChannelRuntimeConfigStore(self.path)
    def tearDown(self):self.env.stop();self.tmp.cleanup()
    def test_latest_domain_credentials_and_other_provider(self):
        stale=ChannelRuntimeConfigStore(self.path)
        self.store.set_provider_config('slack',{'enabled':True})
        self.store.set_provider_config('feishu',{'app_id':'synthetic','app_secret':'new','domain':'https://open.feishu.cn'})
        actual=stale.get_provider_config('feishu')
        self.assertEqual(actual['domain'],DOMAIN);self.assertEqual(actual['app_secret'],'new')
        self.assertIn('slack',stale.load_all())
    def test_cas_conflict(self):
        self.store.set_provider_config('feishu',{'app_secret':'one'})
        old=self.store.get_provider_config('feishu')
        self.store.set_provider_config('feishu',{'app_secret':'two'})
        with self.assertRaises(ChannelRuntimeConfigConflict):self.store.commit_provider_config('feishu',old,old)
        self.assertEqual(self.store.get_provider_config('feishu')['app_secret'],'two')
    def test_corrupt_fail_closed(self):
        self.path.write_bytes(b'broken')
        with self.assertRaises(ValueError):self.store.set_provider_config('feishu',{})
        self.assertEqual(self.path.read_bytes(),b'broken')
    def test_fresh_disconnect(self):
        stale=ChannelRuntimeConfigStore(self.path)
        self.store.set_provider_disconnected('feishu')
        self.assertFalse(stale.get_provider_config('feishu')['enabled'])
        self.store.remove_provider_config('feishu');self.assertIsNone(stale.get_provider_config('feishu'))
    def test_default_not_forced_without_operator_setting(self):
        with patch.dict(os.environ,{},clear=True):
            self.store.set_provider_config('feishu',{'domain':'https://open.feishu.cn'})
            self.assertEqual(self.store.get_provider_config('feishu')['domain'],'https://open.feishu.cn')
    def test_lock_symlink_rejected(self):
        other=Path(self.tmp.name)/'other';other.write_text('untouched')
        self.path.with_name('runtime.json.lock').symlink_to(other)
        with self.assertRaises(OSError):self.store.set_provider_config('feishu',{})
        self.assertEqual(other.read_text(),'untouched')
    def test_cross_process_tmpfs_writers(self):
        # Children import only the small store module, not four copies of the
        # Gateway/router dependency graph inside the bounded 2GiB container.
        code="import sys;sys.path.insert(0,sys.argv[3]);from app.channels.runtime_config_store import ChannelRuntimeConfigStore;ChannelRuntimeConfigStore(sys.argv[1]).set_provider_config(sys.argv[2],{'enabled':True})"
        jobs=[]
        try:
            for i in range(4):
                jobs.append(subprocess.Popen([sys.executable,'-I','-B','-c',code,str(self.path),'synthetic'+str(i),str(BACKEND)],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
            for job in jobs:self.assertEqual(job.wait(timeout=30),0)
        finally:
            for job in jobs:
                if job.poll() is None:
                    job.terminate()
                    try:job.wait(timeout=3)
                    except subprocess.TimeoutExpired:job.kill();job.wait(timeout=3)
        self.assertEqual(len(self.store.load_all()),4)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
    def test_failed_write_retains_previous_bytes(self):
        self.store.set_provider_config('feishu',{'app_secret':'old'})
        before=self.path.read_bytes()
        with patch('os.replace',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):self.store.set_provider_config('feishu',{'app_secret':'new'})
        self.assertEqual(self.path.read_bytes(),before)

class NativeRoute(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.stack=ExitStack()
        self.stack.enter_context(patch.dict(os.environ,{'DEERFLOW_FEISHU_DOMAIN':DOMAIN}))
        self.store=ChannelRuntimeConfigStore(Path(self.tmp.name)/'runtime.json')
        self.store.set_provider_config('feishu',{'enabled':True,'app_id':'synthetic-id','app_secret':'latest-secret'})
        self.config=types.SimpleNamespace(enabled=True,feishu=types.SimpleNamespace(enabled=True))
        self.request=types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))
        self.restart=AsyncMock(return_value=True);self.admin=AsyncMock()
        for name,value in {'require_admin_user':self.admin,
            '_get_channel_connections_config':AsyncMock(return_value=self.config),
            '_get_runtime_config_store':AsyncMock(return_value=self.store),
            '_get_channels_config':AsyncMock(return_value={'feishu':{'app_id':'synthetic-id','app_secret':'stale-secret'}}),
            '_restart_runtime_channel_if_available':self.restart,
            '_provider_response':lambda *args:'synthetic-ok'}.items():
            self.stack.enter_context(patch.object(route,name,value))
        self.body=route.ChannelRuntimeConfigRequest(values={'app_id':'synthetic-id','app_secret':'********'})
    async def asyncTearDown(self):self.stack.close();self.tmp.cleanup()
    async def invoke(self):return await route.configure_channel_provider_runtime('feishu',self.body,self.request)
    async def test_real_route_masked_secret_domain(self):
        self.assertEqual(await self.invoke(),'synthetic-ok')
        result=self.store.get_provider_config('feishu')
        self.assertEqual(result['app_secret'],'latest-secret');self.assertEqual(result['domain'],DOMAIN)
        self.admin.assert_awaited_once();self.restart.assert_awaited_once()
    async def test_conflict_no_restart(self):
        with patch.object(self.store,'commit_provider_config',side_effect=ChannelRuntimeConfigConflict()):
            with self.assertRaises(HTTPException) as caught:await self.invoke()
        self.assertEqual(caught.exception.status_code,409);self.restart.assert_not_awaited()
    async def test_admin_rejection_no_write(self):
        before=self.store._path.read_bytes();self.admin.side_effect=HTTPException(403,'synthetic')
        with self.assertRaises(HTTPException) as caught:await self.invoke()
        self.assertEqual(caught.exception.status_code,403)
        self.assertEqual(self.store._path.read_bytes(),before);self.restart.assert_not_awaited()
    async def test_restart_failure_saved_not_rolled_back(self):
        self.restart.return_value=False
        with self.assertRaises(HTTPException) as caught:await self.invoke()
        self.assertEqual(caught.exception.status_code,503)
        self.assertEqual(self.store.get_provider_config('feishu')['domain'],DOMAIN)
    async def test_disabled_gate(self):
        self.config.enabled=False;before=self.store._path.read_bytes()
        with self.assertRaises(HTTPException) as caught:await self.invoke()
        self.assertEqual(caught.exception.status_code,400)
        self.assertEqual(self.store._path.read_bytes(),before);self.restart.assert_not_awaited()

class ProxyRecipe(unittest.TestCase):
    def test_direct_selection_without_network(self):
        import requests
        from requests.utils import get_environ_proxies
        env={'HTTP_PROXY':'http://127.0.0.1:9','HTTPS_PROXY':'http://127.0.0.1:9',
             'http_proxy':'http://127.0.0.1:9','https_proxy':'http://127.0.0.1:9',
             'NO_PROXY':'localhost,.svc,open.w.cnooc.com.cn,lark-frontier.w.cnooc.com.cn',
             'no_proxy':'localhost,.svc,open.w.cnooc.com.cn,lark-frontier.w.cnooc.com.cn'}
        with patch.dict(os.environ,env,clear=True):
            for host in ('open.w.cnooc.com.cn','lark-frontier.w.cnooc.com.cn'):
                self.assertEqual(get_environ_proxies('https://'+host),{})
            self.assertIn('https',get_environ_proxies('https://unrelated.invalid'))
            from websockets.uri import parse_uri
            from websockets.proxy import get_proxy
            self.assertIsNone(get_proxy(parse_uri('wss://lark-frontier.w.cnooc.com.cn/')))

if __name__=='__main__':
    unittest.main(verbosity=2)
