#!/usr/bin/env python3
"""Read-only report-label update preflight; current project-type Skill, Python 3.7+, no writes."""
import argparse, copy, hashlib, json, os, re, socket, stat, subprocess, sys, threading, time
BASE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
DEADLINE=300
MAX_COMMANDS=80
COUNTER=0
STARTED=0
LAST_STEP='init'
EXPECTED_GATEWAY_NODES=('ecs-zdz-pro-0018','ecs-zdz-pro-0019','ecs-zdz-pro-0020','ecs-zdz-pro-0021','ecs-zdz-pro-0022')
PRIOR_RELEASE={'path':'/var/lib/portal-research-fund-skill-update-project-type-20261105-r1/REPORT.json','bytes':7284,'sha256':'c153ad6df9d7d14d2bf65478d0b998dba3aa9825ec055e54612b71053788dc58','operation':'bbccb30646cf4a7ab2ef30ba506f8318'}
EXPECTED_PVC={'name':'deer-flow-skills-rwx-pvc','uid':'5bd3d4ed-54df-45c7-8954-d32ff8e53ec9','volume':'deerflow-skills-pv-nfs-0000','pv_uid':'a9292380-b804-4952-a088-9d6447b4bb2d'}
EXPECTED_SKILL={'bytes': 795808, 'file_count': 30, 'files': [{'bytes': 12471, 'path': 'SKILL.md', 'sha256': 'c4f29d94a8b0c9768a08272bd959657b3ece00ae8f14137c948585048890f823'}, {'bytes': 281899, 'path': 'assets/reference-examples/field-rules-demo.xlsx', 'sha256': '4c67f86102bfe0baf3cc27d4476aaabe7d8c6842bad5c92f4d872e94eab173f8'}, {'bytes': 83081, 'path': 'assets/reference-examples/group-company-example.docx', 'sha256': '85dc59c86546a68da6d2173de7662ced2769593e5db88d621a0fd7779b846a51'}, {'bytes': 8224, 'path': 'assets/reference-examples/group-company-standard-input.xlsx', 'sha256': '89d04dd9cd7840e70abbc70a4438b1ad4c499df9deb16f954c353a92d4c8a821'}, {'bytes': 34124, 'path': 'assets/reference-examples/miit-example.docx', 'sha256': '34bc5dd3f8f951e6d6647f35f23f384ce95bb00b7a3a02a640a6a93a313a1b62'}, {'bytes': 34107, 'path': 'assets/reference-examples/national-science-example.docx', 'sha256': '57539ce2de776ba81aa9358230e061cb9f97fb2ca441827f6dd6dae98584c883'}, {'bytes': 2117, 'path': 'evals/evals.json', 'sha256': 'bdedcd4761631be2094f3f5b085cee89a26bdca42fa15e4eed1a75ac7ad29565'}, {'bytes': 8254, 'path': 'examples/cases/group-company-complete.json', 'sha256': '1c190cc407eff31aae85c6ef192eac440a5fdc610d512af2166af5e9d8ea4b05'}, {'bytes': 11466, 'path': 'examples/cases/miit-complete.json', 'sha256': 'cdf7b763c3f6afdda3345a77fc0af7c68fe495d5b3628bbd67b3eaecdfddeaeb'}, {'bytes': 7824, 'path': 'examples/cases/national-science-complete.json', 'sha256': '9e98be5a2fb90ff0527ed46fd1b9cf9390a3ea5298d9bb7b531a8d8662422344'}, {'bytes': 8301, 'path': 'references/data-contract.md', 'sha256': 'dc7c65a79769517125fcb183c63caecd6662d141ea9f883e687bceb60202ec31'}, {'bytes': 8493, 'path': 'references/group-company-standard-input.md', 'sha256': 'b23fb015d8a05cb233a6dd38fc813af24c2ffcd09b02dbc58e1e48fd1e86e7de'}, {'bytes': 3815, 'path': 'references/group-company-template-annotations.md', 'sha256': 'f823183012259b3c3f8561da9a9571057c4e247760c9012d1329a10d21056f84'}, {'bytes': 4111, 'path': 'references/group-company.md', 'sha256': '344ba27af54f56033393854424208b4f8764d51609eca063189ad5a66a5b39bf'}, {'bytes': 5516, 'path': 'references/layout-contract.md', 'sha256': '8a41599f087fa20c4f28fd274651cb4e6d87ac1ddbb4eb60e5899210f8a538ee'}, {'bytes': 2300, 'path': 'references/miit.md', 'sha256': '2e5357c3bf3d15e6ae1ee50e3896caca5704f01571b45a26da73cbbbe34ff276'}, {'bytes': 1921, 'path': 'references/national-science.md', 'sha256': '2833f1913cfaddf7b0a69219bb6d2d8d8be16fcd9760db3a534658637f927709'}, {'bytes': 4384, 'path': 'references/profile-rules.json', 'sha256': '8615311c6948b6fb3e7f3a659a7e6e1e51df979bde758c46c9549582c18b0030'}, {'bytes': 31336, 'path': 'references/source-field-catalog.json', 'sha256': '75509114e1b7d3004c0216e2148227ea4e8ffa9338a8ff3b3ea50aefe4f5216e'}, {'bytes': 5559, 'path': 'schemas/case.schema.json', 'sha256': '1ebfaef99c0a771fb16b468ca93ce462227f9827d0a535919c8b1175a068bd28'}, {'bytes': 46732, 'path': 'scripts/docx_builder.py', 'sha256': 'a955447325a5411f440e814182bf3147bb7b82d46c95b276bc8c9e616e6bde13'}, {'bytes': 1987, 'path': 'scripts/extract_reference_catalog.py', 'sha256': 'e83dfe3793908394e10ce77b69e5f053bb6272caea1ac53619b30a8e6250602f'}, {'bytes': 6106, 'path': 'scripts/group_build.py', 'sha256': 'b97ff905817b8edcf7c7e3b62c1451d9fee145df468ff8bb40670300656fbd2f'}, {'bytes': 22443, 'path': 'scripts/group_template.py', 'sha256': 'b7cc8f583165f5039c2870ce60b38cc6fced3aea3a6e6be6172f7fd1f39b1da3'}, {'bytes': 7330, 'path': 'scripts/report_cli.py', 'sha256': '836f8a2da4ca2c987e5fedcd5863b48fd35e4edb8c0b4ebc96140b94df66d3b9'}, {'bytes': 46564, 'path': 'scripts/report_model.py', 'sha256': 'aa9f5c2800b58132a25d45fc67975161c57d3a288670959fcc45a902340e5d71'}, {'bytes': 50559, 'path': 'scripts/standard_input.py', 'sha256': 'a4ab51d0f2075f04804fa2653fa56228a4754f3454f14ffb752e2fd13eb5465a'}, {'bytes': 42962, 'path': 'scripts/template_builder.py', 'sha256': '7e1c9d3f49bdbb3b78ae3cd061192972ed24c37857d907d5c5e41c9bb44f578e'}, {'bytes': 3782, 'path': 'scripts/text_color.py', 'sha256': '61966d28f44dd1aed9dc0489fb23c432ef3d0890e518edef71faf09b98e039e1'}, {'bytes': 8040, 'path': 'scripts/workbook_formulas.py', 'sha256': '9cd6be8b9f5a73d67d87bc8ea7a1a9434119bbb9385f673ad77aa7ed1e4b78e7'}], 'schema': 'research-fund-skill-source.v1', 'tree_sha256': '28944ec28d4644acb09cd10eb8e73dc546a4250d0c21172d325b23cb713d7d43'}
EXPECTED_RUNTIME={'activation': {'activation_from_workflow_present': True, 'body_read_text_present': True, 'bytes': 46679, 'present': True, 'read_skill_content_function_present': True, 'sha256': 'ffdd9224eb543d2d68ba26af982871b27322df072f3454e1f79cd47e70831144'}, 'catalog': {'bytes': 4179, 'present': True, 'sha256': 'a2653c0d06ad432ecbda234d4d6a2fedda7b5a425469cf4ce28649089433728a'}, 'local_storage': {'bytes': 15036, 'present': True, 'sha256': 'e88d1b4152318815580f197056010e917066439022eb489b4a2749cc97ad6876'}, 'projection': {'present': False}, 'router': {'bytes': 33122, 'present': True, 'sha256': '320c8ff0bfa906341ae922824264f79a18617b1ab15d1bd84e450cac4ca2c16f'}, 'types': {'bytes': 3238, 'present': True, 'sha256': 'ff010dc1a5ddc499db8f03bbe153604cd31f5976a1a15d785b9495d53b275d50'}}
CHILD="\nimport ast,hashlib,json,os,pathlib,stat,sys,time\nstarted=time.monotonic()\ndef sha(b): return hashlib.sha256(b).hexdigest()\ndef canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()\ndef read(p,limit=2097152):\n p=pathlib.Path(p)\n for q in (p,)+tuple(p.parents):\n  if q.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')\n a=p.lstat()\n if not stat.S_ISREG(a.st_mode) or a.st_size>limit: raise RuntimeError('FILE_LIMIT')\n b=p.read_bytes(); z=p.lstat()\n if (a.st_ino,a.st_dev,a.st_size,a.st_mtime_ns)!=(z.st_ino,z.st_dev,z.st_size,z.st_mtime_ns) or len(b)!=a.st_size: raise RuntimeError('FILE_CHANGED')\n return b,a\ndef tree(root):\n root=pathlib.Path(root)\n if not root.exists(): return {'present':False}\n for p in (root,)+tuple(root.parents):\n  if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')\n if not root.is_dir(): raise RuntimeError('NOT_DIRECTORY')\n rows=[]; total=0; cache=0\n for parent,dirs,files in os.walk(str(root),followlinks=False):\n  if time.monotonic()-started>45: raise RuntimeError('SCAN_DEADLINE')\n  for n in list(dirs):\n   p=pathlib.Path(parent)/n\n   if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')\n   if n=='__pycache__': dirs.remove(n); cache+=1\n  for n in sorted(files):\n   p=pathlib.Path(parent)/n\n   if n.endswith(('.pyc','.pyo')):\n    if p.is_symlink(): raise RuntimeError('SYMLINK_REFUSED')\n    cache+=1; continue\n   b,s=read(p); total+=len(b)\n   if len(rows)>=512 or total>8388608: raise RuntimeError('PACKAGE_LIMIT')\n   rows.append({'path':p.relative_to(root).as_posix(),'bytes':len(b),'sha256':sha(b)})\n rows.sort(key=lambda x:x['path'])\n if not any(r['path']=='SKILL.md' for r in rows): raise RuntimeError('SKILL_MD_MISSING')\n b,_=read(root/'SKILL.md')\n front=b.split(b'---',2)[1] if b.startswith(b'---') and len(b.split(b'---',2))==3 else b''\n s=root.stat()\n return {'present':True,'files':rows,'tree_sha256':sha(canonical(rows)),'bytes':total,'file_count':len(rows),\n         'frontmatter_sha256':sha(front),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'ignored_cache_entries':cache}\n\ntry:\n step='mode'\n mode=sys.argv[1]\n if mode=='gateway':\n  step='read_public_skill'\n  out={'public':tree('/app/skills/public/research-fund-settlement'),'custom':tree('/app/skills/custom/research-fund-settlement'),'runtime_sources':{}}\n  require_public_again=tree('/app/skills/public/research-fund-settlement')\n  if require_public_again!=out['public']: raise RuntimeError('SKILL_CHANGED_DURING_SCAN')\n  paths={\n   'activation':'/app/backend/packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py',\n   'types':'/app/backend/packages/harness/deerflow/skills/types.py',\n   'projection':'/app/backend/packages/harness/deerflow/skills/projection.py',\n   'local_storage':'/app/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py',\n   'catalog':'/app/backend/packages/harness/deerflow/skills/catalog.py',\n   'router':'/app/backend/app/gateway/routers/skills.py'}\n  for key,path in sorted(paths.items()):\n   step='runtime_source_'+key\n   if not pathlib.Path(path).exists(): out['runtime_sources'][key]={'present':False}; continue\n   b,_=read(path); t=b.decode('utf8'); parsed=ast.parse(t)\n   functions={n.name for n in ast.walk(parsed) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}\n   out['runtime_sources'][key]={'present':True,'sha256':sha(b),'bytes':len(b)}\n   if key=='activation':\n    out['runtime_sources'][key]['read_skill_content_function_present']='_read_skill_content' in functions\n    out['runtime_sources'][key]['activation_from_workflow_present']='_activation_from_workflow' in functions\n    out['runtime_sources'][key]['body_read_text_present']='.read_text(' in t\n  step='skills_config'\n  config=pathlib.Path('/app/backend/config.yaml')\n  if config.exists():\n   b,_=read(config)\n   try:\n    import yaml\n    d=yaml.safe_load(b) or {}; sk=d.get('skills') or {}\n    out['skills_configuration']={}\n    for k in ('path','use','container_path'):\n     v=sk.get(k)\n     if v is None: out['skills_configuration'][k]=None\n     elif isinstance(v,str) and len(v)<256 and (v.startswith(('/app/skills','/mnt/skills','deerflow.skills.')) or v in ('local','filesystem')): out['skills_configuration'][k]=v\n     else: out['skills_configuration'][k]={'value_sha256':sha(canonical(v))}\n   except ImportError: out['skills_configuration']={'status':'YAML_READER_UNAVAILABLE'}\n elif mode=='sandbox':\n  root=pathlib.Path('/mnt/skills/public/research-fund-settlement'); out={'present':root.exists(),'files':[]}\n  for n in ('SKILL.md','scripts/report_cli.py','scripts/group_template.py','scripts/standard_input.py','scripts/text_color.py'):\n   p=root/n\n   if p.exists():\n    b,_=read(p); out['files'].append({'path':n,'sha256':sha(b),'bytes':len(b)})\n else: raise RuntimeError('INVALID_MODE')\n out.update({'application_imported':False,'files_modified':False,'credentials_exported':False})\n print(json.dumps(out,sort_keys=True))\nexcept Exception as exc:\n print(json.dumps({'status':'INCOMPLETE','last_step':locals().get('step','read'),'error_class':type(exc).__name__,'error_code':str(exc) if str(exc) in ('SYMLINK_REFUSED','FILE_LIMIT','FILE_CHANGED','SCAN_DEADLINE','NOT_DIRECTORY','PACKAGE_LIMIT','SKILL_MD_MISSING','SKILL_CHANGED_DURING_SCAN','INVALID_MODE') else 'BOUNDED_CHILD_READ_FAILED','raw_errors_exported':False},sort_keys=True))\n sys.exit(1)\n"

class Refused(Exception): pass

def require(ok,code):
    if not ok: raise Refused(code)

def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def digest(v): return hashlib.sha256(canonical(v)).hexdigest()

def read_prior_receipt():
    """Read only the fixed root-owned receipt using no-follow directory FDs."""
    parts=PRIOR_RELEASE['path'].split('/')[1:]
    require(parts and all(p not in ('','.','..') for p in parts),'PRIOR_RECEIPT_PATH')
    directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
            info=os.fstat(directory)
            require(info.st_uid==0 and info.st_mode & 0o022==0,'PRIOR_RECEIPT_PARENT')
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            before=os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
                    and before.st_mode & 0o022==0 and before.st_size==PRIOR_RELEASE['bytes'],'PRIOR_RECEIPT_FILE')
            raw=b''
            while len(raw)<=PRIOR_RELEASE['bytes']:
                piece=os.read(fd,min(8192,PRIOR_RELEASE['bytes']+1-len(raw)))
                if not piece:break
                raw+=piece
            after=os.fstat(fd);linked=os.stat(parts[-1],dir_fd=directory,follow_symlinks=False)
            identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_nlink)
            require(identity(before)==identity(after)==identity(linked),'PRIOR_RECEIPT_CHANGED_DURING_READ')
            return raw
        finally:os.close(fd)
    finally:os.close(directory)

def verify_prior_release():
    global LAST_STEP
    LAST_STEP='verify_prior_project_type_release'
    raw=read_prior_receipt()
    require(len(raw)==PRIOR_RELEASE['bytes'] and hashlib.sha256(raw).hexdigest()==PRIOR_RELEASE['sha256'],'PRIOR_RECEIPT_HASH_CHANGED')
    receipt=json.loads(raw)
    require(receipt.get('status')=='RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED'
            and receipt.get('exitcode')==0 and receipt.get('operation')==PRIOR_RELEASE['operation']
            and receipt.get('after_tree_sha256')==EXPECTED_SKILL['tree_sha256']
            and receipt.get('maintenance_pod_absent') is True
            and receipt.get('runtime_node_views_accepted') is True,'PRIOR_RELEASE_NOT_ACCEPTED')
    return dict(PRIOR_RELEASE,validated=True)

def deployment_status(value):
    status=value.get('status',{})
    return {'generation':value['metadata'].get('generation'),'desired':value['spec'].get('replicas'),
            'observed_generation':status.get('observedGeneration'),
            'total':status.get('replicas',0),'updated':status.get('updatedReplicas',0),
            'ready':status.get('readyReplicas',0),'available':status.get('availableReplicas',0),
            'terminating':status.get('terminatingReplicas',0),'paused':value['spec'].get('paused',False),
            'converged':converged(value)}

def command(args,input_bytes=None):
    """Bound each pipe while reading; never return stderr or execute a shell."""
    global COUNTER
    require(time.monotonic()-STARTED<DEADLINE,'HOST_DEADLINE')
    COUNTER+=1
    require(COUNTER<=MAX_COMMANDS,'COMMAND_BUDGET')
    require(args[0] in ('get','exec'),'READONLY_COMMAND_REQUIRED')
    p=subprocess.Popen(BASE+args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    buffers=[bytearray(),bytearray()]; exceeded=threading.Event()
    def drain(stream,index,limit):
        while True:
            chunk=stream.read(8192)
            if not chunk: break
            if len(buffers[index])+len(chunk)>limit:
                exceeded.set(); p.kill(); break
            buffers[index].extend(chunk)
    threads=[threading.Thread(target=drain,args=(p.stdout,0,16777216)),threading.Thread(target=drain,args=(p.stderr,1,65536))]
    for t in threads: t.daemon=True; t.start()
    try:
        if input_bytes: p.stdin.write(input_bytes)
        p.stdin.close()
        p.wait(timeout=min(60,max(1,DEADLINE-(time.monotonic()-STARTED))))
    except BaseException:
        p.kill(); p.wait(); raise
    finally:
        for t in threads: t.join(timeout=2)
    require(not exceeded.is_set(),'OUTPUT_BOUND')
    require(not any(t.is_alive() for t in threads),'PIPE_READ_INCOMPLETE')
    try: result=json.loads(bytes(buffers[0]))
    except Exception: raise Refused('INVALID_READ_RESPONSE')
    if p.returncode:
        # Child errors are fixed-category diagnostics, never raw exception text.
        if args[0]=='exec' and isinstance(result,dict) and result.get('status')=='INCOMPLETE':
            return result
        raise Refused('READ_COMMAND_FAILED')
    return result

def get(kind,name=None,selector=None):
    global LAST_STEP
    require(kind in ('deployment','replicaset','replicasets','pods','pod','pvc','pv'),'RESOURCE_NOT_ALLOWED')
    args=['get',kind]
    if name:
        require(re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',name) is not None,'INVALID_RESOURCE_NAME')
        args.append(name)
    if selector:
        require(re.fullmatch(r'[A-Za-z0-9_./=-]+(?:,[A-Za-z0-9_./=-]+)*',selector) is not None,'INVALID_SELECTOR')
        args.extend(['-l',selector])
    LAST_STEP='get:'+kind+(':'+name if name else '')
    return command(args+['-o','json'])

def fingerprint(obj):
    m=obj['metadata']
    return {'name':m['name'],'uid':m['uid'],'generation':m.get('generation'),'spec_sha256':digest(obj['spec'])}

def controlled_by(obj,kind,uid):
    owners=[o for o in obj['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
    return len(owners)==1 and owners[0].get('kind')==kind and owners[0].get('uid')==uid

def cstatus(pod,container):
    found=[s for s in pod.get('status',{}).get('containerStatuses',[]) if s.get('name')==container]
    return found[0] if len(found)==1 else {}

def ready(pod,container):
    s=cstatus(pod,container)
    return (not pod['metadata'].get('deletionTimestamp') and pod.get('status',{}).get('phase')=='Running'
            and any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[]))
            and s.get('ready') is True and 'running' in s.get('state',{}) and bool(s.get('containerID')) and bool(s.get('imageID')))

def pod_identity(pod,container):
    s=cstatus(pod,container)
    return {'uid':pod['metadata']['uid'],'spec_sha256':digest(pod['spec']),
            'owner_sha256':digest(pod['metadata'].get('ownerReferences',[])),
            'image_id':s.get('imageID'),'container_id_sha256':digest(s.get('containerID')),
            'restart_count':s.get('restartCount'),'running_since':s.get('state',{}).get('running',{}).get('startedAt')}

def converged(d):
    s=d.get('status',{}); n=d['spec'].get('replicas')
    return (isinstance(n,int) and n>0 and not d['spec'].get('paused',False)
            and s.get('observedGeneration')==d['metadata'].get('generation')
            and all(s.get(k)==n for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas'))
            and s.get('terminatingReplicas',0)==0)

def selector(d):
    sel=d['spec']['selector']; labels=sel.get('matchLabels',{})
    require(labels and not sel.get('matchExpressions'),'SELECTOR_UNSUPPORTED')
    return ','.join(k+'='+v for k,v in sorted(labels.items()))

def mounts(spec,container,path):
    volumes={v['name']:v for v in spec.get('volumes',[])}
    return [{'claim':volumes.get(m['name'],{}).get('persistentVolumeClaim',{}).get('claimName'),
             'read_only':m.get('readOnly',False),'subpath_present':bool(m.get('subPath') or m.get('subPathExpr'))}
            for c in spec.get('containers',[]) if c.get('name')==container
            for m in c.get('volumeMounts',[]) if m.get('mountPath')==path]

def current_template_matches(replica_set,deployment):
    left=copy.deepcopy(replica_set.get('spec',{}).get('template'))
    right=copy.deepcopy(deployment.get('spec',{}).get('template'))
    if not isinstance(left,dict) or not isinstance(right,dict): return False
    for template in (left,right):
        template.get('metadata',{}).get('labels',{}).pop('pod-template-hash',None)
    return left==right

def select_current_pods(d,role,diagnostics,allow_incomplete=False):
    """Retained terminal pods are evidence, never counted as current replicas."""
    sel=selector(d); labels=d['spec']['selector']['matchLabels']
    detail={'selector':sel,'selector_source':'fresh_deployment','candidates':[],
            'current_owned_ready_count':0,'desired':d['spec']['replicas']}
    diagnostics[role]=detail
    candidates=get('pods',selector=sel)['items']
    detail['candidate_count']=len(candidates)
    require(len(candidates)<=512,'ROLE_POD_LIST_BOUND_'+role)
    expected={c['name']:c['image'] for c in d['spec']['template']['spec']['containers']}
    selected=[]; rs_cache={}
    for p in sorted(candidates,key=lambda x:x['metadata']['name']):
        meta=p['metadata'];status=p.get('status',{});cs=cstatus(p,role)
        owners=[o for o in meta.get('ownerReferences',[]) if o.get('controller') is True]
        row={'name':meta['name'],'uid':meta['uid'],'phase':status.get('phase'),
             'terminating':bool(meta.get('deletionTimestamp')),
             'pod_ready':any(c.get('type')=='Ready' and c.get('status')=='True' for c in status.get('conditions',[])),
             'container_names':[c.get('name') for c in p.get('spec',{}).get('containers',[])],
             'role_ready':cs.get('ready') is True,'role_running':'running' in cs.get('state',{}),
             'container_id_present':bool(cs.get('containerID')),'image_id_present':bool(cs.get('imageID')),
             'controller_count':len(owners),
             'controllers':[{k:o.get(k) for k in ('kind','name','uid')} for o in owners],
             'selection_reason':None}
        detail['candidates'].append(row)
        if meta.get('namespace')!='deer-flow' or any(meta.get('labels',{}).get(k)!=v for k,v in labels.items()):
            row['selection_reason']='namespace_or_selector_mismatch';continue
        if not ready(p,role):
            row['selection_reason']='not_running_ready_or_identity_missing';continue
        if len(owners)!=1 or owners[0].get('kind')!='ReplicaSet' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',owners[0].get('name','')):
            row['selection_reason']='replicaset_controller_required';continue
        owner=owners[0]; name=owner['name']
        if name not in rs_cache:
            require(len(rs_cache)<8,'CURRENT_REPLICASET_READ_BOUND_'+role)
            rs_cache[name]=get('replicaset',name)
        rs=rs_cache[name]
        row['replicaset_uid_matches']=rs['metadata']['uid']==owner.get('uid')
        row['deployment_owner_matches']=controlled_by(rs,'Deployment',d['metadata']['uid'])
        if not row['replicaset_uid_matches'] or not row['deployment_owner_matches']:
            row['selection_reason']='foreign_or_changed_controller';continue
        row['template_matches']=current_template_matches(rs,d)
        if not row['template_matches']:
            row['selection_reason']='not_current_deployment_template';continue
        if {c['name']:c['image'] for c in p['spec']['containers']}!=expected:
            row['selection_reason']='pod_image_mismatch';continue
        row['selection_reason']='current_owned_ready';selected.append(p)
    detail['current_owned_ready_count']=len(selected)
    detail['complete_current_replica_set']=len(selected)==d['spec']['replicas']
    if not allow_incomplete:
        require(detail['complete_current_replica_set'],'CURRENT_READY_POD_COUNT_'+role)
    detail['selection_reason_counts']={reason:sum(row['selection_reason']==reason for row in detail['candidates']) for reason in sorted(set(row['selection_reason'] for row in detail['candidates']))}
    if len(detail['candidates'])>24:
        relevant=[row for row in detail['candidates'] if row['selection_reason']!='current_owned_ready']
        detail['candidates']=(relevant+[row for row in detail['candidates'] if row['selection_reason']=='current_owned_ready'])[:24]
        detail['candidate_details_truncated']=True
    detail['selected_pods']=[{'name':p['metadata']['name'],'uid':p['metadata']['uid']} for p in selected[:16]]
    detail['selected_pod_details_truncated']=len(selected)>16
    detail['selected_inventory_sha256']=digest(sorted((p['metadata']['name'],pod_identity(p,role)) for p in selected))
    return selected

def gateway_inventory(d,pods):
    bynode={}
    for p in pods:
        require(mounts(p['spec'],'gateway','/app/skills')==mounts(d['spec']['template']['spec'],'gateway','/app/skills'),'GATEWAY_POD_MOUNT_CHANGED')
        bynode.setdefault(p['spec']['nodeName'],p)
    require(0<len(bynode)<=16,'GATEWAY_NODE_BOUND')
    inventory=digest(sorted((p['metadata']['name'],pod_identity(p,'gateway')) for p in pods))
    return bynode,inventory

def observe(pod,container,mode):
    global LAST_STEP
    require(mode in ('gateway','sandbox') and container==mode,'CHILD_MODE_REFUSED')
    require(ready(pod,container),'EXEC_POD_NOT_READY')
    LAST_STEP='observe:'+mode
    return command(['exec','-i',pod['metadata']['name'],'-c',container,'--','python3','-I','-B','-',mode],CHILD.encode())

def compare_skill(observed):
    expected=EXPECTED_SKILL
    expected_rows={r['path']:r for r in expected['files']}
    actual_rows={r['path']:r for r in observed.get('files',[])}
    # Unknown filenames are hashed because they might contain user data.
    differences=[{'path':k if k in expected_rows else None,'path_sha256':digest(k),
                  'expected':expected_rows.get(k,{}).get('sha256'),'observed':actual_rows.get(k,{}).get('sha256')}
                 for k in sorted(set(expected_rows)|set(actual_rows)) if expected_rows.get(k)!=actual_rows.get(k)]
    ok=(observed.get('present') is True and observed.get('tree_sha256')==expected['tree_sha256']
        and observed.get('file_count')==expected['file_count'] and observed.get('bytes')==expected['bytes'] and not differences)
    return {'matches_expected':ok,'expected_tree_sha256':expected['tree_sha256'],
            'observed_tree_sha256':observed.get('tree_sha256'),'observed_count':observed.get('file_count'),
            'expected_count':expected['file_count'],'differences':differences[:64]}

def runtime_diff(observed):
    return [{'source':k,'expected_sha256':EXPECTED_RUNTIME.get(k,{}).get('sha256'),
             'observed_sha256':observed.get(k,{}).get('sha256')}
            for k in sorted(set(EXPECTED_RUNTIME)|set(observed)) if EXPECTED_RUNTIME.get(k)!=observed.get(k)]

def sandbox_mounts(spec):
    volumes={v['name']:v for v in spec.get('volumes',[])}; out=[]
    for c in spec.get('containers',[]):
        if c.get('name')!='sandbox': continue
        for m in c.get('volumeMounts',[]):
            if m.get('mountPath') not in ('/mnt/skills','/mnt/skills/public'): continue
            sub=m.get('subPath','')
            kind=('runtime_projection' if '/skills_view/policies/' in sub else
                  'published_employee_snapshot' if '/agent-skills/' in sub else
                  'shared_public' if (m['mountPath']=='/mnt/skills/public' and sub in ('','public')) or (m['mountPath']=='/mnt/skills' and not sub) else 'other')
            out.append({'kind':kind,'claim':volumes.get(m['name'],{}).get('persistentVolumeClaim',{}).get('claimName'),
                        'read_only':m.get('readOnly',False),'subpath_sha256':digest(sub)})
    return out

def inspect():
    global LAST_STEP
    report={'schema':'portal-research-fund-report-labels-preflight.v1','tool_revision':'20261106-r1','status':'INCOMPLETE',
            'automatic_retry':False,'files_modified':False,'workload_modified':False,'database_access':False,
            'secret_api_reads':0,'credentials_exported':False,'provider_requests':0,'business_requests':0,
            'application_started':False,'pods_created':0,'skill_update_requested':False,'ready_for_update':False,
            'historical_inventories_modified':False,'expected_skill_tree_sha256':EXPECTED_SKILL['tree_sha256']}
    try:
        require(socket.gethostname().split('.')[0]=='datax-01','WRONG_HOST')
        require(os.geteuid()==0,'ROOT_REQUIRED')
        report['prior_release']=verify_prior_release()
        review=[]
        require(digest(EXPECTED_SKILL['files'])==EXPECTED_SKILL['tree_sha256'],'EMBEDDED_MANIFEST_CHANGED')
        ds={n:get('deployment',n) for n in ('gateway','ppt-master-runner','provisioner')}
        report['deployments']={n:fingerprint(d) for n,d in ds.items()}
        # The frontend is observed separately; no historical generation is pinned.
        frontend=get('deployment','frontend')
        report['frontend_observation']={'deployment':fingerprint(frontend),
                                        'images':sorted(c['image'] for c in frontend['spec']['template']['spec']['containers']),
                                        'included_in_skill_update':False}
        report['convergence_before']={n:deployment_status(d) for n,d in dict(ds,frontend=frontend).items()}
        for n in ('gateway','provisioner','frontend'):
            if not report['convergence_before'][n]['converged']:
                review.append('DEPLOYMENT_NOT_CONVERGED_'+n)
        report['skills_mounts']={n:mounts(ds[n]['spec']['template']['spec'],n,'/app/skills') for n in ('gateway','ppt-master-runner')}
        for rows in report['skills_mounts'].values():
            require(len(rows)==1 and rows[0]['claim']==EXPECTED_PVC['name'] and not rows[0]['subpath_present'],'SHARED_SKILLS_MOUNT_CHANGED')
        pvc=get('pvc',EXPECTED_PVC['name']); pv=get('pv',pvc['spec'].get('volumeName'))
        report['skills_pvc']=fingerprint(pvc); report['skills_pv']=fingerprint(pv)
        report['skills_pvc']['phase']=pvc.get('status',{}).get('phase')
        require(pvc['metadata']['uid']==EXPECTED_PVC['uid'] and pvc['spec'].get('volumeName')==EXPECTED_PVC['volume'] and pvc.get('status',{}).get('phase')=='Bound' and 'ReadWriteMany' in pvc['spec'].get('accessModes',[]),'SKILLS_PVC_CHANGED')
        require(pv['metadata']['uid']==EXPECTED_PVC['pv_uid'] and pv['spec'].get('claimRef',{}).get('uid')==EXPECTED_PVC['uid'],'SKILLS_PV_CHANGED')
        provisioner=next(c for c in ds['provisioner']['spec']['template']['spec']['containers'] if c['name']=='provisioner')
        env={e['name']:e.get('value') for e in provisioner.get('env',[])}
        require(env.get('SKILLS_PVC_NAME')==EXPECTED_PVC['name'],'PROVISIONER_SKILLS_BINDING_CHANGED')
        configured_image=env.get('SANDBOX_IMAGE')
        if configured_image is not None: require(isinstance(configured_image,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}',configured_image) is not None,'SANDBOX_IMAGE_INVALID')
        report['provisioner_sandbox_configuration']={'image':configured_image,'skills_pvc_matches':True,'configured_image_explicit':bool(configured_image)}
        report['pod_selection_diagnostics']={}
        provisioner_pods=select_current_pods(ds['provisioner'],'provisioner',report['pod_selection_diagnostics'],allow_incomplete=True)
        for p in provisioner_pods:
            actual=next(c for c in p['spec']['containers'] if c['name']=='provisioner')
            require(actual['image']==provisioner['image'] and actual.get('env',[])==provisioner.get('env',[]),'PROVISIONER_POD_CONFIGURATION_CHANGED')
        report['provisioner_pods']=[{'pod':p['metadata']['name'],'identity':pod_identity(p,'provisioner')} for p in provisioner_pods]
        if configured_image is not None: require(isinstance(configured_image,str) and len(configured_image)<512,'SANDBOX_IMAGE_INVALID')
        pods=select_current_pods(ds['gateway'],'gateway',report['pod_selection_diagnostics'],allow_incomplete=True)
        bynode,inventory=gateway_inventory(ds['gateway'],pods) if pods else ({},digest([]))
        for role in ('gateway','provisioner'):
            if not report['pod_selection_diagnostics'][role]['complete_current_replica_set']:
                review.append('CURRENT_READY_POD_COUNT_'+role)
        missing=sorted(set(EXPECTED_GATEWAY_NODES)-set(bynode))
        additional=sorted(set(bynode)-set(EXPECTED_GATEWAY_NODES))
        report['gateway_node_coverage']={'expected':list(EXPECTED_GATEWAY_NODES),'missing':missing,'additional':additional,'complete':not missing and not additional}
        if missing or additional: review.append('GATEWAY_NODE_COVERAGE_REQUIRES_REVIEW')
        report['gateway_scope']={'replicas':len(pods),'nodes':len(bynode),'sampling':'one_ready_owned_pod_per_node','inventory_sha256':inventory}
        report['samples']=[]; runtime_hashes=set()
        for node,pod in sorted(bynode.items()):
            ob=observe(pod,'gateway','gateway')
            row={'node':node,'pod':pod['metadata']['name'],'identity':pod_identity(pod,'gateway')}
            report['samples'].append(row)
            require(ob.get('status')!='INCOMPLETE','CHILD_READ_FAILED_'+str(ob.get('last_step','unknown')))
            row['skill']=compare_skill(ob['public'])
            row['custom_shadow_present']=ob['custom'].get('present',False)
            row['runtime_sources']=ob['runtime_sources']; row['runtime_changes']=runtime_diff(ob['runtime_sources'])
            row['skills_configuration']=ob.get('skills_configuration')
            require(row['skill']['matches_expected'],'RELEASED_SKILL_CHANGED')
            require(not row['custom_shadow_present'],'CUSTOM_SKILL_SHADOW_REQUIRES_REVIEW')
            runtime_hashes.add(digest(ob['runtime_sources']))
            if row['runtime_changes']: review.append('RUNTIME_SKILL_LOADING_SOURCES_CHANGED')
            fresh=get('pod',pod['metadata']['name'])
            require(ready(fresh,'gateway') and pod_identity(fresh,'gateway')==pod_identity(pod,'gateway'),'SAMPLED_GATEWAY_CHANGED')
        require(len(runtime_hashes)<=1,'GATEWAY_RUNTIME_SOURCES_DIFFER')
        if not runtime_hashes: review.append('NO_READY_CURRENT_GATEWAY_SAMPLE')
        sandboxes=get('pods',selector='app=deer-flow-sandbox')['items']
        require(len(sandboxes)<=2000,'SANDBOX_INVENTORY_BOUND')
        report['sandbox_inventory']={'total':len(sandboxes),'ready':0,'mount_kinds':{},'images':[]}
        image_counts={}; samples=[]; sampled=set()
        for p in sorted(sandboxes,key=lambda x:x['metadata']['name']):
            if not ready(p,'sandbox'): continue
            report['sandbox_inventory']['ready']+=1
            c=next(c for c in p['spec']['containers'] if c['name']=='sandbox'); ident=pod_identity(p,'sandbox')
            key=(c['image'],ident['image_id']); image_counts[key]=image_counts.get(key,0)+1
            for m in sandbox_mounts(p['spec']):
                kind=m['kind']; kinds=report['sandbox_inventory']['mount_kinds']; kinds[kind]=kinds.get(kind,0)+1
                samplekey=(kind,c['image'])
                if samplekey in sampled or len(samples)>=3: continue
                if m['claim']!=EXPECTED_PVC['name']: review.append('SANDBOX_SKILLS_BINDING_REQUIRES_REVIEW'); continue
                ob=observe(p,'sandbox','sandbox')
                require(ob.get('status')!='INCOMPLETE','SANDBOX_CHILD_READ_FAILED')
                observed={r['path']:r for r in ob.get('files',[])}
                expected={r['path']:r for r in EXPECTED_SKILL['files'] if r['path'] in observed}
                sample={'pod_identity_sha256':digest(p['metadata']['uid']),'identity':ident,'image':c['image'],'mount':m,'observation':ob,
                        'known_sample_files_match':bool(observed) and observed==expected}
                samples.append(sample); sampled.add(samplekey)
                if kind=='shared_public' and not sample['known_sample_files_match']: review.append('SANDBOX_SHARED_SKILL_MISMATCH')
                fresh=get('pod',p['metadata']['name'])
                require(ready(fresh,'sandbox') and pod_identity(fresh,'sandbox')==ident,'SAMPLED_SANDBOX_CHANGED')
        report['sandbox_inventory']['images']=[{'image':k[0],'image_id':k[1],'ready_pods':v,'matches_configured_image':bool(configured_image) and k[0]==configured_image} for k,v in sorted(image_counts.items())]
        report['sandbox_samples']=samples
        if not samples: review.append('NO_READY_SANDBOX_SAMPLE')
        if not configured_image: review.append('SANDBOX_IMAGE_DEFAULT_REQUIRES_REVIEW')
        if configured_image and not any(k[0]==configured_image for k in image_counts): review.append('NO_CURRENT_SANDBOX_IMAGE_SAMPLE')
        report['convergence_after']={}
        for n,d in ds.items():
            fresh=get('deployment',n)
            require(fingerprint(d)==fingerprint(fresh),'DEPLOYMENT_CHANGED_'+n)
            report['convergence_after'][n]=deployment_status(fresh)
            if n in ('gateway','provisioner') and not converged(fresh): review.append('DEPLOYMENT_NOT_CONVERGED_'+n)
        for p in provisioner_pods:
            fresh=get('pod',p['metadata']['name'])
            require(ready(fresh,'provisioner') and pod_identity(fresh,'provisioner')==pod_identity(p,'provisioner'),'PROVISIONER_POD_CHANGED_DURING_READ')
        require(fingerprint(pvc)==fingerprint(get('pvc',EXPECTED_PVC['name'])),'SKILLS_PVC_CHANGED_DURING_READ')
        require(fingerprint(pv)==fingerprint(get('pv',EXPECTED_PVC['volume'])),'SKILLS_PV_CHANGED_DURING_READ')
        report['final_pod_selection_diagnostics']={}
        freshprovisioner=select_current_pods(ds['provisioner'],'provisioner',report['final_pod_selection_diagnostics'],allow_incomplete=True)
        require(report['pod_selection_diagnostics']['provisioner']['selected_inventory_sha256']==report['final_pod_selection_diagnostics']['provisioner']['selected_inventory_sha256'],'PROVISIONER_INVENTORY_CHANGED_DURING_READ')
        freshpods=select_current_pods(ds['gateway'],'gateway',report['final_pod_selection_diagnostics'],allow_incomplete=True)
        _,freshinventory=gateway_inventory(ds['gateway'],freshpods) if freshpods else ({},digest([]))
        require(inventory==freshinventory,'GATEWAY_INVENTORY_CHANGED_DURING_READ')
        fresh_frontend=get('deployment','frontend')
        require(fingerprint(frontend)==fingerprint(fresh_frontend),'FRONTEND_CHANGED_DURING_READ')
        report['convergence_after']['frontend']=deployment_status(fresh_frontend)
        if not converged(fresh_frontend): review.append('DEPLOYMENT_NOT_CONVERGED_frontend')
        require(verify_prior_release()==report['prior_release'],'PRIOR_RELEASE_CHANGED_DURING_READ')
        report['frontend_observation']['stable_readback']=True
        report.update(stable_readback=True,runtime_loading_review_required='RUNTIME_SKILL_LOADING_SOURCES_CHANGED' in review,review_required=bool(review),review_reasons=sorted(set(review)),
                      status='READONLY_REPORT_LABELS_BASELINE_REVIEW_REQUIRED' if review else 'READONLY_REPORT_LABELS_BASELINE_ACCEPTED_NO_CHANGE',exitcode=2 if review else 0)
    except Exception as exc:
        report.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'BOUNDED_READ_FAILED',exitcode=1)
    report.update(last_step=LAST_STEP,read_commands=COUNTER,elapsed_seconds=round(time.monotonic()-STARTED,3),raw_errors_exported=False)
    return report

def main():
    global STARTED,COUNTER
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--inspect-readonly',action='store_true',required=True); p.parse_args()
    STARTED=time.monotonic(); COUNTER=0
    report=inspect(); print(json.dumps(report,sort_keys=True),flush=True); return report['exitcode']

if __name__=='__main__': sys.exit(main())
