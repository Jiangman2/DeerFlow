"""Exact small-file PowerShell5/7 fixture; stub executables contain no network code."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

HERE = Path(__file__).absolute().parent
ROOT = HERE.parent
TEMPLATE = ROOT/'native-upload-template.ps1'
GENERATOR = ROOT/'prepare_native_upload.py'
MERGE = ROOT.parent.parent.parent/'portal-ppt-merge-20261017'
PS5 = Path(r'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
PS7 = Path(r'C:/Users/15285/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe')
STUB = r'''
using System;
using System.IO;
using System.Diagnostics;
using System.Text;
public static class OfflineArgvStub {
 public static int Main(string[] args) {
  string name = Path.GetFileNameWithoutExtension(Process.GetCurrentProcess().MainModule.FileName);
  string directory = Environment.GetEnvironmentVariable("PORTAL_UPLOAD_REVIEW_LOG");
  string[] encoded = new string[args.Length];
  for(int i=0;i<args.Length;i++) encoded[i]=Convert.ToBase64String(Encoding.UTF8.GetBytes(args[i]));
  File.WriteAllLines(Path.Combine(directory,name+".args"),encoded);
  if(name=="ssh") File.WriteAllText(Path.Combine(directory,"ssh.stdin"),Console.In.ReadToEnd());
  int code=0; Int32.TryParse(Environment.GetEnvironmentVariable("PORTAL_UPLOAD_REVIEW_"+name.ToUpper()+"_EXIT"),out code);
  return code;
 }
}
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run(argv, env=None, timeout=30):
    start=time.monotonic()
    result=subprocess.run([str(x) for x in argv],env=env,capture_output=True,timeout=timeout)
    return {'argv':[str(x) for x in argv], 'exit_code':result.returncode,'seconds':round(time.monotonic()-start,3),
            'stdout':result.stdout.decode('utf-8',errors='replace'),'stderr':result.stderr.decode('utf-8',errors='replace')}


def argv_from(path):
    return [base64.b64decode(line).decode('utf-8') for line in path.read_text(encoding='utf-8-sig').splitlines()]


def main():
    template=TEMPLATE.read_bytes()
    inventory_raw=(MERGE/'CANDIDATE-IMAGE-INVENTORY.json').read_bytes()
    assert sha(inventory_raw)=='e6662fbebf2210d73de5f64498fe7548a718570ef303181016802ea2c118dbef'
    original=[row for row in json.loads(inventory_raw)['images'] if row['archive']['host']=='local']
    assert len(original)==5 and sum(row['archive']['bytes'] for row in original)==3818267648
    # Original OCI inputs are stat'd only, never read/hash-copied for this test.
    original_mapping=[]
    for row in original:
        actual=Path(row['archive']['path'])
        assert actual.is_file() and actual.stat().st_size==row['archive']['bytes']
        original_mapping.append({'role':row['role'],'name':actual.name,'bytes':actual.stat().st_size,'path':str(actual)})
    results=[]
    with tempfile.TemporaryDirectory(prefix='portal-ppt-offline-argv-review-') as temp:
        base=Path(temp)
        tools=base/'stub executables';tools.mkdir()
        compiler=base/'compile.ps1'
        compiler.write_text("param([string]$Output)\n$ErrorActionPreference='Stop'\nAdd-Type -TypeDefinition @'\n"+STUB+"\n'@ -OutputAssembly $Output -OutputType ConsoleApplication\n",encoding='utf-8')
        compiled=run([PS5,'-NoProfile','-ExecutionPolicy','Bypass','-File',compiler,tools/'ssh.exe'])
        assert compiled['exit_code']==0,compiled
        shutil.copyfile(tools/'ssh.exe',tools/'scp.exe')
        for engine in (PS5,PS7):
            for scenario in ('plan','success','ssh-failure','scp-failure','input-mismatch'):
                case=base/(engine.stem+'-'+scenario);case.mkdir()
                inputs=case/'source with spaces';inputs.mkdir()
                logs=case/'logs';logs.mkdir()
                archives=[]
                for index,row in enumerate(original):
                    name=Path(row['archive']['path']).name
                    raw=('tiny OCI substitute '+str(index)+'\n').encode()
                    source=inputs/name;source.write_bytes(raw)
                    archives.append({'kind':'archive','role':row['role'],'name':name,'path':str(source),'bytes':len(raw),'sha256':sha(raw)})
                tool=case/'tool.py';tool.write_bytes(b'# inert fixture only\n')
                manifest={'destination':'cnooc@192.168.111.2','directory':'/home/cnooc/portal-ppt-native-preload-20261017-r1',
                    'archives':archives,'archive_bytes':sum(r['bytes'] for r in archives),
                    'tools':[{'kind':'tool','name':tool.name,'bytes':tool.stat().st_size,'sha256':sha(tool.read_bytes())}],
                    'server_command':'PRINT_ONLY_DO_NOT_EXECUTE'}
                manifest_raw=json.dumps(manifest).encode();(case/'UPLOAD-MANIFEST.json').write_bytes(manifest_raw)
                script=case/'Upload.ps1';script.write_bytes(template.replace(b'__UPLOAD_MANIFEST_SHA256__',sha(manifest_raw).encode()))
                if scenario=='input-mismatch': Path(archives[0]['path']).write_bytes(b'wrong-size')
                env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],PORTAL_UPLOAD_REVIEW_LOG=str(logs),
                         PORTAL_UPLOAD_REVIEW_SSH_EXIT='29' if scenario=='ssh-failure' else '0',
                         PORTAL_UPLOAD_REVIEW_SCP_EXIT='31' if scenario=='scp-failure' else '0')
                # A Python launch does not perform PowerShell's cross-version
                # PSModulePath repair. Let each engine construct its own defaults.
                for key in list(env):
                    if key.upper() == 'PSMODULEPATH': env.pop(key)
                args=[engine,'-NoProfile','-ExecutionPolicy','Bypass','-File',script]
                if scenario=='plan':args.append('-PlanOnly')
                result=run(args,env)
                should_pass=scenario in ('plan','success')
                assert (result['exit_code']==0)==should_pass,result
                ssh_log,scp_log=logs/'ssh.args',logs/'scp.args'
                assert ssh_log.exists()==(scenario not in ('plan','input-mismatch')),(scenario,result)
                assert scp_log.exists()==(scenario in ('success','scp-failure')),(scenario,result)
                if ssh_log.exists():
                    assert argv_from(ssh_log)==['-T','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=ask','cnooc@192.168.111.2','python3 -I -B -']
                    bootstrap=(logs/'ssh.stdin').read_text(encoding='utf-8-sig')
                    assert 'p.mkdir(mode=0o700)' in bootstrap and 'p.exists() or p.is_symlink()' in bootstrap
                    assert 'ctr' not in bootstrap and 'kubectl' not in bootstrap
                if scp_log.exists():
                    expected=['-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',str(tool)]+[r['path'] for r in archives]+['cnooc@192.168.111.2:/home/cnooc/portal-ppt-native-preload-20261017-r1/']
                    assert argv_from(scp_log)==expected,(argv_from(scp_log),expected)
                result.update(scenario=scenario,network_executed=False,stub_native_argv_verified=True)
                results.append(result)
    receipt={'schema':'portal-ppt-native-upload-independent-review.v1','status':'PASS','script_sha256':sha(template),
             'generator_sha256':sha(GENERATOR.read_bytes()),'inventory_sha256':sha(inventory_raw),
             'original_archive_mapping_stat_only':original_mapping,'compiler':compiled,'tests':results,'passed':len(results),
             'actual_ssh_scp_executed':False,'generator_executed':False,'large_archives_read_or_hashed':False,
             'server_files_changed':False,'host_archive_downloaded':False,'temporary_fixture_cleanup':'TemporaryDirectory complete'}
    receipt['fixture_environment']={'PSModulePath':'unset only in fixture child, so PS5/PS7 construct their own default module paths',
                                    'first_failure':'FIRST-FAILURE.json; cross-version module discovery, not upload-script behavior'}
    output=(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n').encode()
    (HERE/'REVIEW.json').write_bytes(output)
    print(json.dumps({'status':'PASS','tests':len(results),'review_sha256':sha(output),'bytes':len(output)}))


if __name__=='__main__':main()
