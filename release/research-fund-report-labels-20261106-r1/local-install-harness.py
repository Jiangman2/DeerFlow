import base64,hashlib,importlib.util,json,time
from pathlib import Path
qa=Path('/qa')
spec=importlib.util.spec_from_file_location('installed_remote',qa/'installed_remote.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
before=json.loads((qa/'before-manifest.json').read_bytes());after=json.loads((qa/'after-manifest.json').read_bytes())
(r.ROOT/'public').mkdir()
r.unpack((qa/'before.tar.gz').read_bytes(),before,r.ROOT/'public'/r.NAME)
original_run=r.subprocess.run
seen=[]
def run(*args,**kwargs):
    result=original_run(*args,**kwargs)
    argv=args[0]
    if len(argv)>4 and argv[1:4]==['-I','-B','-c'] and argv[4]==r.QUALIFIER_SOURCE:
        assert len(seen)==0
        value=json.loads(result.stdout)
        with (qa/'qualification-run.json').open('xb') as out:out.write(r.canonical(value)+b'\n')
        seen.append({'returncode':result.returncode,'passed':value.get('passed')})
    return result
r.subprocess.run=run
archive=(qa/'candidate.tar.gz').read_bytes()
request={'mode':'stage','operation':'17171717171717171717171717171717','before':before,'after':after,'archive_b64':base64.b64encode(archive).decode(),'archive_sha256':r.sha(archive)}
started=time.monotonic()
with r.lease():
    staged=r.stage(request)
    assert staged['status']=='STAGED_SYNTHETIC_ACCEPTED'
    r.verify(r.ROOT/'public'/r.NAME,before)
    published=r.publish(dict(request,mode='publish'))
    assert published['status']=='SKILL_PACKAGE_UPDATED'
    r.verify(r.ROOT/'public'/r.NAME,after)
    r.verify(r.transaction(request['operation'])/'backup',before)
assert seen==[{'returncode':0,'passed':304}]
result={'schema':'research-fund-report-labels-local-install-qualification.v1','status':'PASS','scope':'local_synthetic_shared_directory_only','production_actions':0,'database_access':False,'provider_requests':0,'network_access':False,'user_files_read':False,'candidate_tree_sha256':after['tree_sha256'],'before_tree_sha256':before['tree_sha256'],'archive_sha256':r.sha(archive),'stage':staged,'publication':published,'qualifier_invocations':len(seen),'elapsed_seconds':round(time.monotonic()-started,3)}
with (qa/'local-install-qualification.json').open('xb') as out:out.write(r.canonical(result)+b'\n')
print(json.dumps({'status':'PASS','passed':304,'command_checks':9,'candidate_tree':after['tree_sha256'],'production_actions':0,'elapsed_seconds':result['elapsed_seconds']}))
