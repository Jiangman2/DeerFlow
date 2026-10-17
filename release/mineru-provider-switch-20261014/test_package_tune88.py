import ast
import base64
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import package_tune88 as p
import tune_weknora88 as t


class Delivery(unittest.TestCase):
    def test_all_embedded_sources_exact_and_no_start_on_import(self):
        raw, hashes = p.bundle()
        tree = ast.parse(raw)
        encoded = [n.args[0].value for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == 'b85decode']
        self.assertEqual(len(encoded), len(p.MODULES))
        for name, blob in zip(p.MODULES, encoded):
            source = base64.b85decode(blob)
            self.assertEqual(source, (p.ROOT/(name+'.py')).read_bytes())
            self.assertEqual(hashlib.sha256(source).hexdigest(), hashes[name+'.py'])
        prior = {n:sys.modules.get(n) for n in p.MODULES}
        try:
            with patch.object(t.subprocess, 'Popen', side_effect=AssertionError('no commands on import')):
                exec(compile(raw, '<delivery>', 'exec'), {'__name__':'delivery_test'})
            self.assertIs(sys.modules['observe_tune88'].tune, sys.modules['tune_weknora88'])
        finally:
            for n, mod in prior.items():
                if mod is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = mod

    def test_launcher_no_retries_and_observe_only_after_success(self):
        script = p.launcher('a'*64).decode()
        self.assertEqual(script.count('"$CONTROL/tune.py" --apply'), 1)
        self.assertEqual(script.count('"$CONTROL/tune.py" --observe'), 1)
        self.assertLess(script.index('test "${RESULT[0]}" -eq 0'), script.index('--observe'))
        self.assertLess(script.index('test "${RESULT[1]}" -eq 0'), script.index('--observe'))
        self.assertIn('umask 077', script)
        self.assertIn('-m 0600', script)
        self.assertNotIn('--rollback', script)

    def test_launcher_rejects_digest_injection(self):
        with self.assertRaises(ValueError):
            p.launcher('x;echo bad')

    def test_previous_lock_is_readonly_shared_nonblocking(self):
        fcntl = SimpleNamespace(LOCK_SH=1, LOCK_NB=4, flock=Mock())
        meta = SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1)
        with patch.object(t.obs, 'safe_parent'), patch.object(t, 'fcntl', fcntl), \
             patch.object(t.os, 'O_NOFOLLOW', 0x100000, create=True), \
             patch.object(t.os, 'O_NONBLOCK', 0x200000, create=True), \
             patch.object(t.os, 'open', return_value=123) as op, patch.object(t.os, 'fstat', return_value=meta), \
             patch.object(t.os, 'close') as close:
            self.assertEqual(t.acquire_previous_lock(), 123)
            op.assert_called_once_with(str(t.PREVIOUS_ROOT/'controller.lock'), t.os.O_RDONLY|0x100000|0x200000)
            fcntl.flock.assert_called_once_with(123, 5)
            close.assert_not_called()
            fcntl.flock.side_effect = BlockingIOError()
            with self.assertRaises(BlockingIOError):
                t.acquire_previous_lock()
            close.assert_called_once_with(123)


if __name__ == '__main__':
    unittest.main()
