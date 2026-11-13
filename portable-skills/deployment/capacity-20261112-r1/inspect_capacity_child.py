"""Only fixed Provisioner source AST and resource/node-pool environment inputs."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

KEYS=('SANDBOX_NODE_POOL','SANDBOX_CPU_REQUEST','SANDBOX_MEMORY_REQUEST','SANDBOX_CPU_LIMIT','SANDBOX_MEMORY_LIMIT',
      'SANDBOX_EPHEMERAL_STORAGE_REQUEST','SANDBOX_EPHEMERAL_STORAGE_LIMIT','PPT_MASTER_AGENT_HOST_ENABLED',
      'PPT_MASTER_AGENT_HOST_CPU_REQUEST','PPT_MASTER_AGENT_HOST_MEMORY_REQUEST','PPT_MASTER_AGENT_HOST_CPU_LIMIT','PPT_MASTER_AGENT_HOST_MEMORY_LIMIT')
NAMES={'sandbox','ppt-master-agent-host','prepare-user-data','verify-skill-mounts'}
RESOURCES={'cpu','memory','ephemeral-storage'}

def safe_input(key,value):
    if value is None:return None
    if not isinstance(value,str) or len(value)>4096:raise ValueError('RESOURCE_ENV_SHAPE')
    if key=='SANDBOX_NODE_POOL':
        if value and not all(re.fullmatch('[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',part.strip()) for part in value.split(',')):raise ValueError('NODE_POOL_SHAPE')
    elif key=='PPT_MASTER_AGENT_HOST_ENABLED':
        if value.strip().lower() not in ('true','false','1','0','yes','no','on','off',''):raise ValueError('BOOLEAN_ENV_SHAPE')
    elif not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:m|Ki|Mi|Gi|Ti|K|M|G|T)?',value):raise ValueError('RESOURCE_ENV_QUANTITY')
    return value

def scalar(node,resource=False):
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,int,float,bool,type(None))):
        if resource:
            if isinstance(node.value,bool) or node.value is None:return {'kind':'unknown_expression'}
            safe_input('SANDBOX_CPU_REQUEST',str(node.value))
        return {'kind':'literal','value':node.value}
    if isinstance(node,ast.Name) and node.id in KEYS:return {'kind':'resource_variable','name':node.id}
    return {'kind':'unknown_expression'}

def source_summary(raw,environment):
    tree=ast.parse(raw);defaults={};containers=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='get' and isinstance(node.func.value,ast.Attribute) and isinstance(node.func.value.value,ast.Name) and node.func.value.value.id=='os' and node.func.value.attr=='environ':
            key=node.args[0].value if node.args and isinstance(node.args[0],ast.Constant) else None
            if key in KEYS:
                value=scalar(node.args[1]) if len(node.args)>1 else {'kind':'literal','value':None}
                if value.get('kind')=='literal' and isinstance(value.get('value'),str):value['value']=safe_input(key,value['value'])
                defaults.setdefault(key,[]).append(value)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='V1Container':
            keywords={item.arg:item.value for item in node.keywords if item.arg}
            name=keywords.get('name');name=name.value if isinstance(name,ast.Constant) else None
            if name not in NAMES:continue
            result={'container':name};resources=keywords.get('resources')
            if isinstance(resources,ast.Call):
                for item in resources.keywords:
                    if item.arg not in ('requests','limits') or not isinstance(item.value,ast.Dict):continue
                    result[item.arg]={key.value:scalar(value,resource=True) for key,value in zip(item.value.keys,item.value.values) if isinstance(key,ast.Constant) and key.value in RESOURCES}
            containers.append(result)
    return {'environment_inputs':{key:{'present':key in environment,'value':safe_input(key,environment.get(key)),'source_defaults':defaults.get(key,[])} for key in KEYS},
        'container_resource_expressions':containers,'defaults_basis':'AST_LITERAL_ONLY_NO_APPLICATION_IMPORT_OR_TEMPLATE_EXECUTION'}

def main():
    sys.dont_write_bytecode=True;out={'schema':'sandbox-capacity-provisioner-source.v1','status':'INCOMPLETE','files_modified':False,'credentials_exported':False,'secret_api_reads':0,'application_imported':False,'provider_requests':0}
    try:
        path=Path('/app/app.py');fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size>131072:raise ValueError('SOURCE_SIZE_OR_TYPE')
            raw=stream.read(131073);after=os.fstat(stream.fileno())
        signature=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
        if len(raw)>131072 or signature(before)!=signature(after) or signature(path.lstat())!=signature(after):raise ValueError('SOURCE_CHANGED')
        out.update(source_sha256=hashlib.sha256(raw).hexdigest(),source_bytes=len(raw),**source_summary(raw,{key:os.environ[key] for key in KEYS if key in os.environ}),status='PROVISIONER_RESOURCE_INPUTS_OBSERVED_NO_CHANGE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,ValueError) else 'SOURCE_OBSERVATION_FAILED_REDACTED')
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
