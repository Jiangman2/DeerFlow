"""Offline source/exact-delivery tests under the actual Python3.7 interpreter."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
REPORT=Path('/delivery/weknora-datax03-nfs-test-stage-r1-native37.json')
LOG=Path('/delivery/weknora-datax03-nfs-test-stage-r1-native37.txt')
NAME='weknora-datax03-nfs-test-stage-20261017-r1.py'


def main():
    if sys.version_info[:2]!=(3,7) or not Path('/.dockerenv').exists() or REPORT.exists() or LOG.exists():
        raise RuntimeError('fresh_offline_python37_qualification_required')
    import test_stage
    raw=(HERE/'stage.py').read_bytes();capture=io.StringIO();counts={}
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        path=Path(td)/NAME;path.write_bytes(raw);path.chmod(0o600)
        spec=importlib.util.spec_from_file_location('native37_delivery',path)
        delivery=importlib.util.module_from_spec(spec);spec.loader.exec_module(delivery)
        for name,module in (('source',test_stage.m),('exact_delivery',delivery)):
            with patch.object(test_stage,'m',module),contextlib.redirect_stdout(capture):
                result=unittest.TextTestRunner(stream=capture,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(test_stage))
            if not result.wasSuccessful() or result.skipped:
                print(capture.getvalue());raise RuntimeError('qualification_failed_'+name)
            counts[name]=result.testsRun
    report=dict(python=sys.version.split()[0],script_sha256=hashlib.sha256(raw).hexdigest(),
        result='PASS',tests=counts,skipped=0,network='DISABLED',mount_exportfs_services='MOCKED',
        actual_nfs_kernel_and_crossnode='NOT_TESTED')
    with REPORT.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    with LOG.open('x') as f:f.write(capture.getvalue())
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
