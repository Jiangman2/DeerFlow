"""Read-only source/config and synthetic Work formatting qualification."""
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import unittest

PARENT_SOURCES={}
NEXT_SOURCES={}
CANDIDATES={}
TEST_SOURCE=''
CONFIG_SHA='b160057a087d848310f4521162fbee07f25178812692c3b1ec253be3632b08bf'

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()

def audit(event,args):
    if event=='open':
        mode=args[1] if len(args)>1 else None;flags=args[2] if len(args)>2 else 0
        need(not(isinstance(mode,str) and any(x in mode for x in 'wax+') or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)),'FILE_WRITE_BLOCKED')
    if event.startswith('socket.') or event in ('subprocess.Popen','os.system','os.mkdir','os.remove','os.rename','os.rmdir','os.chmod','os.chown','os.utime','os.link','os.symlink','sqlite3.connect'):
        raise Refused('NETWORK_OR_MUTATION_BLOCKED')

def read(path,limit=2097152):
    path=Path(path)
    if path==Path('/etc/deerflow-openssl/openssl.cnf'):
        # Kubernetes directory ConfigMaps expose keys through ..data symlinks.
        # Allow this one field-bound source only inside its fixed mount root.
        root=path.parent
        need(not root.is_symlink(),'OPENSSL_MOUNT_ROOT_LINK_REFUSED')
        path=path.resolve(strict=True)
        need(root in path.parents,'OPENSSL_SOURCE_ESCAPED_MOUNT')
    for part in (path,)+tuple(path.parents):need(not part.is_symlink(),'SOURCE_LINK_REFUSED')
    before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_size<=limit,'SOURCE_BOUND')
    raw=path.read_bytes();after=path.stat()
    need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'SOURCE_CHANGED')
    return raw

def observe(mode):
    need(mode in ('qualify','verify'),'QUALIFICATION_MODE_REQUIRED')
    expected=PARENT_SOURCES if mode=='qualify' else NEXT_SOURCES
    need(len(expected)==14 and set(PARENT_SOURCES)==set(NEXT_SOURCES),'FOURTEEN_SOURCE_FILES_REQUIRED')
    verified={}
    for path,row in expected.items():
        raw=read(path);need(len(raw)==row['bytes'] and sha(raw)==row['sha256'],'SOURCE_HASH_MISMATCH')
        verified[path]={'bytes':len(raw),'sha256':sha(raw)}
    config=read('/app/backend/config.yaml',65536);need(sha(config)==CONFIG_SHA,'BUDGET_CONFIGURATION_CHANGED')
    import yaml
    parsed=yaml.safe_load(config)
    need(type(parsed.get('max_recursion_limit')) is int and parsed['max_recursion_limit']==10000
         and type(parsed.get('scheduler',{}).get('recursion_limit')) is int and parsed['scheduler']['recursion_limit']==10000,'BUDGET_10000_REQUIRED')
    candidates={}
    for name,item in CANDIDATES.items():
        path='/app/backend/app/scheduler/'+name
        raw=base64.b64decode(item['base64'],validate=True) if mode=='qualify' else read(path)
        need(sha(raw)==item['sha256'] and len(raw)==item['bytes'],'CANDIDATE_HASH_MISMATCH')
        candidates[name]=raw.decode('utf-8')
    need(set(candidates)=={'work_notification_delivery.py','work_notifications.py'},'EXACT_NOTIFICATION_FILES_REQUIRED')
    namespace={'__name__':'isolated_work_notification_tests','CANDIDATES':candidates}
    exec(compile(TEST_SOURCE,'<isolated-notification-tests>','exec'),namespace)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(namespace['NotificationLinkTests'])
    logs=io.StringIO();result=unittest.TextTestRunner(stream=logs,verbosity=1).run(suite)
    need(result.testsRun==12 and result.wasSuccessful() and not result.skipped,'NOTIFICATION_REGRESSIONS_FAILED')
    return {'status':'GATEWAY_READER_NOTIFICATION_SOURCES_QUALIFIED','mode':mode,'source_files_verified':len(verified),'sources':verified,
            'config_sha256':sha(config),'scheduler_recursion_limit':10000,'max_recursion_limit':10000,
            'synthetic_checks':12,'synthetic_diagnostic_sha256':sha(logs.getvalue().encode()),
            'application_imported':False,'loaded_application_modules_verified':False,'isolated_source_functions_qualified':True,
            'credentials_exported':False,'provider_requests':0,'database_access':False,'persistent_files_modified':False,'messages_sent':0}

def main():
    sys.dont_write_bytecode=True
    out={'status':'INCOMPLETE','provider_requests':0,'database_access':False,'messages_sent':0,'credentials_exported':False}
    try:
        sys.addaudithook(audit)
        need(len(sys.argv)==2,'MODE_ARGUMENT_REQUIRED')
        out=observe(sys.argv[1])
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'SOURCE_QUALIFICATION_FAILED_REDACTED')
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
