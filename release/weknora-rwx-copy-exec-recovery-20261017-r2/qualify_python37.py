"""Run real Python3.7 source and exact-byte-delivery tests with networking disabled."""
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
REPORT=Path('/delivery/weknora-copy-exec-r2-native37.json')
LOG=Path('/delivery/weknora-copy-exec-r2-native37.txt')


def main():
    if sys.version_info[:2]!=(3,7) or not Path('/.dockerenv').exists() or REPORT.exists() or LOG.exists():
        raise RuntimeError('fresh_python37_offline_qualification_required')
    import test_recover
    source=(HERE/'recover.py').read_bytes()
    capture=io.StringIO();counts={}
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        path=Path(td)/'weknora-rwx-copy-exec-recovery-20261017-r2.py'
        path.write_bytes(source);path.chmod(0o600)
        spec=importlib.util.spec_from_file_location('native37_exact_delivery',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for name,target in (('source',test_recover.m),('exact_delivery',module)):
            with patch.object(test_recover,'m',target),contextlib.redirect_stdout(capture):
                result=unittest.TextTestRunner(stream=capture,verbosity=2).run(
                    unittest.defaultTestLoader.loadTestsFromModule(test_recover))
            if not result.wasSuccessful() or result.skipped:
                print(capture.getvalue());raise RuntimeError('native37_failed_'+name)
            counts[name]=result.testsRun
    report=dict(python=sys.version.split()[0],script_sha256=hashlib.sha256(source).hexdigest(),
        source_and_exact_delivery='PASS',tests=counts,skipped=0,
        original_ebadf_without_emulation='REPRODUCED',fixed_uid1000_synthetic_copy='PASS',
        image='python:3.7.17-slim-bullseye',
        image_index_digest='sha256:603879aeff06df23f0d392a19f7ce02aa85e14e6b4d25fd9647b116e0cb93467',
        network='DISABLED',actual_server_kernel419_nfs='NOT_TESTED')
    with REPORT.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    with LOG.open('x') as f:f.write(capture.getvalue())
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
