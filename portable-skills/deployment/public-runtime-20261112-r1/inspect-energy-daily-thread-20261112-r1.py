"""Read only one named energy-daily thread's stored inputs and mount metadata.

Python3.7+ operator; child uses only Python standard library. No provider calls,
Secret reads, model calls, browser launches, package installs or file writes.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

THREAD='201fb3d5-49c0-4276-9e3f-3db67f8edfe7'
CLAIM='deer-flow-skills-rwx-pvc'
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
COLUMNS='NAME:.metadata.name,UID:.metadata.uid,THREAD:.metadata.annotations.deerflow\\.io/thread-id,NODE:.spec.nodeName,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="sandbox")].ready'
LIST=['get','pods','-l','app.kubernetes.io/component=sandbox','--no-headers','-o','custom-columns='+COLUMNS]

CHILD=r'''
import datetime,hashlib,json,math,os,pathlib,re,signal,stat,sys
ROOT=pathlib.Path('/mnt/user-data/workspace')
OUTPUTS=pathlib.Path('/mnt/user-data/outputs')
SKILL=pathlib.Path('/mnt/skills/public/energy-market-daily')
RUNTIME=pathlib.Path('/mnt/skills/.energy-browser-runtime-20261112-r1/linux-arm64')
CODES=('LD0221','XA0008','NY0121','NY0421','XA0942','LD1221','LPGD59','LPGD60','LPG1225','LPG1224','LPG1179','LPG1177','LPG1227','LPG1226')
HOLIDAYS={'周六','周日','休市','节假日','假日'}
MISSING={'','-','--','暂无','暂无数据','未更新'}
BYTES_READ=0

class Refused(ValueError):pass
def need(ok,code):
 if not ok:raise Refused(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def stamp(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def no_links(path):
 for part in (path,)+tuple(path.parents):
  try:s=part.lstat()
  except FileNotFoundError:continue
  need(not stat.S_ISLNK(s.st_mode),'PATH_LINK_REFUSED')
def read(path,maximum=8*1024*1024):
 global BYTES_READ
 no_links(path)
 fd=os.open(str(path),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 try:
  first=os.fstat(fd)
  need(stat.S_ISREG(first.st_mode) and first.st_nlink==1 and 0<first.st_size<=maximum,'STORED_FILE_BOUND')
  BYTES_READ+=first.st_size;need(BYTES_READ<=64*1024*1024,'TOTAL_READ_BOUND')
  with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(maximum+1)
  need(len(raw)==first.st_size and stamp(first)==stamp(os.fstat(fd))==stamp(path.stat()),'STORED_FILE_CHANGED')
  return raw
 finally:os.close(fd)
def json_file(path,maximum=8*1024*1024):
 raw=read(path,maximum);return json.loads(raw.decode('utf-8-sig')),raw
def metadata(path):
 row={'path':str(path)}
 try:
  no_links(path);s=path.lstat()
  row.update(present=True,directory=stat.S_ISDIR(s.st_mode),regular=stat.S_ISREG(s.st_mode),readable=os.access(str(path),os.R_OK))
 except FileNotFoundError:row['present']=False
 except Exception as exc:row['error_class']=type(exc).__name__
 return row
def classify(value):
 raw=str(value).strip()
 try:
  number=float(raw)
  return 'numeric_positive' if math.isfinite(number) and number>0 else 'invalid_numeric'
 except (ValueError,TypeError):
  if raw in HOLIDAYS:return 'explicit_nontrading'
  if raw in MISSING:return 'recognized_missing'
  return 'unrecognized'
def date(value):
 need(isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value),'STORED_DATE_INVALID')
 need(datetime.date.fromisoformat(value).isoformat()==value,'STORED_DATE_INVALID');return value
def safe_time(value):
 if not isinstance(value,str) or not re.fullmatch(r'[0-9T:+.Z -]{10,64}',value):return None
 return value
def report_files(root):
 no_links(root)
 if not root.exists():return {'present':False,'files':[]}
 need(root.is_dir(),'OUTPUT_DIRECTORY_TYPE')
 found=[];scanned=0;more=False
 with os.scandir(str(root)) as entries:
  for entry in entries:
   scanned+=1;need(scanned<=2048,'OUTPUT_ENTRY_BOUND')
   if not ('日报' in entry.name or 'energy-daily' in entry.name.lower() or 'energy_daily' in entry.name.lower()):continue
   path=root/entry.name
   if path.suffix.lower() not in ('.png','.html','.json','.txt'):continue
   need(not entry.is_symlink(),'OUTPUT_LINK_REFUSED')
   if not entry.is_file(follow_symlinks=False):continue
   if len(found)>=32:more=True;break
   state=path.stat();row={'name':entry.name,'bytes':state.st_size}
   if 0<state.st_size<=4*1024*1024 and BYTES_READ+state.st_size<=60*1024*1024:
    row['sha256']=sha(read(path,4*1024*1024))
   found.append(row)
 return {'present':True,'files':sorted(found,key=lambda row:row['name']),'additional_matching_files':more,'file_bodies_exported':False}
def summarize_cache(payload,target):
 need(isinstance(payload,dict) and isinstance(payload.get('data'),list) and len(payload['data'])<=2048,'CACHE_SCHEMA_BOUND')
 output=[]
 for code in CODES:
  found=[item for item in payload['data'] if isinstance(item,dict) and item.get('code_name')==code]
  matches=[str(item.get('value_date','')).replace('/','-')==target for item in found]
  output.append({'code':code,'rows':len(found),'date_matches':bool(found) and all(matches),
                 'close_classes':sorted(set(classify(item.get('close')) for item in found))})
 return {'provider_errcode_zero':str(payload.get('errcode'))=='0','row_count':len(payload['data']),'series':output}
def summarize_market(market,target):
 need(isinstance(market,dict),'MARKET_SCHEMA_INVALID')
 quotes=[item for kind in ('oil','gas','freight') for item in market.get(kind,[]) if isinstance(item,dict)]
 need(len(quotes)<=64,'MARKET_QUOTE_BOUND')
 observations=market.get('observations',[])
 need(isinstance(observations,list) and len(observations)<=4096,'MARKET_OBSERVATION_BOUND')
 result=[]
 for code in CODES:
  current=[item for item in quotes if item.get('code')==code]
  rows=[item for item in observations if isinstance(item,dict) and item.get('code')==code and item.get('date')==target]
  result.append({'code':code,'quote_count':len(current),'quote_statuses':sorted(set(item.get('status') if item.get('status') in ('available','non_trading','missing') else 'unrecognized' for item in current)),
                 'quote_value_classes':sorted(set(classify(item.get('value')) for item in current)),
                 'target_observation_count':len(rows),'target_raw_close_classes':sorted(set(classify(item.get('raw_close')) for item in rows))})
 failures=market.get('acquisition_failures',{})
 failure=failures.get(target) if isinstance(failures,dict) else None
 return {'data_date':market.get('data_date') if market.get('data_date')==target else 'MISMATCH',
         'retrieved_at':safe_time(market.get('retrieved_at')),'market_complete':market.get('market_complete') is True,
         'provider_requests_stored':market.get('provider_requests') if type(market.get('provider_requests')) is int else None,
         'historical_or_target_failure_count':len(failures) if isinstance(failures,dict) else None,
         'target_failure_code':failure if isinstance(failure,str) and re.fullmatch('[A-Z0-9_]{1,100}',failure) else None,'series':result}
def inspect_project(project):
 need(project.parent==ROOT and re.fullmatch(r'energy-daily-[A-Za-z0-9_-]{1,120}',project.name),'PROJECT_SCOPE')
 no_links(project);need(project.is_dir(),'PROJECT_DIRECTORY_REQUIRED')
 state,state_raw=json_file(project/'PROJECT.json',32768)
 need(isinstance(state,dict),'PROJECT_SCHEMA_INVALID')
 target=date(state.get('data_date'));publication=date(state.get('publication_date'))
 market,market_raw=json_file(project/'market.json')
 out={'project':str(project),'publication_date':publication,'data_date':target,'publication_minus_one_matches_target':(datetime.date.fromisoformat(publication)-datetime.timedelta(days=1)).isoformat()==target,
      'prepared_at':safe_time(state.get('prepared_at')),'project_manifest_sha256':sha(state_raw),'market_sha256':sha(market_raw),
      'market_matches_project_input_hash':isinstance(state.get('input_hashes'),dict) and state['input_hashes'].get('market')==sha(market_raw),
      'repeated_prepare_reuses_stored_project':True,'market':summarize_market(market,target)}
 cache_path=project/'cache/jkx'/('jkx_new_'+target+'.json')
 try:
  cache,cache_raw=json_file(cache_path)
  out['target_cache']=dict(summarize_cache(cache,target),present=True,sha256=sha(cache_raw))
 except FileNotFoundError:
  cache_raw=None;out['target_cache']={'present':False,'external_cache_not_inspected':True}
 need(read(project/'PROJECT.json',32768)==state_raw and read(project/'market.json')==market_raw,'PROJECT_FILES_CHANGED')
 if cache_raw is not None:need(read(cache_path)==cache_raw,'PROJECT_FILES_CHANGED')
 out['stable_readback']=True
 out['project_outputs']=report_files(project/'outputs')
 return out
def inspect_browser():
 out={'official_skill':metadata(SKILL),'outside_public_runtime':metadata(RUNTIME),'outside_public_runtime_manifest':metadata(RUNTIME/'RUNTIME.json'),
      'public_relative_runtime':metadata(SKILL.parent/'.energy-browser-runtime-20261112-r1/linux-arm64'),'live_runtime_binary_files_read':0}
 path=SKILL/'assets/browser-runtime.json'
 try:
  value,raw=json_file(path,32768)
  out['binding']={'present':True,'sha256':sha(raw),'expected_schema':isinstance(value,dict) and value.get('schema')=='energy-browser-runtime-binding.v1',
                  'expected_directory':isinstance(value,dict) and value.get('directory')=='../../.energy-browser-runtime-20261112-r1/linux-arm64'}
 except FileNotFoundError:out['binding']={'present':False}
 return out
def main():
 out={'schema':'energy-fixed-thread-inspection-child.v1','status':'INCOMPLETE','credentials_read':False,'provider_requests':0,'files_modified':False,'raw_news_or_messages_exported':False}
 try:
  signal.signal(signal.SIGALRM,lambda *a:(_ for _ in ()).throw(Refused('CHILD_TIME_BOUND')));signal.alarm(20)
  out.update(exec_uid=os.geteuid(),task_user_impersonated=False,browser=inspect_browser())
  no_links(ROOT);need(ROOT.is_dir(),'WORKSPACE_DIRECTORY_REQUIRED')
  candidates=[];scanned=0
  with os.scandir(str(ROOT)) as entries:
   for entry in entries:
    scanned+=1;need(scanned<=2048,'WORKSPACE_ENTRY_BOUND')
    if not re.fullmatch(r'energy-daily-[A-Za-z0-9_-]{1,120}',entry.name):continue
    need(not entry.is_symlink(),'PROJECT_LINK_REFUSED')
    if entry.is_dir(follow_symlinks=False):candidates.append(ROOT/entry.name)
  need(len(candidates)<=10,'PROJECT_COUNT_BOUND')
  out['projects']=[]
  for path in sorted(candidates):
   try:out['projects'].append(inspect_project(path))
   except Exception as exc:out['projects'].append({'project':str(path),'error_class':type(exc).__name__,'error_code':str(exc) if isinstance(exc,Refused) else 'PROJECT_INSPECTION_FAILED_REDACTED'})
  out.update(status='NAMED_THREAD_STORED_MARKET_AND_BROWSER_OBSERVED_NO_CHANGE',project_count=len(candidates),thread_outputs=report_files(OUTPUTS),bytes_read=BYTES_READ)
 except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'THREAD_INSPECTION_FAILED_REDACTED')
 finally:signal.alarm(0)
 print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')
if __name__=='__main__':sys.exit(main())
'''


class Refused(ValueError):pass


def need(ok,code):
    if not ok:raise Refused(code)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def candidates(raw):
    need(isinstance(raw,str) and len(raw)<=262144,'POD_LIST_BOUND')
    selected=[]
    for line in raw.splitlines():
        parts=line.split();need(len(parts)==7,'POD_LIST_SHAPE')
        name,uid,thread,node,phase,deleting,ready=parts
        if thread!=THREAD:continue
        need(re.fullmatch(r'sandbox-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'NAMED_POD_IDENTITY')
        if phase=='Running' and deleting=='<none>' and ready=='true':selected.append(parts)
    need(len(selected)<=1,'NAMED_THREAD_POD_AMBIGUOUS')
    return selected


def pod_identity(pod,chosen):
    meta,spec,status=pod['metadata'],pod['spec'],pod.get('status',{})
    need(meta.get('name')==chosen[0] and meta.get('uid')==chosen[1] and meta.get('annotations',{}).get('deerflow.io/thread-id')==THREAD
         and not meta.get('deletionTimestamp') and spec.get('nodeName')==chosen[3],'NAMED_THREAD_POD_CHANGED')
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
    need(len(states)==1 and states[0].get('ready') is True and status.get('phase')=='Running','NAMED_THREAD_POD_NOT_READY')
    state=states[0]
    identity={'pod':chosen[0],'uid':chosen[1],'node':chosen[3],'spec_sha256':digest(canonical(spec)),
              'container_id_sha256':digest(str(state.get('containerID','')).encode()),'image_id_sha256':digest(str(state.get('imageID','')).encode()),
              'restart_count':state.get('restartCount')}
    volumes={row['name']:row for row in spec.get('volumes',[])}
    mains=[row for row in spec.get('containers',[]) if row.get('name')=='sandbox'];need(len(mains)==1,'ONE_SANDBOX_CONTAINER_REQUIRED')
    mounts=[]
    for mount in mains[0].get('volumeMounts',[]):
        volume=volumes.get(mount.get('name'),{})
        if volume.get('persistentVolumeClaim',{}).get('claimName')==CLAIM:
            mounts.append({'mount_path':mount.get('mountPath'),'subpath':mount.get('subPath'),'read_only':mount.get('readOnly') is True,'claim':CLAIM})
    return identity,mounts


class Operation:
    def __init__(self):
        self.pod=None;self.queries=0

    def command(self,args):
        allowed=args==LIST
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec','-i',self.pod,'-c','sandbox','--','python3','-I','-B','-c',CHILD]
        need(allowed,'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<5,'QUERY_BOUND');self.queries+=1
        prefix=list(KUBE);timeout=45 if args[0]=='exec' else 25
        if args[0]=='exec':prefix[2]='--request-timeout=40s'
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(prefix+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                                     env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=2097152 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(2097153).decode('utf-8')
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    operation=Operation()
    out={'schema':'energy-fixed-thread-inspection.v1','thread_id':THREAD,'status':'INCOMPLETE','workload_modified':False,'files_modified':False,
         'credentials_read':False,'secret_api_reads':0,'provider_requests':0,'database_access':False,'raw_news_or_messages_exported':False}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        selected=candidates(operation.command(LIST))
        if not selected:
            out['status']='THREAD_SANDBOX_NOT_PRESENT'
        else:
            chosen=selected[0];operation.pod=chosen[0]
            before,mounts=pod_identity(json.loads(operation.command(['get','pod',chosen[0],'-o','json'])),chosen)
            out.update(sample=before,skills_mounts=mounts)
            observed=json.loads(operation.command(['exec','-i',chosen[0],'-c','sandbox','--','python3','-I','-B','-c',CHILD]))
            out['observation']=observed
            after,_=pod_identity(json.loads(operation.command(['get','pod',chosen[0],'-o','json'])),chosen)
            need(before==after,'NAMED_THREAD_RUNTIME_CHANGED')
            out['stable_readback']=True
            need(observed.get('status')=='NAMED_THREAD_STORED_MARKET_AND_BROWSER_OBSERVED_NO_CHANGE','CHILD_INSPECTION_INCOMPLETE')
            out['status']='NAMED_THREAD_STORED_MARKET_AND_BROWSER_OBSERVED_NO_CHANGE'
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'INSPECTION_FAILED_REDACTED')
    out.update(queries=operation.queries,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())
