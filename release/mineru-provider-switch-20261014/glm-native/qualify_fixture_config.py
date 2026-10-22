"""Execute a minimal Go/Viper reproduction without compiling or running WeKnora."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from fixture_config_fix import corrected_config


def main():
    p=argparse.ArgumentParser()
    for name in ('go','source','proxy','module-cache','output'): p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    if args.output.exists(): raise RuntimeError('qualification_output_exists')
    locks={'go.mod':'2f2555453ee70e6bd86e2edaf89be7bf4c85bf9f40f72206b1ad1ab239f2d34b',
           'go.sum':'0effbd17b64fd6da32e0cc9ce2fe89fe2a18d92a0384303ecfcec92bd7a6329c'}
    sources={name:(args.source/name).read_bytes() for name in locks}
    for name,raw in sources.items():
        if hashlib.sha256(raw).hexdigest()!=locks[name]: raise RuntimeError('source_lock_changed:'+name)
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','HOME')}
    env.update(GOTOOLCHAIN='local',GOPROXY=args.proxy.resolve().as_uri(),GOSUMDB='off',
        GOMODCACHE=str(args.module_cache.resolve()),GOWORK='off',GOFLAGS='-mod=readonly',CGO_ENABLED='0',GOMAXPROCS='2')
    probe=Path(__file__).with_name('fixture_config_probe.go').resolve()
    with tempfile.TemporaryDirectory(prefix='weknora-config-probe-') as temp:
        work=Path(temp)
        for name,raw in sources.items(): (work/name).write_bytes(raw)
        # Load only these pure functions, not Linux-only cluster administration imports.
        tree=ast.parse(Path(__file__).with_name('accept_enrichment.py').read_text(encoding='utf-8'))
        tree.body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('clean_config','spec_hash')]
        scope={'json':json,'hashlib':hashlib}; exec(compile(tree,'fixture_config','exec'),scope)
        original=scope['clean_config'](); fixed=corrected_config(original)
        for name,value in (('before.json',original),('after.json',fixed)):
            (work/name).write_text(json.dumps(value),encoding='utf-8')
        version=subprocess.check_output([str(args.go),'version'],env=env,timeout=20).decode().strip()
        print(json.dumps(dict(config_probe='RUNNING',go=version,model_requests=0)),flush=True)
        proc=subprocess.run([str(args.go),'run','-p','2',str(probe),str(work/'before.json'),str(work/'after.json')],
            cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=240)
        if proc.returncode:
            print(proc.stderr.decode('utf-8','replace')); raise RuntimeError('minimal_viper_probe_failed')
        receipt=json.loads(proc.stdout)
        if receipt.get('fixture_config_reproduction')!='PASS': raise RuntimeError('probe_missing_pass')
        for name,raw in sources.items():
            if (work/name).read_bytes()!=raw: raise RuntimeError('go_lock_modified')
    receipt.update(go=version,dependency_locks=locks,probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(),
        original_config_sha256=scope['spec_hash'](original),corrected_config_sha256=scope['spec_hash'](fixed),
        proxy='LOCAL_FILE_ONLY',production_writes=0,ready_for_rollout=False)
    args.output.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt),flush=True)


if __name__=='__main__': main()
