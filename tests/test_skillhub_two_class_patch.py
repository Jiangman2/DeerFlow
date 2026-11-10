import hashlib
import importlib.util
import pathlib
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jar_patch',ROOT/'deploy/enterprise/patch-skillhub-auth-jar-20261105.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class TwoClassPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name);self.jar=self.root/'original.jar';self.output=self.root/'candidate.jar';self.classes=self.root/'classes'
        self.old={rel:b'\xca\xfe\xba\xbe\x00\x00\x00\x41old:'+rel.encode() for rel in m.PATCHES}
        self.new={rel:b'\xca\xfe\xba\xbe\x00\x00\x00\x41new-auth:'+rel.encode() for rel in m.PATCHES}
        for rel,raw in self.new.items():
            p=self.classes/'com/iflytek/skillhub'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        self.files={m.PREFIX+rel:raw for rel,raw in self.old.items()}
        self.files.update({'BOOT-INF/lib/dependency.jar':b'untouched dependency',
                          'BOOT-INF/classes/db/migration/V1.sql':b'unchanged schema',
                          'META-INF/MANIFEST.MF':b'Manifest-Version: 1.0\n',
                          'BOOT-INF/classes/unchanged.class':b'untouched original class'})
        self.write_jar()
        self.apply_pins()
    def write_jar(self):
        with zipfile.ZipFile(self.jar,'w') as z:
            z.comment=b'original archive comment'
            for name,raw in self.files.items():
                info=zipfile.ZipInfo(name,(2026,9,8,1,2,4));info.external_attr=0o100644<<16;info.comment=b'entry comment'
                info.compress_type=zipfile.ZIP_STORED if name.endswith('.jar') else zipfile.ZIP_DEFLATED
                z.writestr(info,raw)
    def apply_pins(self):
        pin=m.file_pin(self.jar)
        for patcher in (patch.object(m,'ORIGINAL_SHA',pin['sha256']),patch.object(m,'ORIGINAL_BYTES',pin['bytes']),
            patch.object(m,'PATCHES',{rel:{'before':m.sha(self.old[rel]),'after':m.sha(self.new[rel])} for rel in self.old})):
            patcher.start();self.addCleanup(patcher.stop)
    def run_patch(self):
        return m.patch_jar(self.jar,self.classes,self.output)
    def test_only_two_classes_change_dependencies_and_migrations_preserved(self):
        original=self.jar.read_bytes();r=self.run_patch()
        self.assertEqual(self.jar.read_bytes(),original)
        self.assertEqual(r['changed_entries'],sorted(m.PREFIX+rel for rel in self.old))
        self.assertEqual(r['dependency_jars_preserved'],1);self.assertEqual(r['migration_entries_preserved'],1)
        self.assertFalse(r['ready_for_rollout'])
        with zipfile.ZipFile(self.jar) as a,zipfile.ZipFile(self.output) as b:
            for name in self.files:
                self.assertEqual(a.getinfo(name).compress_type,b.getinfo(name).compress_type)
                self.assertEqual(a.getinfo(name).external_attr,b.getinfo(name).external_attr)
                self.assertEqual(a.getinfo(name).date_time,b.getinfo(name).date_time)
                if not name.startswith(m.PREFIX):self.assertEqual(a.read(name),b.read(name))
            self.assertEqual(a.comment,b.comment)
    def test_changed_original_refused_before_output(self):
        with self.jar.open('ab') as f:f.write(b'changed')
        with self.assertRaisesRegex(m.Refused,'PRODUCTION_JAR_PIN_MISMATCH'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_wrong_compiled_class_refused_before_output(self):
        rel=next(iter(self.new));(self.classes/'com/iflytek/skillhub'/rel).write_bytes(b'wrong')
        with self.assertRaisesRegex(m.Refused,'COMPILED_CLASS_PIN_MISMATCH'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_missing_compiled_class_refused(self):
        rel=next(iter(self.new));(self.classes/'com/iflytek/skillhub'/rel).unlink()
        with self.assertRaisesRegex(m.Refused,'COMPILED_CLASS_REQUIRED'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_existing_candidate_never_overwritten(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(m.Refused,'NEW_OUTPUT_REQUIRED'):self.run_patch()
        self.assertEqual(self.output.read_bytes(),b'keep')
    def test_wrong_old_class_refused(self):
        rel=next(iter(self.old));self.files[m.PREFIX+rel]=b'other baseline';self.write_jar();pin=m.file_pin(self.jar)
        with patch.object(m,'ORIGINAL_SHA',pin['sha256']),patch.object(m,'ORIGINAL_BYTES',pin['bytes']),self.assertRaisesRegex(m.Refused,'ORIGINAL_CLASS_PIN_MISMATCH'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_signed_jar_refused(self):
        self.files['META-INF/APP.SF']=b'signature';self.write_jar();pin=m.file_pin(self.jar)
        with patch.object(m,'ORIGINAL_SHA',pin['sha256']),patch.object(m,'ORIGINAL_BYTES',pin['bytes']),self.assertRaisesRegex(m.Refused,'SIGNED_TOP_LEVEL_JAR_REQUIRES_REVIEW'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_java_version_change_refused(self):
        rel=next(iter(self.new));changed=self.new[rel][:6]+b'\x00\x42'+self.new[rel][8:]
        (self.classes/'com/iflytek/skillhub'/rel).write_bytes(changed)
        pins={k:dict(v) for k,v in m.PATCHES.items()};pins[rel]['after']=m.sha(changed)
        with patch.object(m,'PATCHES',pins),self.assertRaisesRegex(m.Refused,'CLASS_VERSION_CHANGED'):self.run_patch()
        self.assertFalse(self.output.exists())
    def test_duplicate_entry_refused(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.jar,'a') as z:z.writestr('META-INF/MANIFEST.MF',b'duplicate')
        pin=m.file_pin(self.jar)
        with patch.object(m,'ORIGINAL_SHA',pin['sha256']),patch.object(m,'ORIGINAL_BYTES',pin['bytes']),self.assertRaisesRegex(m.Refused,'DUPLICATE_JAR_ENTRY'):self.run_patch()
        self.assertFalse(self.output.exists())

if __name__=='__main__':unittest.main()
