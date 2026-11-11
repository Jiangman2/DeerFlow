"""Local-only two-class patch of the exact operator-observed SkillHub JAR.

No network, deployment, environment, database or production configuration reads.
Other entry contents, compression modes, permissions, timestamps and comments are preserved.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import zipfile

ORIGINAL_SHA='6fc23f8056b763a1590a9953a83e6bee873e7bb2e935b2dddf93589585500363'
ORIGINAL_BYTES=103450647
PREFIX='BOOT-INF/classes/com/iflytek/skillhub/'
PATCHES={
 'integration/deerflow/DeerFlowRouteSecurityPolicyRegistry.class':{
  'before':'e19ddfcada8dad2c80eb0ed787b35758aee3afe988ce492b50b9f9f4da6f81ff',
  'after':'625596c3bef759024d909fe0265160ffa94f022f434ef764eec0116b15752ec4'},
 'compat/ClawHubRegistrySecurityConfig.class':{
  'before':'247cf497897586ef4f832ef8be0f5e45297f65b7eabcf023711cc2573fe1e006',
  'after':'0c5517708d2cb7fd54352b647f8180c725d536b580a5d0a783abf099aba7aa7e'}}

class Refused(ValueError):
    pass

def require(ok,code):
    if not ok: raise Refused(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_pin(path):
    h=hashlib.sha256();n=0
    with path.open('rb') as f:
        for data in iter(lambda:f.read(1048576),b''):
            n+=len(data);require(n<=268435456,'JAR_FILE_SIZE_BOUND');h.update(data)
    return {'bytes':n,'sha256':h.hexdigest()}

def inventory(archive):
    infos=archive.infolist();require(0<len(infos)<=30000,'JAR_MEMBER_COUNT_BOUND')
    names=[x.filename for x in infos]
    require(len(set(names))==len(names),'DUPLICATE_JAR_ENTRY')
    require(all(not n.startswith('/') and '..' not in n.split('/') and '\\' not in n for n in names),'UNSAFE_JAR_ENTRY')
    require(sum(x.file_size for x in infos)<=1073741824 and all(x.file_size<=268435456 for x in infos),'JAR_EXPANDED_SIZE_BOUND')
    require(not any(n.upper().startswith('META-INF/') and n.upper().endswith(('.SF','.RSA','.DSA','.EC')) for n in names),'SIGNED_TOP_LEVEL_JAR_REQUIRES_REVIEW')
    result={}
    for info in infos:
        raw=archive.read(info)
        result[info.filename]={'bytes':len(raw),'sha256':sha(raw),'compression':info.compress_type,
          'date_time':list(info.date_time),'external_attr':info.external_attr,'internal_attr':info.internal_attr,
          'extra_sha256':sha(info.extra),'comment_sha256':sha(info.comment)}
    return result

def patch_jar(original,classes,output):
    require(original.is_file() and not original.is_symlink(),'REGULAR_ORIGINAL_JAR_REQUIRED')
    require(not output.exists() and not output.is_symlink(),'NEW_OUTPUT_REQUIRED')
    before_pin=file_pin(original)
    require(before_pin=={'bytes':ORIGINAL_BYTES,'sha256':ORIGINAL_SHA},'PRODUCTION_JAR_PIN_MISMATCH')
    replacements={}
    for relative,pins in PATCHES.items():
        path=classes/'com/iflytek/skillhub'/relative
        require(path.is_file() and not path.is_symlink() and path.stat().st_size<=65536,'COMPILED_CLASS_REQUIRED')
        raw=path.read_bytes()
        require(sha(raw)==pins['after'] and raw[:4]==b'\xca\xfe\xba\xbe','COMPILED_CLASS_PIN_MISMATCH')
        replacements[PREFIX+relative]=raw
    with zipfile.ZipFile(original,'r') as source:
        before=inventory(source)
        for relative,pins in PATCHES.items():
            name=PREFIX+relative
            require(name in before and before[name]['sha256']==pins['before'],'ORIGINAL_CLASS_PIN_MISMATCH')
            old=source.read(name);new=replacements[name]
            require(old[:4]==b'\xca\xfe\xba\xbe' and old[4:8]==new[4:8],'CLASS_VERSION_CHANGED')
        require(file_pin(original)==before_pin,'ORIGINAL_CHANGED_BEFORE_COPY')
        # Exclusive output and the original ZipInfo retain stored nested dependency JARs.
        with output.open('xb') as file:
            with zipfile.ZipFile(file,'w',allowZip64=True) as target:
                target.comment=source.comment
                for info in source.infolist():
                    target.writestr(copy.copy(info),replacements.get(info.filename,source.read(info)))
        with zipfile.ZipFile(output,'r') as target:
            after=inventory(target)
            require(target.comment==source.comment,'ARCHIVE_COMMENT_CHANGED')
    require(file_pin(original)==before_pin,'ORIGINAL_CHANGED_DURING_COPY')
    require(set(before)==set(after),'JAR_ENTRY_SET_CHANGED')
    changed=[name for name in before if before[name]!=after[name]]
    require(set(changed)==set(replacements),'UNEXPECTED_JAR_CONTENT_DELTA')
    for name in replacements:
        old=dict(before[name]);new=dict(after[name])
        old.pop('bytes');old.pop('sha256');new.pop('bytes');new.pop('sha256')
        require(old==new,'CLASS_ZIP_METADATA_CHANGED')
    preserved={name:meta for name,meta in before.items() if name not in replacements}
    return {'schema':'portal-skillhub-two-class-patch.v1','original':before_pin,'candidate':file_pin(output),
      'changed_entries':sorted(changed),'preserved_entries':len(preserved),
      'preserved_inventory_sha256':sha(json.dumps(preserved,sort_keys=True,separators=(',',':')).encode()),
      'dependency_jars_preserved':sum(n.startswith('BOOT-INF/lib/') and n.endswith('.jar') for n in preserved),
      'migration_entries_preserved':sum('/db/migration/' in n for n in preserved),
      'original_preserved':True,'production_mutations':0,'candidate_runtime_tests_pending':True,
      'ready_for_rollout':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('original-jar','compiled-classes','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    receipt=patch_jar(args.original_jar,args.compiled_classes,args.output)
    print(json.dumps(receipt,sort_keys=True))
