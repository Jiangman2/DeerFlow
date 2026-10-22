"""Exercise ARM64 native code and exact private executable transport; synthetic keys only."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import native_credential_transport as c

IMAGE='sha256:581f0292073059289b736d46d572ba4c515e0b511f8645963e0b0adf232680a1'
KEY='0123456789abcdef0123456789abcdef'  # Synthetic local test only.


def main():
    p=argparse.ArgumentParser(); p.add_argument('--helper',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path); args=p.parse_args()
    if args.output.exists(): raise RuntimeError('qualification_exists')
    c.HELPER=args.helper.read_bytes(); c.HELPER_SHA=hashlib.sha256(c.HELPER).hexdigest()
    cid=None
    def command(argv,data=None,check=True):
        result=subprocess.run(argv,input=data,capture_output=True,timeout=45)
        if check and result.returncode: raise RuntimeError('synthetic_native_command_failed:'+result.stderr.decode()[-300:])
        return result
    try:
        cid=command(['docker','run','-d','--rm','--pull=never','--platform=linux/arm64','--network=none',
            '--cpus=1','--memory=256m','--pids-limit=64','--read-only','--tmpfs','/tmp:rw,exec,nosuid,nodev,size=16m',
            '--user=1000:1000','--name','codex-native-credential-'+uuid.uuid4().hex[:12],
            '-e','SYSTEM_AES_KEY='+KEY,'-e','AUTO_MIGRATE=false','--entrypoint','sleep',IMAGE,'900']).stdout.decode().strip()
        import re
        assert re.fullmatch('[a-f0-9]{64}',cid)
        argv=['docker','exec','-i',cid,'python3','-I','-B','-c',c.LAUNCHER,c.HELPER_SHA]
        def call(request,ok=True):
            result=command(argv,c.packet(request),False)
            value=json.loads(result.stdout)
            assert (result.returncode==0)==ok,(value,result.stderr[-100:])
            if not ok:
                assert value.get('credential_transport')=='UNCONFIRMED' and value.get('raw_error_printed') is False
                assert value.get('step') in ('identity_and_input','private_executable_stage','native_execution','native_receipt')
                assert KEY not in result.stdout.decode() and not result.stderr
            return value
        test=command(argv,c.packet({'mode':'self-test'}),False)
        if test.returncode:
            # Debug synthetic inputs only; this path never handles a real key.
            debug=c.LAUNCHER.replace('except BaseException:',"except BaseException as exc:\n print(type(exc).__name__,str(exc),file=sys.stderr)")
            debug=debug.replace('if child.returncode or child.stderr',"print(child.returncode,child.stdout,child.stderr,file=sys.stderr)\n  if child.returncode or child.stderr")
            d=command(argv[:-2]+[debug,c.HELPER_SHA],c.packet({'mode':'self-test'}),False)
            raise RuntimeError('synthetic_selftest_diagnostic:'+d.stderr.decode()[-3000:])
        assert json.loads(test.stdout)==dict(credential_helper='PASS',mode='self-test',native_roundtrip=True)
        cipher=call({'mode':'encrypt','api_key':'SYNTHETIC-GLM-CREDENTIAL'})['ciphertext']
        assert c.valid_cipher(cipher)
        raw=base64.urlsafe_b64decode(cipher[7:]+'='*((-len(cipher[7:]))%4))
        assert AESGCM(KEY.encode()).decrypt(raw[:12],raw[12:],None)==b'SYNTHETIC-GLM-CREDENTIAL'
        call({'mode':'verify','api_key':'SYNTHETIC-GLM-CREDENTIAL','ciphertext':cipher})
        nonce=os.urandom(12); independent='enc:v1:'+base64.urlsafe_b64encode(nonce+AESGCM(KEY.encode()).encrypt(nonce,b'SYNTHETIC-PYTHON',None)).decode().rstrip('=')
        call({'mode':'verify','api_key':'SYNTHETIC-PYTHON','ciphertext':independent})
        for req in ({'mode':'encrypt','api_key':''},{'mode':'encrypt','api_key':'enc:v1:fake'},
            {'mode':'encrypt','api_key':'has whitespace'}, {'mode':'encrypt','api_key':'a'*4097},
            {'mode':'encrypt','api_key':'中文'}, {'mode':'encrypt','api_key':'good','extra':True},
            {'mode':'other'}, {'mode':'verify','api_key':'WRONG','ciphertext':cipher},
            {'mode':'verify','api_key':'SYNTHETIC-GLM-CREDENTIAL','ciphertext':cipher[:-3]+'AAA'}): call(req,False)
        other=call({'mode':'encrypt','api_key':'SYNTHETIC-GLM-CREDENTIAL'})['ciphertext']
        assert other!=cipher
        bad=command(argv[:-1]+['0'*64],c.packet({'mode':'self-test'}),False)
        assert bad.returncode!=0
        bad_key=command(['docker','exec','-i','-e','SYSTEM_AES_KEY=invalid',cid]+argv[4:],c.packet({'mode':'self-test'}),False)
        assert bad_key.returncode!=0
        clean=command(['docker','exec',cid,'python3','-c',"from pathlib import Path; assert not list(Path('/tmp').glob('.weknora-credential-*'))"])
        report=dict(native_credential_qualification='PASS',platform='linux/arm64',uid=1000,gid=1000,
            native_weknora_crypto_roundtrip='PASS',python_aes256gcm_bidirectional_interop='PASS',
            private_executable_readonly_root='PASS',random_nonce='PASS',invalid_inputs_rejected=9,
            helper_sha256=c.HELPER_SHA,launcher_sha256=hashlib.sha256(c.LAUNCHER.encode()).hexdigest(),
            temp_helper_cleanup='PASS',wrong_master_key_rejected=True,
            production_keys_used=False,network='NONE',production_mutation=False)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report))
    finally:
        if cid:
            command(['docker','rm','-f',cid]); print('disposable_crypto_fixture_removed=true')


if __name__=='__main__': main()
