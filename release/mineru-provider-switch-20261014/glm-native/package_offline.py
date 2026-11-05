"""Local packaging only; no enterprise connection or container execution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
from assemble_offline_context import assemble, copy_exact, regular
from prepare_public_inputs import HERE, json_once, require, sha, write_once

ROOT = HERE.parents[2]
INFRA = ROOT/'release/production-build-alignment-20260928/r4-build'
FROZEN = ('isolated_buildkit.py', 'build_resources.py', 'bounded_runc.py', 'resource_canary.py',
          'prepare_oci.py', 'stage_base.py', 'verify_oci_base.py', 'verify_built_image.py', 'layer_graph.py')
TOOLS = ('build_weknora.py', 'build_guard.py', 'stage_context.py', 'verify_native_candidate.py',
         'verify_weknora_base.py', 'audit_build_inputs.py', 'SERVER-BUILD-RUNBOOK.txt')
BASELINE = {
    'deployment_uid':'44aca5b9-8fc1-44d6-af61-37c6d62723d5', 'generation':68,
    'spec_sha256':'a68a260e4a0d7b4f245c99c0c551012608aaa527e50acb235438eec02406ad72',
    'pod':'weknora-app-f6fb87c6d-gqtf5', 'pod_uid':'d64b460f-b27e-4a6f-afde-c5583b7d0455',
    'container_id':'containerd://8cfe3a38b2bf36f76091ea9534be15108a631c48ac38068f59b406920c89a810',
    'image':'deerflow.local/weknora-app:0.8.0-enterprise-proxy1-anydoc1-arm64-20260912',
    'image_id':'sha256:93d6900e88edcd1b5f389c5756f849b34f5051d832a72b0bc17b0af86c709194',
    'node':'datax-03', 'restarts':0}


def add(t, name, source=None, raw=None):
    require(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name, 'unsafe_package_path')
    if source is not None:
        regular(source)
    e = tarfile.TarInfo(name)
    e.size = source.stat().st_size if source is not None else len(raw)
    e.uid = e.gid = e.mtime = 0
    e.mode = 0o600
    with source.open('rb') if source is not None else io.BytesIO(raw) as stream:
        t.addfile(e, stream)


def pack(inputs, output, verification):
    require(not output.exists(), 'fresh_package_output_required')
    proof = json.loads(verification.read_bytes())
    require(proof.get('local_tool_tests') == 'PASS' and proof.get('linux_arm64_build') == 'NOT_EXECUTED', 'qualification_receipt')
    output.mkdir(parents=True, mode=0o700)
    context = output/'context'
    assemble(ROOT/'vendor/weknora-app-20260912', inputs, context)
    private = output/'package'
    private.mkdir(mode=0o700)
    for name in TOOLS:
        copy_exact(HERE/name, private/name)
    for name in FROZEN:
        copy_exact(INFRA/name, private/name)
    copy_exact(verification, private/'LOCAL-VERIFICATION.json')
    with tarfile.open(private/'context.tar', 'w:') as t:
        for f in sorted(context.rglob('*')):
            if f.is_file():
                add(t, f.relative_to(context).as_posix(), source=f)
    spec = json.loads((HERE/'BUILD-INPUT-LOCK.json').read_bytes())
    builder = dict(spec['builder'], image='weknora-glm-builder:1.26.8-bookworm-arm64')
    layout = inputs/'builder-oci'
    index = json.loads((layout/'index.json').read_bytes())
    require(len(index['manifests']) == 1 and index['manifests'][0]['digest'] == builder['manifest_digest'], 'builder_identity')
    index['manifests'][0]['annotations'] = {'org.opencontainers.image.ref.name':builder['image']}
    with tarfile.open(private/'builder.oci.tar', 'w:') as t:
        add(t, 'index.json', raw=json.dumps(index,sort_keys=True).encode())
        add(t, 'oci-layout', source=layout/'oci-layout')
        manifest = json.loads((layout/'blobs/sha256'/builder['manifest_digest'][7:]).read_bytes())
        digests = {builder['manifest_digest'], builder['config_digest']} | {x['digest'] for x in manifest['layers']}
        for d in sorted(digests):
            f = layout/'blobs/sha256'/d[7:]
            require(sha(f) == d[7:], 'builder_blob_corrupt')
            add(t, 'blobs/sha256/'+d[7:], source=f)
    builder['archive_sha256'] = sha(private/'builder.oci.tar')
    plan = {'scope':'WEKNORA_NATIVE_GLM_BUILD_ONLY', 'runtime_base':spec['runtime_base'],
            'builder':builder, 'production_baseline':BASELINE, 'context_sha256':sha(private/'context.tar'),
            'production_writes':0, 'provider_requests':0, 'ready_for_rollout':False,
            'frozen_infra_sha256':{n:sha(private/n) for n in FROZEN}}
    json_once(private/'BUILD-PLAN.json', plan)
    lines = ''.join(sha(p)+'  '+p.name+'\n' for p in sorted(private.iterdir()))
    write_once(private/'PACKAGE.SHA256', lines.encode('ascii'))
    archive = output/'weknora-glm-offline-build-20261016-r1.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=1) as t:
        for f in sorted(private.iterdir()):
            add(t, f.name, source=f)
    digest = sha(archive)
    template = (HERE/'START-template.sh').read_text(encoding='utf-8')
    write_once(output/'START-weknora-glm-build.sh', template.replace('@PACKAGE_SHA256@', digest).encode())
    copy_exact(HERE/'SERVER-BUILD-RUNBOOK.txt', output/'SERVER-BUILD-RUNBOOK.txt')
    result = {'offline_build_package':'READY_FOR_SERVER_PREFLIGHT', 'archive':str(archive),
              'sha256':digest, 'bytes':archive.stat().st_size,
              'start_sha256':sha(output/'START-weknora-glm-build.sh'),
              'context_sha256':plan['context_sha256'], 'candidate_image_built':False,
              'production_writes':0, 'ready_for_rollout':False}
    json_once(output/'PACKAGE-RESULT.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--inputs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--verification', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(pack(a.inputs.absolute(), a.output.absolute(), a.verification.absolute()), sort_keys=True), flush=True)
