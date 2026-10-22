"""Offline ABI/test receipts and exact three-binary OCI delta. Python3.7+."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile
from build_guard import checksum, require, safe_file
from prepare_oci import extract
from verify_oci_base import document, verify
from verify_built_image import configuration, preserved
from verify_weknora_base import verify as verify_archive

BINARIES = {'/out/WeKnora':'app/WeKnora',
            '/out/weknora-anydoc-probe':'usr/local/bin/weknora-anydoc-probe',
            '/out/weknora-glm-probe':'usr/local/bin/weknora-glm-probe'}


def evidence(root):
    require((root/'sqlite-build-preflight.txt').read_text().strip() ==
            'sqlite_header_link_and_cgo=PASS builder_only=true database_configuration_changed=false',
            'sqlite_build_preflight_missing')
    hashes = {}
    for line in (root/'binaries.sha256').read_text(encoding='ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  (/out/[A-Za-z0-9-]+)', line)
        require(m is not None and m[2] in BINARIES and m[2] not in hashes, 'binary_hash_receipt')
        hashes[m[2]] = m[1]
    require(set(hashes) == set(BINARIES), 'binary_receipt_incomplete')
    suites = {}
    for name in ('vlm-tests.jsonl', 'anydoc-tests.jsonl', 'ssrf-regression.jsonl'):
        rows = [json.loads(x) for x in (root/name).read_text(encoding='utf-8').splitlines() if x.strip()]
        require(rows and not any(x.get('Action') == 'fail' for x in rows), 'failed_test_receipt:'+name)
        passed = {x.get('Package') for x in rows if x.get('Action') == 'pass' and not x.get('Test')}
        started = {x.get('Package') for x in rows if x.get('Action') == 'start'}
        require(started and started == passed, 'incomplete_test_packages:'+name)
        suites[name] = {'passed_tests':sum(x.get('Action') == 'pass' and bool(x.get('Test')) for x in rows),
                        'skipped_tests':sum(x.get('Action') == 'skip' and bool(x.get('Test')) for x in rows)}
        require(suites[name]['passed_tests'] > 0, 'empty_suite:'+name)
        if name == 'vlm-tests.jsonl':
            native = [x for x in rows if 'EnterpriseGLM' in x.get('Test', '')]
            require(any(x.get('Action') == 'pass' for x in native) and
                    not any(x.get('Action') == 'skip' for x in native), 'native_glm_tests_missing_or_skipped')
    for rel in ('anydoc-self-test.json', 'runtime/anydoc.json'):
        r = json.loads((root/rel).read_bytes())
        require(r.get('available') and r.get('self_test') and r.get('version') == '0.1.9' and
                r.get('markdown_bytes', 0) > 0 and not r.get('ocr_required') and not r.get('assets_error'), 'anydoc_smoke')
    require((root/'runtime/PASS.txt').read_text().strip() == 'runtime_loader_and_anydoc=PASS provider_requests=0',
            'runtime_smoke_missing')
    require((root/'go-mod-verify.txt').read_text().strip() == 'all modules verified', 'go_mod_verify')
    require('AArch64' in (root/'server-elf.txt').read_text(), 'server_elf_arch')
    return {BINARIES[k]:v for k,v in hashes.items()}, suites


def delta(blob, layer, expected, seen):
    with blob.open('rb') as raw:
        stream = gzip.GzipFile(fileobj=raw) if layer['mediaType'].endswith('gzip') else raw
        with tarfile.open(fileobj=stream, mode='r|') as t:
            local = set()
            for m in t:
                name = m.name[2:] if m.name.startswith('./') else m.name
                name = name.rstrip('/')
                require(name not in local and len(local) < 12, 'excess_delta_members')
                local.add(name)
                require(m.uid == m.gid == 0 and not m.mode & 0o6022 and not m.issparse()
                        and not m.linkname, 'unsafe_delta_metadata')
                if m.isdir():
                    require(name in ('.', 'app', 'usr', 'usr/local', 'usr/local/bin'), 'unexpected_delta_directory')
                    continue
                require(m.isfile() and name in expected and name not in seen and m.mode == 0o755
                        and 0 < m.size < 1024**3, 'unexpected_binary_delta')
                h = hashlib.sha256()
                with t.extractfile(m) as f:
                    for b in iter(lambda:f.read(1024**2), b''):
                        h.update(b)
                require(h.hexdigest() == expected[name], 'binary_delta_hash')
                seen.add(name)
        if stream is not raw:
            stream.close()


def main():
    p = argparse.ArgumentParser()
    for name in ('archive', 'output', 'metadata', 'base', 'base-receipt', 'plan', 'evidence'):
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--image', required=True)
    a = p.parse_args()
    plan = document(a.plan)
    expected, suites = evidence(a.evidence)
    layout = extract(a.archive, a.output)
    result = verify(layout, {'source_image':a.image, 'source_image_id':document(a.metadata)['containerimage.digest']})
    before = document(a.base/'blobs/sha256'/document(a.base_receipt)['manifest_digest'][7:])
    after = document(layout/'blobs/sha256'/result['manifest_digest'][7:])
    old, new = before['layers'], after['layers']
    require(len(old) == 20 and new[:20] == old and 1 <= len(new)-20 <= 3, 'base_prefix_or_delta_count')
    preserved(configuration(a.base, plan['runtime_base']['manifest_digest']), configuration(layout, result['manifest_digest']))
    checked = verify_archive(a.archive, lambda r:print(json.dumps(r), flush=True),
        result['manifest_digest'], result['config_digest'], len(new), result['compressed_layer_bytes'])
    seen = set()
    for layer in new[20:]:
        delta(layout/'blobs/sha256'/layer['digest'][7:], layer, expected, seen)
    require(seen == set(expected), 'missing_binary_delta')
    result.update(image=a.image, archive_sha256=checksum(a.archive), base_layer_prefix='PRESERVED',
                  runtime_config='PRESERVED', binary_sha256=expected, test_suites=suites,
                  uncompressed_diffids='PASS', uncompressed_layer_bytes=checked['uncompressed_layer_bytes'],
                  provider_requests=0, native_intranet_acceptance='NOT_TESTED', ready_for_rollout=False)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
