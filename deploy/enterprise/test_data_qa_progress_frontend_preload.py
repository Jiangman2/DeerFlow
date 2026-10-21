"""Linux offline qualification of the full-OCI node import wrappers."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGE=Path(os.environ.get('FRONTEND_PROGRESS_PACKAGE','/package'))


def load(role):
    p=PACKAGE/('preload-portal-frontend-data-qa-progress-'+role+'-20261021-r1.py')
    spec=importlib.util.spec_from_file_location('preload_'+role,p)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.HOST=next(iter(module.NODES))
    return module


class PreloadTests(unittest.TestCase):
    def test_actual_full_export_all_blob_and_diffid_hashes(self):
        for role in ('0015','datax02'):
            with self.subTest(role=role):
                module=load(role); importer,_,_=module.load_inputs()
                with (PACKAGE/'frontend.data-qa-progress.full.oci.tar').open('rb') as source:
                    result=module.inspect_archive(source.fileno(),importer,time.monotonic())
                self.assertEqual(len(result['chains']),11)
                self.assertEqual(result['chains'][-1]['chain_id'],module.FINAL_CHAIN)
                self.assertEqual(result['manifest']['config']['digest'],module.CONFIG)

    def test_full_transport_never_queries_containerd_cache(self):
        module=load('0015'); controller=SimpleNamespace(report={})
        with (PACKAGE/'frontend.data-qa-progress.full.oci.tar').open('rb') as source:
            fd=module.assemble_complete(source.fileno(),None,controller,None,time.monotonic())
            try:
                self.assertEqual(os.fstat(fd).st_size,module.TRANSPORT_PIN['bytes'])
                self.assertFalse(controller.report['cached_containerd_payload_bytes_read'])
            finally: os.close(fd)
            self.assertFalse(source.closed)

    def test_unknown_node_stops_before_tool_lookup(self):
        for role in ('0015','datax02'):
            module=load(role)
            with patch.object(module.os,'uname',return_value=SimpleNamespace(nodename='unknown-node')),patch.object(module.shutil,'which',side_effect=AssertionError('TOOL_LOOKUP_FORBIDDEN')),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(),1)

    def test_changed_embedded_importer_refused(self):
        module=load('0015'); module.SOURCES['importer']+=b'\n'
        with self.assertRaises(Exception): module.load_inputs()

    def test_wrong_metadata_digest_refused_before_import(self):
        module=load('0015'); importer,_,_=module.load_inputs(); module.CONFIG='sha256:'+'0'*64
        with (PACKAGE/'frontend.data-qa-progress.full.oci.tar').open('rb') as source:
            with self.assertRaises(Exception): module.inspect_archive(source.fileno(),importer,time.monotonic())

    def test_truncated_transport_refused(self):
        module=load('datax02'); importer,_,_=module.load_inputs()
        with tempfile.TemporaryFile() as short,(PACKAGE/'frontend.data-qa-progress.full.oci.tar').open('rb') as source:
            short.write(source.read(65536)); short.flush(); short.seek(0)
            with self.assertRaises(Exception): module.inspect_archive(short.fileno(),importer,time.monotonic())


if __name__=='__main__': unittest.main()
