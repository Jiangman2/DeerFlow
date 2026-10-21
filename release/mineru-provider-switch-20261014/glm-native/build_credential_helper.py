"""Offline, small stdlib-only helper build. Generated copies are build artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
CRYPTO=HERE.parents[2]/'vendor/weknora-app-20260912/internal/utils/crypto.go'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); args=p.parse_args()
    if args.output.exists(): raise RuntimeError('output_exists')
    raw=CRYPTO.read_bytes()
    if raw.count(b'package utils')!=1: raise RuntimeError('crypto_source_shape')
    args.output.mkdir(parents=True)
    (args.output/'crypto.go').write_bytes(raw.replace(b'package utils',b'package main',1))
    shutil.copyfile(HERE/'credential_helper_main.go',args.output/'main.go')
    import os
    env=dict(os.environ,CGO_ENABLED='0',GOOS='linux',GOARCH='arm64',GO111MODULE='off',
        GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',GOMAXPROCS='2')
    binary=args.output/'native-credential-helper'
    subprocess.run(['/usr/local/go/bin/go','build','-trimpath','-buildvcs=false','-ldflags=-s -w',
        '-o',str(binary),str(args.output/'main.go'),str(args.output/'crypto.go')],env=env,check=True,timeout=240)
    b=binary.read_bytes()
    if b[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(b[18:20],'little')!=183: raise RuntimeError('elf_arch')
    info=subprocess.run(['/usr/local/go/bin/go','version','-m',str(binary)],check=True,capture_output=True).stdout.decode()
    if '\tdep\t' in info: raise RuntimeError('external_go_dependency')
    report=dict(native_credential_build='PASS',crypto_source_sha256=sha(raw),
        crypto_source_transform='PACKAGE_DECLARATION_ONLY',main_sha256=sha((HERE/'credential_helper_main.go').read_bytes()),
        helper_sha256=sha(b),helper_bytes=len(b),go_buildinfo=info,network='NONE',production_mutation=False)
    (args.output/'BUILD.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__': main()
