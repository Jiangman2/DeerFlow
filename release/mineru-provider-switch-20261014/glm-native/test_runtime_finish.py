import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]/'release/production-build-alignment-20260928/r4-build'))
import prepare_runtime_finish as r
import verify_runtime_candidate as v
import finish_runtime_build as driver
import runtime_check as runtime


class RuntimeFinishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def zipper(self, missing=None, symlink=None):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as z:
            for name in r.DICT_NAMES:
                if name == missing:
                    continue
                info = zipfile.ZipInfo(r.ZIP_PREFIX+name)
                info.external_attr = (0o120777 if name == symlink else 0o100644) << 16
                z.writestr(info, name.encode())
        return raw.getvalue()

    def test_only_five_locked_dictionary_files(self):
        self.assertEqual(set(r.dictionaries(self.zipper())), set(r.DICT_NAMES))
        for option in ({'missing': 'idf.utf8'}, {'symlink': 'user.dict.utf8'}):
            with self.assertRaises(r.safe.CheckFailure):
                r.dictionaries(self.zipper(**option))

    def test_context_member_and_hash_binding(self):
        raw = self.zipper(); archive = self.root/'context.tar'
        rows = {'source/SOURCE.SHA256': b'locked-source', 'offline/'+r.ZIP_NAME: raw,
                'offline/INPUTS.SHA256': (hashlib.sha256(raw).hexdigest()+'  '+r.ZIP_NAME+'\n').encode()}
        with tarfile.open(archive, 'w') as t:
            for name, data in rows.items():
                m = tarfile.TarInfo(name); m.size = len(data); t.addfile(m, io.BytesIO(data))
        self.assertEqual(r.context_inputs(archive), rows)

    def test_only_dictionary_env_may_change(self):
        base = {'config': {'Env': ['EXISTING=yes'], 'Cmd': ['server'], 'User': '1000'},
                'rootfs': {'diff_ids': ['base']}}
        after = copy.deepcopy(base)
        after['config']['Env'].append(v.ENV); after['rootfs']['diff_ids'].append('delta')
        v.preserved_with_dictionary(base, after)
        self.assertEqual(base['config']['Env'], ['EXISTING=yes'])
        bad = copy.deepcopy(after); bad['config']['User'] = '0'
        with self.assertRaisesRegex(RuntimeError, 'unexpected_runtime_config_delta'):
            v.preserved_with_dictionary(base, bad)
        base['config']['Env'].append('JIEBA_DICT_DIR=/operator-custom')
        with self.assertRaisesRegex(RuntimeError, 'base_dictionary_override'):
            v.preserved_with_dictionary(base, after)

    def test_empty_dictionary_env_replaced_not_duplicated(self):
        base = {'config': {'Env': ['JIEBA_DICT_DIR=', 'X=1']}, 'rootfs': {'diff_ids': ['base']}}
        after = copy.deepcopy(base); after['config']['Env'][0] = v.ENV
        after['rootfs']['diff_ids'].append('new')
        v.preserved_with_dictionary(base, after)

    def test_file_delta_requires_exact_hash_mode_and_set(self):
        file = self.root/'blob'
        name = v.DICT_DIR+'/idf.utf8'; data = b'locked dictionary'
        with tarfile.open(file, 'w') as t:
            m = tarfile.TarInfo(name); m.size=len(data); m.mode=0o644
            t.addfile(m, io.BytesIO(data))
        expected = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644}}
        seen=set(); v.delta(file, {'mediaType': 'tar'}, expected, seen)
        self.assertEqual(seen, {name})
        for bad in ({}, {name: {'sha256': '0'*64, 'mode': 0o644}},
                    {name: {'sha256': expected[name]['sha256'], 'mode': 0o755}}):
            with self.assertRaises(RuntimeError):
                v.delta(file, {'mediaType': 'tar'}, bad, set())

    def test_successful_compile_snapshot_must_be_unique(self):
        snapshots = self.root/'snapshots'; snapshots.mkdir()
        hashes = ''.join('a'*64+'  /out/'+n+'\n' for n in ('WeKnora','weknora-anydoc-probe','weknora-glm-probe'))
        for number in ('1', '2'):
            proof = snapshots/number/'fs/out/evidence'; proof.mkdir(parents=True)
            for name in r.PROOF: (proof/name).write_bytes(b'proof')
            (proof/'source.sha256').write_bytes(b'source')
            (proof/'binaries.sha256').write_text(hashes)
        with patch.object(r, 'small', side_effect=lambda path,fs: path.read_bytes()):
            with self.assertRaisesRegex(RuntimeError, 'exact_completed_compile_snapshot_required'):
                r.find_compiled(snapshots, b'source')

    def test_error_classification_no_raw_output(self):
        result=runtime.classes('panic: Dictionary file does not exist: /private/path\ncredential=not-for-output')
        self.assertTrue(result['go_panic']); self.assertTrue(result['missing_dictionary'])
        self.assertNotIn('private', json.dumps(result)); self.assertNotIn('credential', json.dumps(result))

    def test_runtime_recipe_never_recompiles_or_submits(self):
        text=(HERE/'Dockerfile.runtime-finish').read_text()
        for forbidden in ('FROM compile', 'go build', 'cargo ', '--probe-once', 'apt-get'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(text.count('ENV JIEBA_DICT_DIR='),2)
        self.assertEqual(text.count('RUN '),1)
        self.assertIn('RUN --network=none',text)
        final=text.split('AS candidate\n')[1].split('FROM scratch')[0]
        self.assertEqual(final.count('COPY '),4)
        self.assertNotIn('runtime_check.py',final)

    def test_python37_and_frozen_resource_driver(self):
        for name in ('prepare_runtime_finish.py','verify_runtime_candidate.py','finish_runtime_build.py','runtime_check.py'):
            ast.parse((HERE/name).read_text(),feature_version=(3,7))
        self.assertTrue(issubclass(driver.RuntimeBuilder,driver.original.Builder))
        self.assertIs(driver.RuntimeBuilder.start, driver.original.Builder.start)
        self.assertIs(driver.RuntimeBuilder.guard, driver.original.Builder.guard)
        self.assertIs(driver.RuntimeBuilder.stop, driver.original.Builder.stop)


if __name__ == '__main__':
    unittest.main()
