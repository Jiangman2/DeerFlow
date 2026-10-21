"""Local ARM64 reproduction of the dictionary packaging boundary; no models."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import prepare_runtime_finish as r

SOURCE = '''package main
import("flag";"os";"path/filepath";"github.com/yanyiwu/gojieba")
var j = initJieba()
func initJieba() *gojieba.Jieba {
 d:=os.Getenv("JIEBA_DICT_DIR"); if d=="" {return gojieba.NewJieba()}
 return gojieba.NewJieba(filepath.Join(d,"jieba.dict.utf8"),filepath.Join(d,"hmm_model.utf8"),
 filepath.Join(d,"user.dict.utf8"),filepath.Join(d,"idf.utf8"),filepath.Join(d,"stop_words.utf8"))
}
func main(){flag.Bool("probe-once",false,"help only fixture; never submits");flag.Parse();j.Free()}
'''


def main():
    start=time.monotonic(); os.umask(0o022)
    archive=Path(sys.argv[1]); out=Path('/out')
    work=Path('/tmp/jieba-test'); work.mkdir()
    locked=r.context_inputs(archive)
    proxy=work/'proxy'; proxy.mkdir()
    prefix='offline/go-proxy/github.com/yanyiwu/gojieba/@v/v1.4.7.'
    with tarfile.open(archive,'r:') as t:
        for m in t:
            if m.name=='source/go.sum':
                (work/'go.sum').write_bytes(t.extractfile(m).read())
            if m.name.startswith(prefix):
                assert m.isfile() and not m.linkname and m.size<32*1024**2
                target=proxy/m.name[len('offline/go-proxy/'):]
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(t.extractfile(m).read())
    (work/'go.mod').write_text('module jieba-qualification\n\ngo 1.26.0\n\nrequire github.com/yanyiwu/gojieba v1.4.7\n')
    (work/'main.go').write_text(SOURCE)
    env=dict(os.environ,GOTOOLCHAIN='local',GOPROXY='file://'+str(proxy),GOSUMDB='off',
             GOEXPERIMENT='nodwarf5',GOMAXPROCS='2',CGO_ENABLED='1',GOFLAGS='-mod=readonly')
    env.pop('JIEBA_DICT_DIR',None)
    print('jieba_probe=COMPILE_SMALL_FIXTURE_ONLY network=none',flush=True)
    with (out/'compile.log').open('wb') as log:
        subprocess.run(['go','build','-p','2','-trimpath','-o','probe','.'],cwd=work,env=env,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    missing=subprocess.run([str(work/'probe'),'-h'],cwd='/',env=env,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,timeout=30)
    assert missing.returncode==2 and b'Dictionary file does not exist:' in missing.stderr
    directory=work/'dict'; directory.mkdir(mode=0o755)
    data=r.dictionaries(locked['offline/'+r.ZIP_NAME])
    for name,raw in data.items():
        (directory/name).write_bytes(raw); (directory/name).chmod(0o644)
    env['JIEBA_DICT_DIR']=str(directory)
    # Drop to the same non-root identity as production BEFORE process initialization.
    def unprivileged():
        os.setgroups([]); os.setgid(1000); os.setuid(1000)
    fixed=subprocess.run([str(work/'probe'),'-h'],cwd='/',env=env,stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE,timeout=30,preexec_fn=unprivileged)
    assert fixed.returncode==0 and b'probe-once' in fixed.stderr
    result={'jieba_packaging_reproduction':'PASS','architecture':'linux/arm64_EMULATED',
            'missing_dictionary_exit':missing.returncode,'fixed_nonroot_help_exit':fixed.returncode,
            'dictionary_hashes':{n:hashlib.sha256(raw).hexdigest() for n,raw in data.items()},
            'elapsed_seconds':round(time.monotonic()-start,2),'provider_requests':0,
            'scope':'SAME_GOJIEBA_MODULE_AND_INIT_PATTERN_NOT_FULL_CANDIDATE','ready_for_rollout':False}
    (out/'JIEBA-PROBE.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__': main()
