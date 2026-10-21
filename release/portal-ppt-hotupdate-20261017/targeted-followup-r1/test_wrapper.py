"""Exercise real wrapper stop/copy/export behavior in an offline Linux fixture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

if os.name == 'posix':
    import pwd

HERE = Path(__file__).resolve().parent
COLLECTOR = '''import json,os,pathlib,sys
p=pathlib.Path(sys.argv[sys.argv.index('--out')+1]);p.mkdir(mode=0o700)
assert pathlib.Path(sys.argv[sys.argv.index('--baseline')+1]).read_bytes()==b'{"fixture":true}'
pathlib.Path(os.environ['CALL_LOG']).open('a').write('collect\\n')
if os.environ.get('NO_REPORT')!='1': (p/'REPORT.json').write_text('{"fixture":"report"}')
sys.exit(int(os.environ.get('FIXTURE_RC','0')))
'''

@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'offline root Linux fixture')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='targeted-wrapper-')
        self.root = Path(self.temp.name)
        (self.root/'home').mkdir(); (self.root/'var').mkdir()
        self.helper = self.root/'home/portal-ppt-baseline-helper-20261017-r1.py'
        self.collector = self.root/'home/portal-ppt-targeted-20261017-r1.py'
        self.baseline = self.root/'var/portal-ppt-refresh-tool-TeLN6c6D/baseline/REPORT.json'
        self.baseline.parent.mkdir(parents=True)
        self.helper.write_text('# fixture never executed\n')
        self.collector.write_text(COLLECTOR)
        self.baseline.write_bytes(b'{"fixture":true}')
        text = (HERE/'run-portal-ppt-targeted-20261017-r1.sh.in').read_text()
        text = text.replace('/home/cnooc',str(self.root/'home')).replace('/var/lib',str(self.root/'var'))
        text = text.replace('cnooc','root').replace('test "$(hostname -s)" = datax-01','true')
        for marker,p in [('__HELPER_SHA256__',self.helper),('__COLLECTOR_SHA256__',self.collector),('__BASELINE_REPORT_SHA256__',self.baseline)]:
            text=text.replace(marker,hashlib.sha256(p.read_bytes()).hexdigest())
        self.wrapper=self.root/'run.sh';self.wrapper.write_text(text)
        self.log=self.root/'calls.log'

    def tearDown(self):
        self.temp.cleanup()

    def run_fixture(self, **flags):
        result=subprocess.run(['bash',str(self.wrapper)],env=dict(os.environ,CALL_LOG=str(self.log),**flags),capture_output=True,timeout=15)
        reports=list((self.root/'home').glob('portal-ppt-targeted-20261017-r1-*/REPORT.json'))
        return result,reports

    def test_success_exports_private_report(self):
        r,reports=self.run_fixture()
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.log.read_text(),'collect\n')
        self.assertEqual(len(reports),1)
        self.assertEqual(json.loads(reports[0].read_text()),{'fixture':'report'})
        self.assertEqual(reports[0].stat().st_mode & 0o777,0o600)
        self.assertEqual(reports[0].parent.stat().st_mode & 0o777,0o700)

    def test_failure_keeps_report_and_true_exit(self):
        r,reports=self.run_fixture(FIXTURE_RC='3')
        self.assertEqual(r.returncode,3)
        self.assertEqual(len(reports),1)

    def test_missing_report_cannot_report_success(self):
        r,reports=self.run_fixture(NO_REPORT='1')
        self.assertEqual(r.returncode,1)
        self.assertEqual(reports,[])
        self.assertIn(b'report_unavailable=true',r.stdout)

    def test_changed_reference_prevents_collection(self):
        self.baseline.write_text('{}')
        r,reports=self.run_fixture()
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.log.exists())
        self.assertEqual(reports,[])

    def test_changed_collector_prevents_execution(self):
        self.collector.write_text('raise RuntimeError("must not execute")')
        r,reports=self.run_fixture()
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.log.exists())
        self.assertEqual(reports,[])

    def test_symlink_input_rejected(self):
        raw=self.collector.read_bytes();self.collector.unlink()
        target=self.root/'target.py';target.write_bytes(raw);self.collector.symlink_to(target)
        r,reports=self.run_fixture()
        self.assertNotEqual(r.returncode,0)
        self.assertFalse(self.log.exists())
        self.assertEqual(reports,[])

    def test_new_manual_invocation_preserves_earlier_report(self):
        r,first=self.run_fixture();self.assertEqual(r.returncode,0)
        old=first[0].read_bytes()
        r,all_reports=self.run_fixture(FIXTURE_RC='2')
        self.assertEqual(r.returncode,2)
        self.assertEqual(len(all_reports),2)
        self.assertEqual(first[0].read_bytes(),old)

    def use_unprivileged_export(self):
        actor = pwd.getpwnam('nobody')
        self.root.chmod(0o755)
        (self.root/'home').chmod(0o777)
        text = self.wrapper.read_text().replace("pwd.getpwnam('root')", "pwd.getpwnam('nobody')")
        self.wrapper.write_text(text)
        return actor

    def test_report_export_really_drops_root(self):
        actor = self.use_unprivileged_export()
        r,reports = self.run_fixture()
        self.assertEqual(r.returncode, 0, r.stdout+r.stderr)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].stat().st_uid, actor.pw_uid)
        self.assertEqual(reports[0].stat().st_gid, actor.pw_gid)
        self.assertEqual(reports[0].parent.stat().st_uid, actor.pw_uid)
        self.assertEqual(reports[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(reports[0].parent.stat().st_mode & 0o777, 0o700)

    def test_replaced_export_directory_never_receives_root_write(self):
        self.use_unprivileged_export()
        victim = self.root/'protected'; victim.mkdir(mode=0o700)
        original = victim/'REPORT.json'; original.write_text('do not replace')
        text = self.wrapper.read_text().replace(
            '    directory = os.open(export,',
            "    os.rename(export, export + '-moved')\n"
            + "    os.symlink(" + repr(str(victim)) + ", export, target_is_directory=True)\n"
            + '    directory = os.open(export,')
        self.wrapper.write_text(text)
        r,_ = self.run_fixture()
        self.assertEqual(r.returncode, 1, r.stdout+r.stderr)
        self.assertIn(b'export_failed=true; private_report_preserved=true', r.stdout)
        self.assertEqual(original.read_text(), 'do not replace')
        self.assertEqual(victim.stat().st_uid, 0)
        saved = list((self.root/'var').glob('portal-ppt-targeted-tool-*/report/REPORT.json'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(json.loads(saved[0].read_text()), {'fixture':'report'})

if __name__=='__main__':unittest.main()
