import ast
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import diagnose_enrichment_preload as d
import preload_enrichment_images as p


class ReadOnlyTraceTests(unittest.TestCase):
    def test_allowlist_excludes_all_mutations(self):
        for tail in (['images', 'import', 'file'], ['images', 'label', p.ITEMS[0]['image'], 'pinned=true'],
                     ['images', 'rm', p.ITEMS[0]['image']], ['containers', 'list']):
            with self.assertRaisesRegex(RuntimeError, 'non_readonly'): d.operation(p, p.io.CTR+tail)
        for argv in (['kubectl', 'get', 'pods'], ['ssh', 'datax-02'], ['psql'], ['redis-cli']):
            with self.assertRaisesRegex(RuntimeError, 'non_readonly'): d.operation(p, argv)

    def test_exact_both_cri_prefixes_and_metadata(self):
        for item in p.ITEMS:
            for prefix in (p.io.CRI, p.cli.CRI):
                self.assertEqual(d.operation(p, prefix+['inspecti', item['image']])['role'], item['role'])
            self.assertEqual(d.operation(p, p.io.CTR+['content', 'get', item['config_digest']])['digest'], item['config_digest'])

    def test_original_stale_last_is_not_reported_as_failing_command(self):
        output = io.StringIO()
        def fail(argv, **kwargs):
            self.assertEqual(p.cli.LAST, {})
            p.cli.LAST.update(program='ctr', exit_code=1, stdout_bytes=0, stderr_bytes=50)
            raise p.cli.PreloadFailure('command_failed_STATUS_ONLY')
        with patch.object(p.cli, 'command', side_effect=fail), patch.object(p.inventory, 'capture'), \
             patch.object(p.cli, 'LAST', dict(program='crictl', exit_code=0, stdout_bytes=13585)), \
             contextlib.redirect_stdout(output):
            obs = d.install_trace(p)
            with self.assertRaises(p.cli.PreloadFailure): p.inventory.capture(p.io.CTR+['images', 'ls'])
        self.assertEqual(obs[0]['program'], 'ctr')
        self.assertEqual(obs[0]['exit_code'], 1)
        self.assertEqual(obs[0]['operation'], 'LIST_LOCAL_IMAGE_METADATA')
        self.assertNotIn('13585', output.getvalue())

    def test_capture_success_is_internal_raw_only(self):
        out = io.StringIO(); private = b'{"config":{"Env":["PRIVATE_TOKEN=value"]}}'
        def read(argv, **kwargs):
            p.cli.LAST.update(program='ctr', exit_code=0, stdout_bytes=len(private), stderr_bytes=0)
            return private
        with patch.object(p.cli, 'command', side_effect=read), patch.object(p.inventory, 'capture'), \
             patch.object(p.cli, 'LAST', {}), contextlib.redirect_stdout(out):
            obs = d.install_trace(p)
            self.assertEqual(p.inventory.capture(p.io.CTR+['content', 'get', p.ITEMS[0]['config_digest']]), private)
        self.assertNotIn('PRIVATE_TOKEN', out.getvalue())
        self.assertEqual(obs[0]['check_state'], 'PASS')

    def test_py37_and_pinned_delivery_hash(self):
        ast.parse(Path(d.__file__).read_text(), feature_version=(3, 7))
        self.assertEqual(d.TOOL_SHA, 'db944072aa40e78118e70c0af34bc5f7ccc6a4059f9a8cc39b58a65c9c4f1e93')
        self.assertTrue(str(d.TOOL).endswith('/weknora-enrichment-preload-tool-xIpS67Mi/preload.py'))


if __name__ == '__main__': unittest.main()
