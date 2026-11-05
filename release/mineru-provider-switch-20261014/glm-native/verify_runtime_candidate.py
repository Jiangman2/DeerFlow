"""Preserve all previous compile gates; accept exactly five pinned dictionaries."""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import verify_native_candidate as old
from build_guard import checksum, require
from prepare_oci import extract
from verify_oci_base import document, verify
from verify_built_image import configuration
from verify_weknora_base import verify as verify_archive

DICT_DIR = 'usr/local/share/weknora/jieba'
ENV = 'JIEBA_DICT_DIR=/' + DICT_DIR


def preserved_with_dictionary(base, candidate):
    expected = copy.deepcopy(base.get('config', {}))
    values = expected.get('Env', [])
    matches = [i for i, value in enumerate(values) if value.startswith('JIEBA_DICT_DIR=')]
    require(len(matches) <= 1, 'duplicate_base_dictionary_env')
    if matches:
        # A pre-existing nonempty operator choice requires separate review.
        require(values[matches[0]] in ('JIEBA_DICT_DIR=', ENV), 'base_dictionary_override_requires_review')
        values[matches[0]] = ENV
    else:
        values.append(ENV)
    expected['Env'] = values
    require(candidate.get('config') == expected, 'unexpected_runtime_config_delta')
    before, after = base['rootfs']['diff_ids'], candidate['rootfs']['diff_ids']
    require(len(after) > len(before) and after[:len(before)] == before, 'base_diffids_changed')


def delta(blob, layer, expected, seen):
    parents = {'.'}
    for name in expected:
        parents.update(str(p).replace('\\', '/') for p in Path(name).parents if str(p) != '.')
    with blob.open('rb') as raw:
        stream = gzip.GzipFile(fileobj=raw) if layer['mediaType'].endswith('gzip') else raw
        with tarfile.open(fileobj=stream, mode='r|') as tar:
            local = set()
            for m in tar:
                name = m.name[2:] if m.name.startswith('./') else m.name
                name = name.rstrip('/')
                require(name not in local and len(local) < 40 and not m.issparse() and
                        not m.linkname and m.uid == m.gid == 0 and not m.mode & 0o6022,
                        'unsafe_runtime_delta')
                local.add(name)
                if m.isdir():
                    require(name in parents and m.mode == 0o755, 'unexpected_runtime_directory')
                    continue
                require(m.isfile() and name in expected and name not in seen,
                        'unexpected_runtime_file')
                wanted = expected[name]
                require(m.mode == wanted['mode'] and 0 < m.size <= 1024**3, 'runtime_file_mode_or_size')
                h = hashlib.sha256()
                with tar.extractfile(m) as f:
                    for block in iter(lambda: f.read(1024**2), b''):
                        h.update(block)
                require(h.hexdigest() == wanted['sha256'], 'runtime_file_hash')
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
    binaries, suites = old.evidence(a.evidence)  # Includes SQLite, DWARF4, all old test suites.
    runtime = plan['runtime_finish']
    require(binaries == runtime['binaries'], 'reused_binary_receipt_changed')
    dictionaries = runtime['dictionaries']
    names = {'jieba.dict.utf8', 'hmm_model.utf8', 'user.dict.utf8', 'idf.utf8', 'stop_words.utf8'}
    require(set(dictionaries) == names, 'dictionary_set_changed')
    expected = {name: {'sha256': sha, 'mode': 0o755} for name, sha in binaries.items()}
    expected.update({DICT_DIR+'/'+name: {'sha256': sha, 'mode': 0o644}
                     for name, sha in dictionaries.items()})
    layout = extract(a.archive, a.output)
    result = verify(layout, {'source_image': a.image,
                            'source_image_id': document(a.metadata)['containerimage.digest']})
    before = document(a.base/'blobs/sha256'/document(a.base_receipt)['manifest_digest'][7:])
    after = document(layout/'blobs/sha256'/result['manifest_digest'][7:])
    require(len(before['layers']) == 20 and after['layers'][:20] == before['layers']
            and 1 <= len(after['layers'])-20 <= 4, 'runtime_base_or_delta_layers')
    preserved_with_dictionary(configuration(a.base, plan['runtime_base']['manifest_digest']),
                              configuration(layout, result['manifest_digest']))
    checked = verify_archive(a.archive, lambda r: print(json.dumps(r), flush=True),
        result['manifest_digest'], result['config_digest'], len(after['layers']), result['compressed_layer_bytes'])
    seen = set()
    for layer in after['layers'][20:]:
        delta(layout/'blobs/sha256'/layer['digest'][7:], layer, expected, seen)
    require(seen == set(expected), 'runtime_delta_incomplete')
    result.update(image=a.image, archive_sha256=checksum(a.archive), base_layer_prefix='PRESERVED',
        runtime_config='ONLY_JIEBA_DICT_DIR_ADDED_OR_EMPTY_VALUE_REPLACED', runtime_files=expected,
        test_suites=suites, reused_binary_hashes='PASS', uncompressed_diffids='PASS',
        uncompressed_layer_bytes=checked['uncompressed_layer_bytes'], provider_requests=0,
        native_intranet_acceptance='NOT_TESTED', ready_for_rollout=False)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
