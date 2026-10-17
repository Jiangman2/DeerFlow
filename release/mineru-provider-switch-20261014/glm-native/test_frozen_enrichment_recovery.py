"""Qualification against the actual immutable delivery, not a regenerated helper."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import unittest

import diagnose_enrichment_preload as diagnostic


class FrozenDeliveryTests(unittest.TestCase):
    def test_exact_previously_delivered_bootstrap_is_inert_and_read_wrapper_compatible(self):
        source = Path(os.environ['FROZEN_ENRICHMENT_PRELOAD'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), diagnostic.TOOL_SHA)
        code = r'''
import hashlib, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import patch
import diagnose_enrichment_preload as d
import resume_enrichment_preload as recovery
raw = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(raw).hexdigest() == d.TOOL_SHA
with tempfile.TemporaryDirectory(dir='/root') as folder:
    tool = Path(folder)/'preload.py'
    tool.write_bytes(raw); tool.chmod(0o600)
    with patch.object(d, 'TOOL', tool), patch('subprocess.Popen', side_effect=AssertionError('unexpected process')):
        p = d.load()
    assert p.ROOT == recovery.OLD
    assert p.LOCK == Path('/var/lib/weknora-enrichment-preload-20261017-r1.lock')
    assert [x['role'] for x in p.ITEMS] == ['postgres', 'redis']
    assert p.ITEMS[1]['archive_bytes'] == 14084608
    assert not p.IMPORTED
    with patch.object(p.cli, 'command', return_value=b'{}') as original:
        mutate = recovery.install_read_checks(p)
        assert mutate is original
        for i in p.ITEMS:
            for prefix in (p.io.CRI, p.cli.CRI):
                assert p.inventory.capture(prefix+['inspecti', i['image']]) == b'{}'
            assert p.inventory.capture(p.io.CTR+['content', 'get', i['config_digest']]) == b'{}'
        assert p.cli.cri('imagefsinfo', '--output', 'json') == {}
        assert p.cli.command(['ps', '-eo', 'comm=,args=']) == b'{}'
        before = original.call_count
        for argv in (p.cli.import_args(Path('/private/archive')), p.io.CTR+['images','label',p.ITEMS[1]['image'],p.cli.PIN]):
            try: p.cli.command(argv)
            except RuntimeError as exc: assert str(exc) == 'non_readonly_command_rejected'
            else: raise AssertionError('mutation allowed by readonly wrapper')
        assert original.call_count == before
print('frozen_preload_bootstrap_and_recovery_contract=PASS')
'''
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(source)], cwd=str(Path(__file__).parent),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', 'replace'))
        self.assertIn(b'frozen_preload_bootstrap_and_recovery_contract=PASS', result.stdout)


if __name__ == '__main__': unittest.main()
