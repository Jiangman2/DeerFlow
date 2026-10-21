import ast
import importlib.util
import io
import contextlib
from pathlib import Path
import types
import unittest

spec = importlib.util.spec_from_file_location('diag', Path(__file__).with_name('diagnose_build_preflight.py'))
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class Tests(unittest.TestCase):
    def test_python37(self):
        ast.parse(Path(d.__file__).read_text(), feature_version=(3, 7))

    def test_allowed_reads(self):
        prefix = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow']
        d.command_summary(prefix+['get', 'node', 'datax-01', '--show-managed-fields=false', '-o', 'json'])
        d.command_summary(prefix+['get', 'pods', '--selector', 'app=mineru', '--show-managed-fields=false', '-o', 'json'])
        d.command_summary(prefix+['exec', 'example', '-c', 'adapter', '--', '/app/.venv/bin/python', '-B', '-c', 'probe'])
        d.command_summary(['ps', '-eo', 'comm=,args='])

    def test_no_external_mutations(self):
        prefix = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow']
        for args in (['systemctl', 'start', 'bad'], prefix+['delete', 'pod', 'x'],
                     prefix+['get', 'secrets', '--show-managed-fields=false', '-o', 'json'], ['ctr', 'images', 'import', 'x']):
            with self.assertRaises(RuntimeError): d.command_summary(args)

    def test_error_output_is_never_echoed(self):
        secret = b'PRIVATE_FIXTURE_DO_NOT_PRINT'
        result = types.SimpleNamespace(returncode=1, stdout=secret, stderr=secret+b' AssertionError')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            d.traced_run(lambda *args, **kw: result, ['ps', '-eo', 'comm=,args='])
        self.assertNotIn(secret.decode(), out.getvalue())
        self.assertIn('remote_assertion', out.getvalue())


if __name__ == '__main__':
    unittest.main()
