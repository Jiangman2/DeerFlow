"""Canary-only shared storage protocol; never reads or edits the live display."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types


class Refused(RuntimeError): pass


def require(value, code):
    if not value: raise Refused(code)


def regular(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and not path.is_symlink() and s.st_size <= 131072, 'CANARY_FILE_CHANGED')
    return s


def execute(args):
    op, mode = args['operation'], args['mode']
    require(re.fullmatch('[a-f0-9]{32}', op), 'OPERATION_INVALID')
    require(mode in ('initialize','update_b','stale_a','hold_a','contend_b','final_a','read_b','cleanup'), 'MODE_INVALID')
    root = Path('/app/backend/.deer-flow')
    require(root.is_dir() and not any(p.is_symlink() for p in (root,*root.parents)), 'ROOT_CHANGED')
    work = root/('.combined-storage-check-'+op)
    require(hashlib.sha256(args['source'].encode()).hexdigest()==args['sha256'], 'CANDIDATE_CHANGED')
    sys.path.insert(0, '/app/backend')
    module = types.ModuleType('release_competition_store'); module.__file__='<sealed-store-canary>'
    sys.modules[module.__name__]=module
    exec(compile(args['source'],module.__file__,'exec'),module.__dict__)
    os.environ['COMPETITION_DISPLAY_PATH']=str(work/'display.json')
    if mode=='initialize':
        work.mkdir(mode=0o700)
        with (work/'nonce').open('x') as f: f.write(op); f.flush(); os.fsync(f.fileno())
    require(work.is_dir() and not work.is_symlink(), 'CANARY_DIRECTORY_CHANGED')
    # st_dev is mount-local and may differ between nodes for the same NFS inode.
    identity={k:getattr(work.stat(),k) for k in ('st_ino','st_uid')}
    if mode!='initialize':require(identity==args['directory_identity'], 'CANARY_IDENTITY_CHANGED')
    regular(work/'nonce');require((work/'nonce').read_text()==op, 'CANARY_NONCE_CHANGED')
    result=dict(status='PASS',mode=mode,operation=op,directory_identity=identity,production_display_access=False,
        database_access=False,provider_requests=0,workload_modified=False)
    if mode=='initialize':
        require(module.read_display().revision==0 and not (work/'display.json').exists(), 'GET_CREATED_FILE')
        saved=module.save_display(module.DisplayConfig(title='synthetic-a'), 'release-check-a')
        require(saved.revision==1,'FIRST_REVISION')
    elif mode=='update_b':
        before=module.read_display(); require(before.revision==1 and before.title=='synthetic-a','CROSS_NODE_READ_A')
        before.title='synthetic-b'; saved=module.save_display(before,'release-check-b');require(saved.revision==2,'SECOND_REVISION')
    elif mode=='stale_a':
        before=module.read_display();require(before.revision==2 and before.title=='synthetic-b','CROSS_NODE_READ_B')
        try:module.save_display(module.DisplayConfig(revision=1,title='must-not-win'),'release-check-a')
        except module.DisplayConflict: result['stale_revision_rejected']=True
        else:raise Refused('STALE_WRITE_ACCEPTED')
    elif mode=='hold_a':
        with module._lock(work/'display.lock'):
            with (work/'held').open('x') as f:f.write(op)
            end=time.monotonic()+35
            while not (work/'release').exists():
                require(time.monotonic()<end,'HOLDER_TIMEOUT');time.sleep(.1)
            regular(work/'release');require((work/'release').read_text()==op,'RELEASE_CHANGED')
        result['lock_released']=True
    elif mode=='contend_b':
        end=time.monotonic()+15
        while not (work/'held').exists():
            require(time.monotonic()<end,'HOLDER_NOT_READY');time.sleep(.1)
        regular(work/'held');require((work/'held').read_text()==op,'HELD_CHANGED')
        start=time.monotonic()
        try:
            module.save_display(module.DisplayConfig(revision=2,title='must-not-win'),'release-check-b')
        except module.DisplayStoreError as exc:
            require(str(exc)=='display_edit_busy','UNEXPECTED_LOCK_ERROR')
            result['contended_write_rejected']=True
        else:raise Refused('CROSS_NODE_LOCK_NOT_ENFORCED')
        finally:
            with (work/'release').open('x') as f:f.write(op)
        result['lock_wait_seconds']=round(time.monotonic()-start,3)
        require(module.read_display().revision==2,'CONTENTION_MUTATED_DATA')
    elif mode=='final_a':
        before=module.read_display(); require(before.revision==2,'PRE_FINAL_REVISION')
        before.title='synthetic-final';saved=module.save_display(before,'release-check-a');require(saved.revision==3,'FINAL_REVISION')
    elif mode=='read_b':
        final=module.read_display();require(final.revision==3 and final.title=='synthetic-final','ATOMIC_REPLACE_NOT_VISIBLE')
        result['final_revision']=3
    elif mode=='cleanup':
        require(module.read_display().revision==3,'CLEANUP_BEFORE_ACCEPTANCE')
        expected={'nonce','display.json','display.lock','held','release'}
        require({p.name for p in work.iterdir()}==expected,'EXTRA_CANARY_FILES')
        for name in expected:regular(work/name)
        for name in expected:(work/name).unlink()
        work.rmdir();result['canary_removed']=True
    return result


def main():
    try:
        args=json.load(sys.stdin)
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):result=execute(args)
    except Exception as exc:
        result=dict(status='INCOMPLETE',error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'STORAGE_CHECK_FAILED',raw_errors_exported=False)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
