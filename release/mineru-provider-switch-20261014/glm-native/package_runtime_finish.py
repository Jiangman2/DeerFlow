"""Local publisher for the exact tested runtime recovery packet."""
import argparse
import json
from pathlib import Path
import tarfile
import repair_sqlite_build as safe

HERE=Path(__file__).resolve().parent
FILES=('prepare_runtime_finish.py','repair_sqlite_build.py','finish_runtime_build.py',
       'verify_runtime_candidate.py','runtime_check.py','Dockerfile.runtime-finish','RUNTIME-FINISH-RUNBOOK.txt')


def pack(output, verification):
    proof=json.loads(verification.read_bytes())
    safe.require(proof.get('local_tool_tests')=='PASS' and proof.get('linux_packet_rehearsal')=='PASS',
                 'local_rehearsal_required')
    safe.require(not output.exists(),'fresh_output_required')
    output.mkdir(parents=True); payload=output/'payload'; payload.mkdir()
    for name in FILES:
        safe.write_new(payload/name,(HERE/name).read_bytes().replace(b'\r\n',b'\n'))
    safe.write_new(payload/'LOCAL-VERIFICATION.json',verification.read_bytes())
    safe.write_new(payload/'PACKAGE.SHA256',''.join(safe.digest(p)+'  '+p.name+'\n'
        for p in sorted(payload.iterdir())).encode('ascii'))
    archive=output/'weknora-glm-runtime-finish-20261016-r1.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for p in sorted(payload.iterdir()): safe.add(t,p.name,source=p)
    starter=(HERE/'START-sqlite-fix-template.sh').read_text()
    starter=starter.replace('weknora-glm-sqlite-build-fix-20261016-r1.tar.gz',archive.name)
    starter=starter.replace('weknora-glm-sqlite-fix-20261016-r1','weknora-glm-runtime-finish-20261016-r1')
    starter=starter.replace('weknora-glm-sqlite-fix-XXXXXXXX','weknora-glm-runtime-fix-XXXXXXXX')
    starter=starter.replace('repair_sqlite_build.py','prepare_runtime_finish.py')
    starter=starter.replace('./build_weknora.py','./finish_runtime_build.py')
    starter=starter.replace('@PATCH_SHA256@',safe.digest(archive))
    safe.write_new(output/'START-weknora-glm-runtime-finish.sh',starter.encode())
    result={'runtime_finish_packet':'READY_FOR_SERVER_PREFLIGHT','archive_sha256':safe.digest(archive),
            'archive':str(archive),'starter_sha256':safe.digest(output/'START-weknora-glm-runtime-finish.sh'),
            'bytes':archive.stat().st_size,'compile_reuse':'REQUIRED_NOT_FALLBACK',
            'production_writes':0,'ready_for_rollout':False}
    safe.record(output/'PACKAGE-RESULT.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verification',type=Path,required=True)
    a=p.parse_args(); pack(a.output,a.verification)
