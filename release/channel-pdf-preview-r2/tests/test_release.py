import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load('r2_package', ROOT / 'package.py')
oci = load('r2_oci', ROOT / 'verify-oci-baseline.py')
ARCHIVE = Path.home() / 'Downloads/deerflow-channel-pdf-preview-20260923-r1.tar.gz'
REPORT = Path.home() / 'Downloads/channel-pdf-baseline-audit-20260923T113615Z.json'


class ProductionReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='r2-release-test-')
        cls.root = Path(cls.tmp.name) / 'package'
        cls.root.mkdir()
        cls.originals, cls.targets = package.assemble(cls.root, ARCHIVE, REPORT)
        cls.deltas = {c: json.loads((cls.root / (c + '-delta.json')).read_bytes()) for c in package.IMAGES}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_manager_only_three_explicit_changes(self):
        before = self.originals['gateway'][package.MANAGER]
        after = self.targets['gateway'][package.MANAGER]
        self.assertNotIn('AsyncKeyedLockTable', after)
        expected = before.replace('from deerflow.config.paths import make_safe_user_id\n',
            'from deerflow.config.paths import make_safe_user_id\nfrom deerflow.constants import ENTERPRISE_PRODUCT_NAME\n')
        expected = expected.replace('"Connect this channel from DeerFlow Settings, complete the in-channel connect step, then send your message again."',
            'f"请先在{ENTERPRISE_PRODUCT_NAME}的「设置 → 渠道」中连接此渠道，完成渠道内绑定后再发送消息。"')
        expected = expected.replace('"Channel connection verification is temporarily unavailable. Please try again later or contact the DeerFlow operator."',
            'f"渠道连接暂时无法验证，请稍后重试或联系{ENTERPRISE_PRODUCT_NAME}管理员。"')
        self.assertEqual(after, expected)

    def test_locales_only_one_description_each(self):
        pairs = {'en-US.ts': ('Feishu and Lark messages through your 本平台 app.', 'Messages through your 海能 Work app.'),
                 'zh-CN.ts': ('通过本平台应用接收飞书和 Lark 消息。', '通过海能 Work 应用接收消息。')}
        for locale, (old, new) in pairs.items():
            name = 'src/core/i18n/locales/' + locale
            self.assertEqual(self.targets['frontend'][name], self.originals['frontend'][name].replace(old, new, 1))
            self.assertIn('appName: "中国海油通用智能体"', self.targets['frontend'][name])

    def test_existing_constant_is_unchanged_and_checked(self):
        name = 'packages/harness/deerflow/constants.py'
        self.assertEqual(self.originals['gateway'][name], self.targets['gateway'][name])
        self.assertEqual(next(e for e in self.deltas['gateway'] if e['path'] == name)['edits'], [])

    def test_entire_payload_prepares_before_mutation_on_last_file_drift(self):
        for component in package.IMAGES:
            with tempfile.TemporaryDirectory(prefix='r2-drift-') as directory:
                root = Path(directory)
                files = self.originals[component]
                for name, source in files.items():
                    p = root / name
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(source.encode())
                last = root / list(files)[-1]
                last.write_bytes(last.read_bytes() + b'\n# unexpected drift\n')
                before = {name: (root / name).read_bytes() for name in files}
                cmd = [sys.executable, str(self.root / 'apply-delta.py')] if component == 'gateway' else ['node', str(self.root / 'apply-delta.mjs')]
                cmd += [str(root), str(self.root / (component + '-delta.json')), str(root / 'receipt.json')]
                result = subprocess.run(cmd, capture_output=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'hash mismatch', result.stderr.lower())
                self.assertFalse((root / 'receipt.json').exists())
                self.assertEqual(before, {name: (root / name).read_bytes() for name in files})

    def test_all_eight_patched_typescript_files_parse(self):
        node_code = r'''
const ts = require(process.argv[1]);
const sources = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
for (const [name,text] of Object.entries(sources)) {
 const source=ts.createSourceFile(name,text,ts.ScriptTarget.Latest,true,name.endsWith('.tsx')?ts.ScriptKind.TSX:ts.ScriptKind.TS);
 if (source.parseDiagnostics.length) throw Error(name+': '+source.parseDiagnostics.map(x=>ts.flattenDiagnosticMessageText(x.messageText,' ')).join(';'));
}
console.log('audited_frontend_syntax=PASS files='+Object.keys(sources).length);
'''
        result = subprocess.run(['node', '-e', node_code, str(REPO / 'frontend/node_modules/typescript/lib/typescript.js')],
                                input=json.dumps(self.targets['frontend']), text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('files=8', result.stdout)

    def test_package_offline_and_no_source_report_inclusion(self):
        names = {p.name for p in self.root.iterdir()}
        self.assertNotIn(REPORT.name, names)
        for p in self.root.iterdir():
            if p.suffix != '.png':
                self.assertNotIn(b'\r', p.read_bytes(), p.name)
        builder = (self.root / 'build-candidate-datax-01.sh').read_text()
        self.assertLess(builder.index('--opt target=source-check'), builder.index('partial_candidate_needs_inspection'))
        self.assertNotIn('kubectl apply', builder)
        self.assertNotIn('kubectl patch', builder)
        self.assertNotIn('images import', builder)
        self.assertIn('channel-pdf-preview-r2', (self.root / 'IMAGE_LOCKS.env').read_text())

    def test_bad_audit_checksum_fails_without_guessing(self):
        doc = json.loads(REPORT.read_bytes())
        doc['components']['gateway']['files'][0]['source'] += '\n# drift\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audit.json'
            path.write_bytes(json.dumps(doc).encode())
            with self.assertRaisesRegex(ValueError, 'checksum'):
                package.load_inputs(ARCHIVE, path)


class OciIdentityTests(unittest.TestCase):
    def make(self, root, image, arch='arm64', identity='config'):
        def store(value):
            raw = json.dumps(value).encode()
            digest = hashlib.sha256(raw).hexdigest()
            path = root / 'blobs/sha256' / digest
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            return {'digest': 'sha256:' + digest, 'size': len(raw)}
        config = store({'os': 'linux', 'architecture': arch})
        manifest = store({'schemaVersion': 2, 'config': config})
        manifest['annotations'] = {'io.containerd.image.name': image, 'org.opencontainers.image.ref.name': 'tag'}
        (root / 'index.json').write_bytes(json.dumps({'manifests': [manifest]}).encode())
        (root / 'manifest.digest').write_bytes(manifest['digest'].encode())
        return {'components': {'gateway': {'image': image, 'runtime_image_id': config['digest'] if identity == 'config' else manifest['digest']}}}

    def test_config_or_manifest_identity(self):
        for identity in ('config', 'manifest'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                oci.verify(root, self.make(root, 'test/image:tag', identity=identity), 'gateway')

    def test_wrong_platform_and_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = self.make(root, 'test/image:tag', arch='amd64')
            with self.assertRaisesRegex(ValueError, 'arm64'):
                oci.verify(root, lock, 'gateway')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = self.make(root, 'test/image:tag')
            lock['components']['gateway']['runtime_image_id'] = 'sha256:' + '0'*64
            with self.assertRaisesRegex(ValueError, 'differs'):
                oci.verify(root, lock, 'gateway')

    def test_corrupted_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = self.make(root, 'test/image:tag')
            selected = (root / 'manifest.digest').read_text().split(':')[1]
            (root / 'blobs/sha256' / selected).write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                oci.verify(root, lock, 'gateway')


if __name__ == '__main__':
    unittest.main()
