"""Offline Linux fixture for the wrapper's stop/export behavior; no real collectors."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
BASELINE = '''import json, os, pathlib, sys
p=pathlib.Path(sys.argv[sys.argv.index('--out')+1]); p.mkdir(mode=0o700)
(p/'REPORT.json').write_text(json.dumps({'fixture':'baseline'}))
pathlib.Path(os.environ['CALL_LOG']).open('a').write('baseline\\n')
sys.exit(int(os.environ.get('BASELINE_RC','0')))
'''
SOURCE = '''import io, json, os, pathlib, sys, tarfile
p=pathlib.Path(sys.argv[sys.argv.index('--out')+1]); p.mkdir(mode=0o700)
assert pathlib.Path(sys.argv[sys.argv.index('--baseline')+1]).is_file()
(p/'REPORT.json').write_text(json.dumps({'fixture':'source'}))
pathlib.Path(os.environ['CALL_LOG']).open('a').write('source\\n')
if os.environ.get('SOURCE_RC','0')=='0':
 with tarfile.open(p/'SOURCE-EVIDENCE.tar.gz','w:gz') as archive:
  info=tarfile.TarInfo('source/example.py'); raw=b'fixture-only'; info.size=len(raw)
  archive.addfile(info,io.BytesIO(raw))
sys.exit(int(os.environ.get('SOURCE_RC','0')))
'''


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'root inside offline Linux fixture only')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wrapper-fixture-')
        self.root = Path(self.tmp.name)
        (self.root/'home').mkdir()
        (self.root/'var').mkdir()
        self.baseline = self.root/'home/portal-ppt-baseline-20261017-r2.py'
        self.baseline.write_text(BASELINE)
        self.source = self.root/'home/portal-source-directory-20261017-r2.py'
        self.source.write_text(SOURCE)
        text = (HERE/'run-portal-ppt-refresh-20261017-r2.sh.in').read_text()
        # Substitute only fixture filesystem/user/hostname boundaries. All hashes,
        # branching, copy/export, failure preservation and archive logic stay real.
        text = text.replace('/home/cnooc', str(self.root/'home'))
        text = text.replace('/var/lib', str(self.root/'var'))
        text = text.replace('cnooc', 'root')
        text = text.replace('test "$(hostname -s)" = datax-01', 'true')
        for marker, path in [('__BASELINE_COLLECTOR_SHA256__', self.baseline),
                             ('__SOURCE_COLLECTOR_SHA256__', self.source)]:
            text = text.replace(marker, hashlib.sha256(path.read_bytes()).hexdigest())
        self.wrapper = self.root/'run.sh'
        self.wrapper.write_text(text)
        self.log = self.root/'calls.txt'

    def tearDown(self):
        self.tmp.cleanup()

    def run_fixture(self, **flags):
        env = dict(os.environ, CALL_LOG=str(self.log), **flags)
        result = subprocess.run(['bash',str(self.wrapper)], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        archives = sorted((self.root/'home').glob('portal-ppt-refresh-*/EVIDENCE.tar.gz'))
        return result, archives

    def members(self, path):
        with tarfile.open(path,'r:gz') as archive:
            return set(archive.getnames()), archive.extractfile('STATUS.txt').read().decode()

    def test_success_exports_only_fixed_evidence(self):
        result, archives = self.run_fixture()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.log.read_text(),'baseline\nsource\n')
        self.assertEqual(len(archives),1)
        names,status = self.members(archives[0])
        self.assertEqual(names, {'STATUS.txt','baseline/REPORT.json','source/REPORT.json','source/SOURCE-EVIDENCE.tar.gz'})
        self.assertIn('ready_for_rollout=false',status)
        self.assertEqual(archives[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(archives[0].parent.stat().st_mode & 0o777,0o700)

    def test_baseline_failure_stops_source_and_preserves_report(self):
        result, archives = self.run_fixture(BASELINE_RC='1')
        self.assertEqual(result.returncode,1)
        self.assertEqual(self.log.read_text(),'baseline\n')
        names,status = self.members(archives[0])
        self.assertEqual(names,{'STATUS.txt','baseline/REPORT.json'})
        self.assertIn('source_exit_code=NOT_RUN',status)

    def test_source_failure_preserves_both_diagnostics(self):
        result, archives = self.run_fixture(SOURCE_RC='3')
        self.assertEqual(result.returncode,3)
        names,status = self.members(archives[0])
        self.assertEqual(names,{'STATUS.txt','baseline/REPORT.json','source/REPORT.json'})
        self.assertIn('source_exit_code=3',status)

    def test_hash_failure_prevents_any_collection(self):
        self.source.write_text(SOURCE+'\n# changed\n')
        result, archives = self.run_fixture()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.log.exists())
        self.assertEqual(archives,[])

    def test_linked_input_rejected_before_collection(self):
        self.source.rename(self.root/'original.py')
        self.source.symlink_to(self.root/'original.py')
        result, archives = self.run_fixture()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.log.exists())
        self.assertEqual(archives,[])

    def test_new_run_preserves_previous_evidence(self):
        result, archives = self.run_fixture()
        self.assertEqual(result.returncode,0)
        first = archives[0]; old = first.read_bytes()
        result, archives = self.run_fixture()
        self.assertEqual(result.returncode,0)
        self.assertEqual(len(archives),2)
        self.assertEqual(first.read_bytes(),old)


if __name__=='__main__':
    unittest.main()
