"""Read one trusted owner's persisted thread files through a Gateway mount."""
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import signal
import socket
import sys
from urllib.parse import urlsplit

THREAD='201fb3d5-49c0-4276-9e3f-3db67f8edfe7'
CLASSIFIER_B64=''
CLASSIFIER_SHA=''
READONLY_SQL='SELECT user_id FROM threads_meta WHERE thread_id=$1'
DB_HOST=None
DB_PORT=None
DB_ADDRESSES=set()
ALLOW_DB_DNS=False


class Refused(ValueError):pass


def need(ok,code):
    if not ok:raise Refused(code)


def digest(raw):return hashlib.sha256(raw).hexdigest()


def audit(event,args):
    if event=='open':
        mode=args[1] if len(args)>1 else None
        flags=args[2] if len(args)>2 else 0
        need(not (isinstance(mode,str) and any(token in mode for token in ('w','a','x','+'))),'READONLY_FILE_WRITE_BLOCKED')
        need(not (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)),'READONLY_FILE_WRITE_BLOCKED')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.chown','os.utime','subprocess.Popen','os.system','sqlite3.connect'):
        raise Refused('READONLY_OPERATION_BLOCKED')
    if event=='socket.getaddrinfo':
        need(ALLOW_DB_DNS and args[0]==DB_HOST and args[1] in (DB_PORT,str(DB_PORT),None),'NON_DATABASE_DNS_BLOCKED')
    if event=='socket.connect':
        target=args[1]
        need(isinstance(target,tuple) and len(target)>=2 and target[0] in DB_ADDRESSES and target[1]==DB_PORT,'NON_DATABASE_NETWORK_BLOCKED')
    if event in ('socket.bind','socket.gethostbyname','socket.gethostbyaddr'):
        raise Refused('NON_DATABASE_NETWORK_BLOCKED')


def runtime_modules():
    import asyncpg
    from deerflow.config import get_app_config
    from deerflow.config import paths
    return asyncpg,get_app_config,paths


def authorize_mounts(value):
    need(isinstance(value,list) and 0<len(value)<=8,'USERDATA_MOUNT_BOUND')
    roots=[]
    for row in value:
        need(isinstance(row,dict) and set(row)=={'mount_path','claim','subpath_sha256'},'USERDATA_MOUNT_SCHEMA')
        name=row['mount_path']
        need(isinstance(name,str) and name.startswith('/') and '\\' not in name and '..' not in PurePosixPath(name).parts
             and str(PurePosixPath(name))==name and len(PurePosixPath(name).parts)>1,'USERDATA_MOUNT_PATH')
        need(isinstance(row['claim'],str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}',row['claim']),'USERDATA_CLAIM_NAME')
        roots.append(Path(name).resolve())
    return roots


def source_fingerprint(module):
    path=Path(module.__file__).resolve()
    need(path.is_file() and path.stat().st_size<=262144,'PATH_RESOLVER_SOURCE_BOUND')
    raw=path.read_bytes()
    return {'bytes':len(raw),'sha256':digest(raw)}


def trusted_root(paths,owner,mounts):
    need(isinstance(owner,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',owner),'THREAD_OWNER_REQUIRED')
    resolver=paths.get_paths()
    base=Path(resolver.base_dir)
    root=Path(resolver.sandbox_user_data_dir(THREAD,user_id=owner))
    need(base.is_absolute() and root.is_absolute(),'THREAD_STORAGE_ABSOLUTE_REQUIRED')
    for item in (base,root):
        for part in (item,)+tuple(item.parents):
            need(not part.is_symlink(),'THREAD_STORAGE_LINK_REFUSED')
    base=base.resolve();root=root.resolve()
    need(root==base/'users'/owner/'threads'/THREAD/'user-data','THREAD_STORAGE_LAYOUT_UNPROVEN')
    matched=[mount for mount in mounts if base==mount or mount in base.parents]
    need(bool(matched) and any(mount==root or mount in root.parents for mount in matched),'THREAD_STORAGE_NOT_ON_MOUNTED_USERDATA_PVC')
    return root


def classifier():
    raw=base64.b64decode(CLASSIFIER_B64,validate=True)
    need(digest(raw)==CLASSIFIER_SHA,'CLASSIFIER_SOURCE_CHANGED')
    namespace={'__name__':'fixed_stored_market_classifier'}
    exec(compile(raw,'<fixed-market-classifier>','exec'),namespace,namespace)
    return namespace


def inspect_stored(root):
    reader=classifier()
    workspace=root/'workspace'
    reader['ROOT']=workspace;reader['OUTPUTS']=root/'outputs'
    reader['no_links'](root)
    output={'workspace_present':workspace.is_dir(),'projects':[],'raw_file_contents_exported':False,'generation_subdirectories_not_inspected':True}
    if not output['workspace_present']:
        output['published_outputs']=reader['report_files'](root/'outputs')
        return output
    reader['no_links'](workspace)
    found=[];count=0
    with os.scandir(str(workspace)) as entries:
        for entry in entries:
            count+=1;need(count<=2048,'WORKSPACE_ENTRY_BOUND')
            if not re.fullmatch(r'energy-daily-[A-Za-z0-9_-]{1,120}',entry.name):continue
            need(not entry.is_symlink(),'PROJECT_LINK_REFUSED')
            if entry.is_dir(follow_symlinks=False):found.append(workspace/entry.name)
    need(len(found)<=10,'PROJECT_COUNT_BOUND')
    for path in sorted(found):
        try:
            row=reader['inspect_project'](path)
            row['project']='/mnt/user-data/workspace/'+path.name
            output['projects'].append(row)
        except Exception as exc:
            output['projects'].append({'project':'/mnt/user-data/workspace/'+path.name,'error_class':type(exc).__name__,
                                       'error_code':str(exc) if isinstance(exc,reader['Refused']) else 'STORED_PROJECT_UNAVAILABLE'})
    output.update(project_count=len(found),published_outputs=reader['report_files'](root/'outputs'),bytes_read=reader['BYTES_READ'])
    return output


async def readonly_owner(connection):
    async with connection.transaction(readonly=True,isolation='repeatable_read'):
        need(await connection.fetchval('SHOW transaction_read_only')=='on','READONLY_DATABASE_REQUIRED')
        await connection.execute("SET LOCAL statement_timeout='5000ms'")
        await connection.execute("SET LOCAL lock_timeout='500ms'")
        await connection.execute("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
        return await connection.fetchval(READONLY_SQL,THREAD)


async def observe(mount_metadata,mode='inspect',expected_scope=None):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'energy-persisted-thread-child.v1','status':'INCOMPLETE','thread_id':THREAD,'database_readonly':True,'database_modified':False,
         'database_credentials_used':False,'provider_credentials_requested_separately':False,'application_configuration_read_may_resolve_configured_credentials':True,'credentials_exported':False,'files_modified':False,
         'provider_requests':0,'raw_news_or_messages_exported':False,'application_started':False,'configuration_load_only':True,'sql_owner_reads':0}
    conn=None;stage='userdata_mount_validation'
    try:
        mounts=authorize_mounts(mount_metadata)
        stage='configuration_and_path_resolver_import'
        pg,get_config,paths=runtime_modules()
        config=get_config().database
        need(config.backend=='postgres','CONFIGURED_POSTGRES_REQUIRED')
        url=config.postgres_url.replace('postgresql+asyncpg://','postgresql://',1)
        parsed=urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and bool(parsed.hostname),'CONFIGURED_DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT=parsed.hostname,parsed.port or 5432
        stage='resolve_only_configured_database'
        ALLOW_DB_DNS=True
        addresses=socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM)
        need(0<len(addresses)<=16,'DATABASE_DNS_BOUND')
        DB_ADDRESSES={row[4][0] for row in addresses}
        out['path_resolver_source']=source_fingerprint(paths)
        stage='connect_readonly_database'
        out['database_credentials_used']=True
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        stage='read_only_named_thread_owner'
        owner=await readonly_owner(conn);out['sql_owner_reads']+=1
        need(isinstance(owner,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',owner),'THREAD_OWNER_REQUIRED')
        root=trusted_root(paths,owner,mounts)
        out.update(owner_fingerprint=digest(owner.encode()),storage_path_bound_to_trusted_owner=True,storage_covered_by_userdata_pvc=True)
        scope={'root':str(root),'owner_fingerprint':out['owner_fingerprint'],'path_resolver_sha256':out['path_resolver_source']['sha256']}
        if mode=='resolve':
            out['trusted_root']=str(root)
        else:
            need(mode=='inspect' and expected_scope==scope,'TRUSTED_OWNER_STORAGE_SCOPE_CHANGED')
            stage='read_only_persisted_thread_files'
            out['stored']=inspect_stored(root)
        stage='stable_owner_readback'
        final_owner=await readonly_owner(conn);out['sql_owner_reads']+=1
        need(final_owner==owner,'THREAD_OWNER_CHANGED_DURING_READ')
        need(source_fingerprint(paths)==out['path_resolver_source'],'PATH_RESOLVER_CHANGED_DURING_READ')
        out.update(status='NAMED_THREAD_OWNER_ROOT_RESOLVED_READONLY' if mode=='resolve' else 'NAMED_THREAD_PERSISTED_MARKET_OBSERVED_NO_CHANGE',owner_stable_readback=True)
    except Exception as exc:
        out.update(last_step=stage,error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PERSISTED_INSPECTION_FAILED_REDACTED')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception as exc:out['database_close_error_class']=type(exc).__name__
        ALLOW_DB_DNS=False;DB_ADDRESSES=set()
    return out


class Discard:
    def write(self,value):return len(value)
    def flush(self):pass


def main():
    sys.dont_write_bytecode=True
    logging.disable(logging.CRITICAL)
    out={'schema':'energy-persisted-thread-child.v1','status':'INCOMPLETE','database_modified':False,'files_modified':False,'provider_requests':0,'credentials_exported':False}
    try:
        signal.signal(signal.SIGALRM,lambda *a:(_ for _ in ()).throw(Refused('CHILD_DEADLINE')));signal.alarm(45)
        sys.addaudithook(audit)
        need(len(sys.argv)==2 and sys.argv[1] in ('resolve','inspect'),'FIXED_CHILD_MODE_REQUIRED')
        raw=sys.stdin.buffer.read(32769);need(len(raw)<=32768,'CHILD_INPUT_BOUND')
        payload=json.loads(raw)
        need(isinstance(payload,dict) and set(payload)==({'mounts'} if sys.argv[1]=='resolve' else {'mounts','scope'}),'CHILD_INPUT_SCHEMA')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(payload['mounts'],sys.argv[1],payload.get('scope')))
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PERSISTED_CHILD_FAILED_REDACTED')
    finally:signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())
