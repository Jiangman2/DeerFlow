"""Read-only credential-injection preflight; no Secret values or task execution."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import signal
from urllib.parse import urlsplit

UID = '9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253'
SPEC = '009352242b1f12bb361b0874aff9ecc6d72b9d4003b4c1e54988f786c6fe7dd2'
SOURCE = 'b5c733bc176da4aa37d34b809e007b4201ff2fff0a35e321eaa322c8652171bb'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']
CHILD = r'''
import ast,contextlib,hashlib,io,json,logging,os,pathlib,stat,sys,types,signal,re,platform,struct
from unittest.mock import patch
out={'status':'INCOMPLETE','application_started':False,'credentials_read':False,'provider_requests':0,'network_requests':0,'files_modified':False,'real_pods_created':0,'production_environment_used':False}
class Refused(Exception):pass
def stop(*args):raise Refused('CHILD_DEADLINE')
signal.signal(signal.SIGALRM,stop);signal.alarm(20)
def need(ok,code):
 if not ok:raise Refused(code)
def audit(event,args):
 if event.startswith('socket.') and event!='socket.gethostname' or event in ('subprocess.Popen','os.system','sqlite3.connect'):
  out['blocked_event']=event
  raise Refused('EXTERNAL_OPERATION_FORBIDDEN')
 if event=='open' and isinstance(args[0],(str,bytes)) and '/var/run/secrets/' in os.fsdecode(args[0]):
  raise Refused('SECRET_READ_FORBIDDEN')
try:
 sys.dont_write_bytecode=True
 p=pathlib.Path('/app/app.py');before=p.stat();raw=p.read_bytes();after=p.stat()
 need(stat.S_ISREG(before.st_mode) and len(raw)<=131072 and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'SOURCE_FILE_CHANGED')
 out['source_bytes']=len(raw);out['source_sha256']=hashlib.sha256(raw).hexdigest()
 need(out['source_sha256']=='b5c733bc176da4aa37d34b809e007b4201ff2fff0a35e321eaa322c8652171bb','SOURCE_REQUIRES_REVIEW')
 tree=ast.parse(raw)
 functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_build_sandbox_env']
 need(len(functions)==1,'ENVIRONMENT_BUILDER_REQUIRED')
 sys.addaudithook(audit);logging.disable(logging.CRITICAL)
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
  # Some dependency versions ask an external `file` command for pointer size.
  # Constructor-only tests can use the current interpreter's actual bitness.
  with patch.object(platform,'architecture',return_value=(str(struct.calcsize('P')*8)+'bit','')),patch.object(platform,'system',return_value='Windows' if os.name=='nt' else os.uname().sysname):
   from kubernetes import client as k8s_client
 out['synthetic_import_pointer_size_used']=True
 fake={'EXISTING_SETTING':'unchanged','ENERGY_DAILY_HTTP_PROXY':'http://proxy.invalid:8080'}
 g={'k8s_client':k8s_client,'os':types.SimpleNamespace(environ=fake),'SANDBOX_ENV_SECRET_NAME':'synthetic-secret',
    'SANDBOX_SECRET_ENV_KEYS':('EXISTING_KEY','PPT_MASTER_RESPONSES_RELAY_KEY'),
    'PPT_MASTER_AGENT_ONLY_SECRET_ENV_KEYS':frozenset(['PPT_MASTER_RESPONSES_RELAY_KEY']),
    'SANDBOX_PASSTHROUGH_ENV_KEYS':('EXISTING_SETTING',),'SANDBOX_CA_SECRET_NAME':'',
    'SANDBOX_CA_FILE':'/synthetic/ca','SANDBOX_OPENSSL_CONFIGMAP_NAME':'','SANDBOX_OPENSSL_CONFIG_FILE':'/synthetic/openssl'}
 exec(compile(ast.Module(body=functions,type_ignores=[]),'<fixed-synthetic-env>','exec'),g)
 baseline=[x.to_dict() for x in g['_build_sandbox_env']()]
 g['SANDBOX_SECRET_ENV_KEYS']+=('JKX_API_URL','JKX_API_PASSWORD')
 g['SANDBOX_PASSTHROUGH_ENV_KEYS']+=('ENERGY_DAILY_HTTP_PROXY',)
 candidate=[x.to_dict() for x in g['_build_sandbox_env']()]
 additions=[x for x in candidate if x['name'] in ('JKX_API_URL','JKX_API_PASSWORD','ENERGY_DAILY_HTTP_PROXY')]
 need([x for x in candidate if x not in additions]==baseline,'UNRELATED_ENVIRONMENT_CHANGED')
 need(len(additions)==3,'INJECTION_NOT_SUPPORTED')
 for row in additions:
  if row['name'].startswith('JKX_'):
   need(row.get('value') is None and row['value_from']['secret_key_ref']['name']=='synthetic-secret' and row['value_from']['secret_key_ref']['key']==row['name'],'SECRET_REFERENCE_INVALID')
 need(not any(x['name']=='PPT_MASTER_RESPONSES_RELAY_KEY' for x in candidate),'AGENT_SECRET_LEAKED')
 out.update(status='SYNTHETIC_SECRET_ENV_INJECTION_ACCEPTED',unrelated_entries_preserved=True,new_secret_references=2,new_proxy_entry=1,agent_only_secret_excluded=True,kubernetes_requests=0)
except Refused as exc:out['error_code']=str(exc)
except Exception as exc:
 out.update(error_class=type(exc).__name__,error_code='SOURCE_INSPECTION_FAILED')
 if isinstance(exc,ModuleNotFoundError) and isinstance(exc.name,str) and re.fullmatch('[A-Za-z0-9_.]{1,128}',exc.name):out['missing_module']=exc.name
finally:signal.alarm(0)
print(json.dumps(out,sort_keys=True));sys.exit(int(out['status']=='INCOMPLETE'))
'''


class Refused(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def key_list(value):
    need(isinstance(value, str) and len(value) <= 8192, 'ENVIRONMENT_LIST_INVALID')
    keys = [part.strip() for part in value.split(',') if part.strip()]
    need(all(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', key) for key in keys), 'ENVIRONMENT_LIST_INVALID')
    return keys


def proxy_summary(value):
    need(isinstance(value, str), 'PROXY_SETTING_UNAVAILABLE')
    parsed = urlsplit(value)
    need(parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.query and not parsed.fragment, 'PROXY_SETTING_INVALID')
    return {'scheme': parsed.scheme, 'host': parsed.hostname, 'port': parsed.port, 'authentication_present': bool(parsed.username or parsed.password)}


def run_query(args, child=False):
    # No shell and no mutating kubectl verb is accepted.
    fixed = [['get', 'deployment', name, '-o', 'json'] for name in ('provisioner', 'energy-market-collector')]
    fixed += [['get', 'pods', '-l', 'app=provisioner', '-o', 'json']]
    dynamic = len(args) == 5 and args[0] == 'get' and args[1] in ('pod', 'replicaset') and re.fullmatch('provisioner-[a-z0-9-]+', args[2]) and args[3:] == ['-o', 'json']
    execute = child and len(args) == 10 and args[0] == 'exec' and re.fullmatch('provisioner-[a-z0-9-]+', args[1]) and args[2:] == ['-c', 'provisioner', '--', 'python3', '-I', '-B', '-c', CHILD]
    need(args in fixed or dynamic or execute, 'READONLY_COMMAND_REQUIRED')
    limit = 65536 if child else 1048576
    with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(KUBE + args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                   env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, start_new_session=os.name=='posix')
        until=time.monotonic()+(35 if child else 25)
        try:
            while process.poll() is None:
                need(os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=65536,'READ_COMMAND_OUTPUT_BOUND')
                need(time.monotonic()<until,'READ_COMMAND_TIMEOUT')
                time.sleep(.05)
            need(process.returncode in ((0,1) if child else (0,)) and os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=65536,'READ_COMMAND_FAILED_OR_OVERSIZE')
            output.seek(0)
            return json.loads(output.read(limit+1))
        finally:
            if process.poll() is None:
                if os.name=='posix':
                    os.killpg(process.pid,signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args(argv)
    out = {'schema': 'energy-portable-runtime-preflight.v2', 'status': 'INCOMPLETE', 'workload_modified': False,
           'secret_api_reads': 0, 'credentials_exported': False, 'provider_requests': 0, 'work_messages_sent': 0}
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        deployment = run_query(['get', 'deployment', 'provisioner', '-o', 'json'])
        need(deployment['metadata']['uid'] == UID and deployment['metadata']['generation'] == 86 and sha(deployment['spec']) == SPEC, 'PROVISIONER_BASELINE_CHANGED')
        env = deployment['spec']['template']['spec']['containers'][0].get('env', [])
        settings = {row['name']: row.get('value') for row in env}
        name = settings.get('SANDBOX_ENV_SECRET_NAME')
        need(isinstance(name, str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,251}', name), 'SANDBOX_SECRET_NAME_UNAVAILABLE')
        out['sandbox_environment'] = {'secret_name': name, 'secret_keys': key_list(settings.get('SANDBOX_SECRET_ENV_KEYS')),
                                      'passthrough_keys': key_list(settings.get('SANDBOX_PASSTHROUGH_ENV_KEYS'))}
        collector = run_query(['get', 'deployment', 'energy-market-collector', '-o', 'json'])
        proxies = [row.get('value') for row in collector['spec']['template']['spec']['containers'][0].get('env', []) if row['name'] == 'ENERGY_MARKET_HTTP_PROXY']
        need(len(proxies) == 1, 'APPROVED_COLLECTOR_PROXY_UNAVAILABLE')
        out['approved_proxy'] = proxy_summary(proxies[0])
        pods = run_query(['get', 'pods', '-l', 'app=provisioner', '-o', 'json'])['items']
        candidates = [p for p in pods if not p['metadata'].get('deletionTimestamp') and p.get('status', {}).get('phase') == 'Running'
                      and any(s.get('ready') is True for s in p['status'].get('containerStatuses', []))]
        need(len(candidates) == 1, 'ONE_READY_PROVISIONER_REQUIRED')
        pod = candidates[0]
        owners = [o for o in pod['metadata'].get('ownerReferences', []) if o.get('controller') and o.get('kind') == 'ReplicaSet']
        need(len(owners) == 1 and re.fullmatch('provisioner-[a-z0-9-]+', owners[0]['name']), 'PROVISIONER_OWNER_REQUIRED')
        rs = run_query(['get', 'replicaset', owners[0]['name'], '-o', 'json'])
        need(rs['metadata']['uid'] == owners[0]['uid'] and any(o.get('controller') and o.get('kind') == 'Deployment' and o.get('uid') == UID for o in rs['metadata'].get('ownerReferences', [])), 'OWNER_CHAIN_CHANGED')
        template = json.loads(json.dumps(rs['spec']['template']))
        template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
        need(template == deployment['spec']['template'], 'CURRENT_TEMPLATE_REQUIRED')
        states = pod['status']['containerStatuses']
        before_runtime = [(s['containerID'], s['imageID'], s['restartCount']) for s in states]
        out['source_qualification'] = run_query(['exec', pod['metadata']['name'], '-c', 'provisioner', '--', 'python3', '-I', '-B', '-c', CHILD], child=True)
        second = run_query(['get', 'pod', pod['metadata']['name'], '-o', 'json'])
        final = run_query(['get', 'deployment', 'provisioner', '-o', 'json'])
        need(second['metadata']['uid'] == pod['metadata']['uid'] and [(s['containerID'], s['imageID'], s['restartCount']) for s in second['status']['containerStatuses']] == before_runtime
             and final['metadata']['uid'] == UID and sha(final['spec']) == SPEC, 'STABLE_RUNTIME_REQUIRED')
        out.update(stable_readback=True, provisioner_generation=86, pod=pod['metadata']['name'], pod_uid=pod['metadata']['uid'])
        need(out['source_qualification'].get('status') == 'SYNTHETIC_SECRET_ENV_INJECTION_ACCEPTED', 'SOURCE_QUALIFICATION_INCOMPLETE')
        out['status'] = 'PORTABLE_RUNTIME_INJECTION_PREFLIGHT_ACCEPTED_NO_CHANGE'
    except Refused as exc:
        out['error_code'] = str(exc)
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code='LOCAL_FAILURE_REDACTED')
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
