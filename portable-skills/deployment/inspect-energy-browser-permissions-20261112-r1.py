"""Observe browser executable permissions in one ordinary sandbox; no browser launch.

Python 3.7+ operator entry. No Secret reads, application imports, network probes,
workspace reads, file writes, process restarts, or Kubernetes mutations.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
COLUMNS = 'NAME:.metadata.name,UID:.metadata.uid,NODE:.spec.nodeName,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="sandbox")].ready,IMAGE:.spec.containers[?(@.name=="sandbox")].image,IMAGE_ID:.status.containerStatuses[?(@.name=="sandbox")].imageID'
LIST = ['get', 'pods', '-l', 'app.kubernetes.io/component=sandbox', '--no-headers', '-o', 'custom-columns=' + COLUMNS]

PERMISSIONS = r'''
import json,os,pathlib,shutil
def observe(path):
 row={'path':str(path)}
 try:
  row.update(present=path.is_file(),readable=os.access(path,os.R_OK),executable=os.access(path,os.X_OK))
 except OSError as exc:row['error_class']=type(exc).__name__
 return row
home=pathlib.Path.home()
out={'uid':os.geteuid(),'gid':os.getegid(),'browsers':[],'caches':[]}
for base in [home/'.cache/ms-playwright',pathlib.Path('/opt/ms-playwright'),pathlib.Path('/root/.cache/ms-playwright')]:
 row={'path':str(base)}
 try:
  row['directory']=base.is_dir()
  if row['directory']:
   for pattern in ['chromium_headless_shell-*/chrome-*/headless_shell*','chromium_headless_shell-*/chrome-*/chrome-headless-shell*']:
    for path in sorted(base.glob(pattern),reverse=True)[:4]:out['browsers'].append(observe(path))
 except OSError as exc:row['error_class']=type(exc).__name__
 out['caches'].append(row)
for name in ['chromium','chromium-browser','google-chrome']:
 path=shutil.which(name)
 if path:out['browsers'].append(observe(pathlib.Path(path)))
out.update(files_modified=False,browser_launched=False,credentials_read=False)
print(json.dumps(out,sort_keys=True))
'''

CHILD = r'''
import json,os,pathlib,pwd,subprocess,sys
code=sys.argv[1]
out={'schema':'energy-browser-permission-child.v1','files_modified':False,'credentials_read':False,'browser_launched':False,'observations':[]}
try:
 targets=[(os.geteuid(),os.getegid(),pwd.getpwuid(os.geteuid()).pw_dir,'kubectl_exec_user')]
 if os.geteuid()==0:
  users=set()
  # Read only process name and numeric UID; never command lines or environments.
  for path in list(pathlib.Path('/proc').iterdir())[:2048]:
   if not path.name.isdigit():continue
   try:
    if (path/'comm').read_text().strip() not in ('bash','sh'):continue
    line=next(line for line in (path/'status').read_text().splitlines() if line.startswith('Uid:'))
    uid=int(line.split()[2])
    if uid!=0:users.add(uid)
   except (OSError,StopIteration,ValueError):continue
  out['nonroot_shell_uids']=sorted(users)
  if len(users)==1:
   account=pwd.getpwuid(next(iter(users)));basis='observed_nonroot_shell_user'
  else:
   try:account=pwd.getpwnam('gem');basis='image_gem_account_not_task_identity_proof'
   except KeyError:account=None
  if account and account.pw_uid!=0:targets.append((account.pw_uid,account.pw_gid,account.pw_dir,basis))
 for uid,gid,home,basis in targets:
  env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':home,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'}
  def demote():
   os.setgroups([]);os.setgid(gid);os.setuid(uid)
  try:
   result=subprocess.run([sys.executable,'-I','-B','-c',code],cwd='/tmp',env=env,preexec_fn=demote if uid!=os.geteuid() else None,
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
   if result.returncode or len(result.stdout)>32768:raise ValueError('PERMISSION_CHILD_FAILED')
   row=json.loads(result.stdout);row['identity_basis']=basis;out['observations'].append(row)
  except Exception as exc:out['observations'].append({'uid':uid,'identity_basis':basis,'error_class':type(exc).__name__})
 out['status']='BROWSER_PERMISSIONS_OBSERVED_NOT_RENDER_QUALIFIED'
except Exception as exc:out.update(status='INCOMPLETE',error_class=type(exc).__name__)
print(json.dumps(out,sort_keys=True))
'''


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def command(args):
    # Fixed read/exec arguments only. No tool path can patch/delete resources.
    need(args == LIST or (len(args) == 12 and args[:2] == ['exec', '-i'] and re.fullmatch('sandbox-[a-z0-9-]+', args[2])
                         and args[3:] == ['-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', CHILD, PERMISSIONS]), 'COMMAND_SCOPE')
    result = subprocess.run(KUBE + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35,
                            env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
    need(result.returncode == 0 and len(result.stdout) <= 262144 and len(result.stderr) <= 65536, 'READ_COMMAND_FAILED')
    return result.stdout.decode('utf-8')


def rows(raw):
    result = []
    for line in raw.splitlines():
        columns = line.split()
        need(len(columns) == 8, 'POD_LIST_SHAPE')
        name, uid, node, phase, deleting, ready, image, image_id = columns
        need(re.fullmatch('sandbox-[a-z0-9-]+', name) and re.fullmatch('[0-9a-f-]{36}', uid), 'POD_IDENTITY')
        if phase == 'Running' and deleting == '<none>' and ready == 'true' and image == IMAGE and image_id == IMAGE_ID:
            result.append(columns)
    return sorted(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', required=True, action='store_true')
    parser.parse_args()
    out = {'schema':'energy-browser-permissions.v1','status':'INCOMPLETE','files_modified':False,'workload_modified':False,
           'credentials_read':False,'secret_api_reads':0,'provider_requests':0,'browser_launched':False,'queries':0}
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        before = rows(command(LIST));out['queries'] += 1
        need(bool(before), 'READY_ORDINARY_SANDBOX_REQUIRED')
        chosen = before[0]
        args = ['exec', '-i', chosen[0], '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', CHILD, PERMISSIONS]
        out['observation'] = json.loads(command(args));out['queries'] += 1
        after = rows(command(LIST));out['queries'] += 1
        need(chosen in after, 'SAMPLED_SANDBOX_CHANGED')
        out.update(status='BROWSER_PERMISSIONS_OBSERVED_NOT_RENDER_QUALIFIED',sample={'pod':chosen[0],'uid':chosen[1],'node':chosen[2]},stable_readback=True)
    except Exception as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'INSPECTION_FAILED_REDACTED')
    out['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(out,sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
