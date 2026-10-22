"""Real mount(2) smoke test only in this disposable container's private namespace."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import tempfile


def main():
    assert Path('/.dockerenv').exists() and os.geteuid()==0
    spec=importlib.util.spec_from_file_location('stage_under_test','/src/stage.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with tempfile.TemporaryDirectory(dir='/qualify') as td:
        base=Path(td);source=base/'source';anchor=base/'anchor';target=anchor/'probe'
        source.mkdir(mode=0o770);source.chmod(0o770);os.chown(source,1000,1000)
        anchor.mkdir(mode=0o755);target.mkdir(mode=0o700)
        assert str(target.resolve()).startswith('/qualify/') and not target.is_symlink()
        fd=os.open(str(source),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        mounted=False
        try:
            m.mount_call('/proc/self/fd/'+str(fd),str(target),4096);mounted=True
            m.mount_call(None,str(target),4096|32|2|4|8)
            m.mount_call(None,str(target),1<<18)
            assert m.signature(source.stat())==m.signature(target.stat())
            row=[r for r in m.mounts() if r['point']==str(target)]
            assert len(row)==1 and {'rw','nosuid','nodev','noexec'}<=row[0]['options']
            assert not row[0]['propagation'] and row[0]['fs']=='tmpfs'
            result=dict(bind_mount_syscall='PASS',source_fd_to_target_inode='PASS',
                        flags=['rw','nosuid','nodev','noexec','private'],filesystem_fixture='tmpfs',
                        nfs_exports='NOT_EXECUTED',network='DISABLED')
        finally:
            os.close(fd)
            if mounted:
                # Exact owned synthetic mount only; never a host or production path.
                assert str(target.resolve()).startswith('/qualify/')
                lib=ctypes.CDLL(None,use_errno=True)
                lib.umount2.argtypes=(ctypes.c_char_p,ctypes.c_int);lib.umount2.restype=ctypes.c_int
                assert lib.umount2(os.fsencode(target),0)==0
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
