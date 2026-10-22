"""Package a small, self-contained acceptance tool; no images or credentials."""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zlib

HERE=Path(__file__).resolve().parent
TESTS=['test_full_enrichment','test_classify_enrichment_assets','test_audit_enrichment_runtime',
 'test_resume_enrichment_preload','test_enrichment_preload_diagnostic','test_frozen_enrichment_recovery',
 'test_preload_enrichment_images','test_transfer_base','test_export_enrichment_images','test_export_weknora_base',
 'test_native_admission_recovery','test_native_glm_acceptance','test_native_preload','test_runtime_reverify',
 'test_runtime_delta_audit','test_runtime_resume','test_runtime_finish','test_dwarf_build_fix',
 'test_sqlite_build_fix','test_native_build','test_unpack_rust']


def sample():
    # Deterministic test fixture, not an enterprise document or model-generated content.
    width,height=640,440; pixels=bytearray(b'\xff'*(width*height*3))
    def rect(x1,y1,x2,y2,color):
        for y in range(y1,y2):
            for x in range(x1,x2): pixels[(y*width+x)*3:(y*width+x+1)*3]=bytes(color)
    glyphs={
      'O':['01110','10001','10001','10001','10001','10001','01110'],
      'R':['11110','10001','10001','11110','10100','10010','10001'],
      'B':['11110','10001','10001','11110','10001','10001','11110'],
      'I':['11111','00100','00100','00100','00100','00100','11111'],
      'T':['11111','00100','00100','00100','00100','00100','00100'],
      '7':['11111','00001','00010','00100','01000','01000','01000'],
      '4':['00010','00110','01010','10010','11111','00010','00010'],
      '2':['01110','10001','00001','00010','00100','01000','11111'],
      '6':['00110','01000','10000','11110','10001','10001','01110']}
    for i,char in enumerate('ORBIT 7426'):
        if char==' ': continue
        for y,row in enumerate(glyphs[char]):
            for x,bit in enumerate(row):
                if bit=='1': rect(25+i*60+x*10,60+y*10,35+i*60+x*10,70+y*10,(12,18,26))
    rect(80,220,560,380,(0,72,230))
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    scan=b''.join(b'\x00'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(scan,9))+chunk(b'IEND',b'')


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); output=args.output.resolve()
    if output.exists(): raise RuntimeError('delivery_exists')
    if sys.platform!='linux' or os.geteuid()!=0: raise RuntimeError('linux_root_qualification_required')
    tests=subprocess.run([sys.executable,'-B','-m','unittest','-v']+TESTS,cwd=str(HERE),
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=240)
    if tests.returncode or b'skipped=' in tests.stdout or b'... skipped' in tests.stdout:
        sys.stdout.buffer.write(tests.stdout); raise RuntimeError('offline_tests_failed_or_skipped')
    names=('native_glm_acceptance','diagnose_enrichment_preload','audit_enrichment_runtime','accept_enrichment')
    sources={n:(HERE/(n+'.py')).read_text(encoding='utf-8') for n in names}
    runtime=(HERE/'enrichment_fixture_runtime.py').read_text(encoding='utf-8'); ast.parse(runtime,feature_version=(3,7))
    png=sample(); boot='#!/usr/bin/env python3\nimport sys,types\n'
    for name,source in sources.items():
        ast.parse(source,feature_version=(3,7))
        boot+='_m=types.ModuleType('+repr(name)+'); _m.__file__='+repr(name+'.py')+'\n'
        boot+='sys.modules['+repr(name)+']=_m\nexec(compile('+repr(source)+','+repr(name+'.py')+',"exec"),_m.__dict__)\n'
    boot+='sys.modules["accept_enrichment"].RUNTIME_SOURCE='+repr(runtime)+'\n'
    boot+='sys.modules["accept_enrichment"].SAMPLE_B64='+repr(base64.b64encode(png).decode())+'\n'
    boot+='if __name__=="__main__":\n    sys.exit(sys.modules["accept_enrichment"].main())\n'
    ast.parse(boot,feature_version=(3,7)); output.mkdir(parents=True)
    target=output/'weknora-full-enrichment-20261017-r1.py'; target.write_bytes(boot.encode())
    help_result=subprocess.run([sys.executable,'-I','-B',str(target),'--help'],capture_output=True,timeout=15)
    if help_result.returncode: raise RuntimeError('packaged_help_failed')
    code='''import base64,runpy,sys,json
runpy.run_path(sys.argv[1],run_name='packaged_offline_test')
a=sys.modules['accept_enrichment']; assert a.SAMPLE_B64
assert base64.b64decode(a.SAMPLE_B64).startswith(b'\\x89PNG\\r\\n\\x1a\\n')
assert not a.index_pass({'indexed':True,'parse_status':'completed'})
assert 'CNOOC_RETRIEVAL_API_KEY' not in a.RUNTIME_SOURCE
objects=a.manifests('weknora-enrich-check-'+'a'*16,dict(app='a',postgres='p',redis='r'),b'ca',{},['10.0.0.1'],[],dict(service_ip='10.0.0.2',pods=[dict(ip='10.0.0.3')]),dict(bootstrap='a'*32,app='a'*32,jwt='a'*32,aes='a'*32))
for obj in objects: a.validate(obj,obj)
assert len(json.dumps(objects[1]).encode()) < 1048576
'''
    tested=subprocess.run([sys.executable,'-I','-B','-c',code,str(target)],capture_output=True,timeout=15)
    if tested.returncode: raise RuntimeError('packaged_execution_failed')
    (output/'synthetic-image.png').write_bytes(png)
    (output/'RUNBOOK.txt').write_bytes((HERE/'FULL-ENRICHMENT-RUNBOOK.txt').read_bytes())
    (output/'LOCAL-TESTS.txt').write_bytes(tests.stdout)
    sha=hashlib.sha256(target.read_bytes()).hexdigest()
    report={'script':target.name,'sha256':sha,'linux_offline_tests':'PASS_ZERO_SKIPS',
        'packaged_function_test':'PASS','python37_grammar':'PASS','sample_sha256':hashlib.sha256(png).hexdigest(),
        'runtime_source_sha256':hashlib.sha256(runtime.encode()).hexdigest(),
        'server_execution':'NOT_EXECUTED_LOCALLY','production_writes':0,'provider_requests':0,'ready_for_rollout':False}
    (output/'VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(tests.stdout.decode('utf-8','replace').split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({'delivery':str(target),'sha256':sha,'bytes':target.stat().st_size}))


if __name__=='__main__': main()
