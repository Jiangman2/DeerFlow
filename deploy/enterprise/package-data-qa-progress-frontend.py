"""Bind the qualified full OCI to the existing node-specific frontend importer.

No runtime/Kubernetes actions. Preserve import, space, CRI, and once-only gates;
full OCI transport needs no read of reused containerd payloads.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

PINS = {
    '0015': ('preload-portal-frontend-employee-roster-20261020-r2.py', 'a68dcf1f762f6a871bde3a0319604de118eb938bd0c9e49af68544b9392268f6'),
    'datax02': ('preload-portal-frontend-employee-roster-datax02-20261020-r2.py', 'd885b1a68e9427adbebe1ba1e911bbb4d3bc5ff85e965d2bc3dc1608eaf77804'),
}
REPO = 'deerflow.local/deer-flow-frontend'
TAG = REPO + ':20261021-data-qa-progress-r1'
CURRENT = 'sha256:ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def replace(source, name, statement, function=False):
    tree=ast.parse(source)
    found=[]
    for n in tree.body:
        if function and isinstance(n,ast.FunctionDef) and n.name==name: found.append(n)
        if not function and isinstance(n,ast.Assign):
            if any(isinstance(t,ast.Name) and t.id==name for target in n.targets for t in ast.walk(target)): found.append(n)
    assert len(found)==1, 'SOURCE_SHAPE_'+name
    n=found[0]; lines=source.splitlines(keepends=True)
    return ''.join(lines[:n.lineno-1])+statement+'\n'+''.join(lines[n.end_lineno:])


def build(archive, qualification, importer_directory, output):
    record=json.loads(qualification.read_text(encoding='utf-8'))
    assert record['status']=='LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    assert record['runtime']['http']['local_http']=='PASS' and record['fresh_runtime']['pinned_base_prefix']=='PASS'
    transport={'bytes':archive.stat().st_size,'sha256':sha(archive.read_bytes())}
    assert transport=={k:record['export'][k] for k in ('bytes','sha256')}, 'QUALIFIED_EXPORT_CHANGED'
    with tarfile.open(archive,'r:') as tar:
        index=json.load(tar.extractfile('index.json')); assert len(index['manifests'])==1
        top=index['manifests'][0]; md=top['digest']; mr=tar.extractfile('blobs/sha256/'+md[7:]).read()
        assert 'sha256:'+sha(mr)==md and md==record['image']['id']
        manifest=json.loads(mr); cd=manifest['config']['digest']; cr=tar.extractfile('blobs/sha256/'+cd[7:]).read()
        assert 'sha256:'+sha(cr)==cd
        config=json.loads(cr)
        assert config['architecture']=='arm64' and config['os']=='linux'
        assert len(manifest['layers'])==len(config['rootfs']['diff_ids'])==11
    chain=''
    for diff in config['rootfs']['diff_ids']:
        chain=diff if not chain else 'sha256:'+sha((chain+' '+diff).encode())
    output.mkdir(exist_ok=False)
    target=output/'frontend.data-qa-progress.full.oci.tar'
    with archive.open('rb') as src,target.open('xb') as dst: shutil.copyfileobj(src,dst)
    assert target.stat().st_size==transport['bytes'] and sha(target.read_bytes())==transport['sha256']
    receipts={}
    for node,(filename,pin) in PINS.items():
        raw=(importer_directory/filename).read_bytes(); assert sha(raw)==pin,'IMPORTER_CHANGED'
        source=raw.decode().replace('\r\n','\n')
        ns={}
        for n in ast.parse(source).body:
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PROTECTED' for t in n.targets): ns=ast.literal_eval(n.value)
        ns.update({REPO+':20261020-employee-roster-r2':CURRENT,REPO+'@'+CURRENT:CURRENT})
        controls='/mnt/data/' if node=='0015' else '/var/lib/'
        values={'TRANSPORT_PIN':transport,'INPUT':'/home/cnooc/'+target.name,'CONTROL':controls+'portal-frontend-data-qa-progress-preload-20261021-r1',
                'TAG':TAG,'MANIFEST':md,'CONFIG':cd,'FINAL_CHAIN':chain,'CACHED_DESCRIPTORS':[],'DESCRIPTORS':manifest['layers'],'PROTECTED':ns}
        for key,value in values.items(): source=replace(source,key,key+' = '+repr(value))
        source=replace(source,'MANIFEST_BYTES','MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = '+repr((len(mr),len(cr),11)))
        source=replace(source,'assemble_complete', '''def assemble_complete(fd,directory,controller,original,started):
    # seal_source has pinned the full export; inspect_archive below independently
    # validates every blob and diff ID before any import. No cache reconstruction.
    time_gate(started)
    controller.report.update(cached_containerd_payload_bytes_read=False,cached_payload_bytes=0,
        transport_kind='FULL_HASH_BOUND_OCI',reused_base_blob_count=0)
    return os.dup(fd)''',function=True)
        ast.parse(source,feature_version=(3,7)); compile(source,'<frontend-preload>','exec')
        name='preload-portal-frontend-data-qa-progress-'+node+'-20261021-r1.py'
        with (output/name).open('x',encoding='utf-8',newline='\n') as stream: stream.write(source)
        receipts[node]={'file':name,'bytes':len(source.encode()),'sha256':sha(source.encode()),'base_importer_sha256':pin}
    result={'schema':'portal-data-qa-progress-frontend-package.v1','manifest':md,'config':cd,'tag':TAG,'transport':transport,
        'qualified_export_record_sha256':sha(qualification.read_bytes()),'scripts':receipts,'workload_modified':False,'ready_for_rollout':False}
    with (output/'PACKAGE.json').open('x',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('archive','qualification','importer-directory','output'): p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args(); print(json.dumps(build(a.archive,a.qualification,a.importer_directory,a.output)))
