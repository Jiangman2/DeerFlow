"""Assemble only the reviewed delta, never the dirty checkout or user PDF."""
import ast
import base64
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
NAME = 'deerflow-channel-pdf-preview-20260923-r1'
GATEWAY = [
    'app/gateway/routers/artifacts.py', 'app/gateway/routers/channel_connections.py',
    'packages/harness/deerflow/constants.py',
    *['app/channels/' + name + '.py' for name in ('feishu','telegram','slack','discord','dingtalk','wechat','wecom','manager')],
]
FRONTEND = [
    'src/components/workspace/artifacts/artifact-file-detail.tsx',
    'src/components/workspace/settings/channels-settings-page.tsx',
    'src/components/workspace/channels/channel-provider-icon.tsx',
    'src/components/workspace/channels/channel-runtime-config-dialog.tsx',
    'src/components/workspace/channels/workspace-channels-list.tsx',
    'src/core/channels/provider-state.ts',
    'src/core/i18n/locales/en-US.ts', 'src/core/i18n/locales/zh-CN.ts',
]


def delta(group, paths):
    entries, originals = [], {}
    for name in paths:
        rel = ('backend' if group == 'gateway' else 'frontend') + '/' + name
        before = subprocess.check_output(['git', 'show', 'HEAD:' + rel], cwd=REPO).decode('utf-8').replace('\r\n','\n')
        after = (REPO / rel).read_text(encoding='utf-8')
        assert before != after, 'expected reviewed diff: ' + rel
        originals[name] = before
        edits = []
        a,b = before.splitlines(keepends=True), after.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None,a,b,autojunk=False)
        for hunk in matcher.get_grouped_opcodes(5):
            old=''.join(a[hunk[0][1]:hunk[-1][2]])
            new=''.join(b[hunk[0][3]:hunk[-1][4]])
            assert old and new and before.count(old)==1 and after.count(new)==1, rel
            edits.append({'before':old,'after':new})
        check=before
        for edit in edits:
            assert check.count(edit['before'])==1
            check=check.replace(edit['before'],edit['after'],1)
        assert check==after, rel
        entries.append({'path':name,'edits':edits})
    return entries,originals


def compressed_fixtures():
    source=(REPO/'backend/tests/test_pdf_preview_object_streams.py').read_text(encoding='utf-8')
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='compressed_pdf')
    scope={}
    exec('import zlib, struct\n'+ast.get_source_segment(source,function),scope)
    make=scope['compressed_pdf']
    cases={'normal':b'', 'javascript':b'/OpenAction << /S /JavaScript /JS (1) >>',
           'launch':b'/OpenAction << /S /Launch /F (app.exe) >>',
           'additional':b'/AA << /WC << /S /JavaScript /JS (1) >> >>',
           'embedded':b'/Names << /EmbeddedFiles << /Names [] >> >>',
           'xfa':b'/AcroForm << /XFA (form data) >>'}
    return {name:base64.b64encode(make(extra)).decode('ascii') for name,extra in cases.items()}


def validate_engines(root, baselines):
    for group, originals in baselines.items():
        with tempfile.TemporaryDirectory(prefix='channel-pdf-patch-test-') as directory:
            base=Path(directory)
            for name,source in originals.items():
                target=base/name
                target.parent.mkdir(parents=True,exist_ok=True)
                # A real later change outside the matched hunks must survive.
                target.write_text(source+ ('\n# untouched sentinel\n' if group=='gateway' else '\n// untouched sentinel\n'),encoding='utf-8')
            command=[sys.executable,str(root/'apply-delta.py')] if group=='gateway' else ['node',str(root/'apply-delta.mjs')]
            command += [str(base),str(root/(group+'-delta.json')),str(base/'receipt.json')]
            subprocess.run(command,check=True)
            subprocess.run(command,check=True)  # already applied: must be idempotent
            for name in originals:
                actual=(base/name).read_text(encoding='utf-8')
                rel=('backend' if group=='gateway' else 'frontend')+'/'+name
                assert actual.startswith((REPO/rel).read_text(encoding='utf-8'))
                assert 'untouched sentinel' in actual
            first=base/next(iter(originals))
            first.write_text('# incompatible baseline\n',encoding='utf-8')
            other={name:(base/name).read_bytes() for name in list(originals)[1:]}
            result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            assert result.returncode!=0, 'must reject mismatched baseline'
            assert all((base/name).read_bytes()==data for name,data in other.items()), 'partial mutation on mismatch'
    print('exact_delta_engines=PASS idempotent=yes mismatch_rejected=yes unrelated_changes_preserved=yes')


def package(output_directory):
    output=output_directory/(NAME+'.tar.gz')
    checksum=output.with_name(output.name+'.sha256')
    if output.exists() or checksum.exists():
        raise SystemExit('Refusing to overwrite an existing release: '+str(output))
    with tempfile.TemporaryDirectory(prefix='channel-pdf-package-') as temporary:
        root=Path(temporary)/NAME
        root.mkdir()
        for item in HERE.iterdir():
            if item.is_file() and item.name!='package.py':
                shutil.copyfile(item,root/item.name)
        # Reuse the audited isolated-build lifecycle, not the PPT rollout script.
        source=REPO/'.codex-tmp/ppt-v660-r15-build-utf8/deerflow-ppt-master-v660-20260923-r15/isolated-buildkit.sh'
        isolated=source.read_text(encoding='utf-8')
        isolated=isolated.replace('    if pod.get("metadata",{}).get("deletionTimestamp"): continue\n','')
        isolated=isolated.replace("'[c]tr -n k8s.io images import|[b]uildctl .* build|[n]erdctl .* build'", "'[c]tr .*images import|[b]uildctl .*build|[n]erdctl .*build'")
        isolated=isolated.replace('kubectl get node','kubectl --request-timeout=30s get node').replace('kubectl -n deer-flow','kubectl --request-timeout=30s -n deer-flow')
        (root/'isolated-buildkit.sh').write_text(isolated,encoding='utf-8',newline='\n')
        baselines={}
        for group,paths in (('gateway',GATEWAY),('frontend',FRONTEND)):
            entries,baselines[group]=delta(group,paths)
            (root/(group+'-delta.json')).write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
        (root/'compressed-fixtures.json').write_text(json.dumps(compressed_fixtures()),encoding='utf-8')
        shutil.copyfile(REPO/'frontend/public/images/cnooc-haineng-mark.png',root/'cnooc-haineng-mark.png')
        shutil.copyfile(REPO/'docs/channel-pdf-preview-hotfix.md',root/'VERIFICATION.md')
        # Ensure a POSIX roundtrip; no CRLF shell scripts or Windows modes.
        for item in root.iterdir():
            if item.suffix!='.png':
                item.write_text(item.read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
        validate_engines(root,baselines)
        manifest=''.join(hashlib.sha256(item.read_bytes()).hexdigest()+'  '+item.name+'\n' for item in sorted(root.iterdir()) if item.is_file())
        (root/'PACKAGE.SHA256').write_text(manifest,encoding='ascii',newline='\n')
        assert b'\r' not in (root/'PACKAGE.SHA256').read_bytes()
        subprocess.run([sys.executable,str(root/'verify-package.py'),str(root)],check=True)
        def normalized(info):
            info.uid=info.gid=0; info.uname=info.gname='root'
            info.mode=0o755 if info.isdir() or info.name.endswith('.sh') else 0o644
            return info
        with output.open('xb') as archive_file:
            with tarfile.open(fileobj=archive_file,mode='w:gz',format=tarfile.PAX_FORMAT) as archive:
                archive.add(root,arcname=NAME,filter=normalized)
        with checksum.open('x',encoding='ascii',newline='\n') as target:
            target.write(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name+'\n')
        assert b'\r' not in checksum.read_bytes()
    print('candidate_build_package='+str(output))
    print('production_rollout=NOT_READY requires_arm64_build_and_runtime_acceptance=yes')


if __name__=='__main__':
    package(Path(sys.argv[1]).resolve())
