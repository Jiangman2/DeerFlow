"""Fresh configuration only: do not instantiate a sandbox provider or its cleanup."""
import contextlib
import json
import logging
import math
import os
import signal
import sys

class Refused(ValueError):pass

def audit(event,args):
    if event=='open':
        mode=args[1] if len(args)>1 else None;flags=args[2] if len(args)>2 else 0
        if isinstance(mode,str) and any(token in mode for token in ('w','a','x','+')) or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):raise Refused('READONLY_WRITE_BLOCKED')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.chown','os.utime','subprocess.Popen','os.system','sqlite3.connect') or event.startswith('socket.'):
        raise Refused('READONLY_OPERATION_BLOCKED')

def configured_policy(config):
    cfg=getattr(config,'sandbox',None);values={}
    for key in ('replicas','idle_timeout','startup_timeout'):
        value=getattr(cfg,key,None)
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>10000000):raise Refused('POLICY_NUMERIC_SHAPE')
        values[key]=value
    return values

def load_policy():
    from deerflow.config import get_app_config
    return configured_policy(get_app_config())

class Discard:
    def write(self,value):return len(value)
    def flush(self):pass

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'sandbox-retention-fresh-config.v1','status':'INCOMPLETE','files_modified':False,'credentials_exported':False,'database_access':False,
        'provider_requests':0,'network_requests':0,'application_configuration_read_may_resolve_configured_credentials':True,
        'provider_instantiation_requested':False,'cleanup_requested':False,'lease_files_read_or_acquired':False,
        'basis':'FRESH_CONFIG_ONLY_NOT_LOADED_PROVIDER_INSTANCE_STATE','unset_values_remain_unknown_no_defaults_inferred':True}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Refused('CONFIG_READ_DEADLINE')));signal.alarm(20)
        sys.addaudithook(audit)
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):values=load_policy()
        out.update(configured_policy=values,status='SANDBOX_RETENTION_CONFIGURATION_OBSERVED_NO_CHANGE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'CONFIG_READ_FAILURE_REDACTED')
    finally:signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
