#!/usr/bin/env python3
"""Build a fixed, local-only cache diagnostic catalog; never import an image.

Five retained OCI archives: seek headers and read small verified JSON only.
All expanded sizes come from pinned previous verification receipts. The server
Host tail deliberately remains unbound until its sealed metadata is inspected.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
MERGE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
PROFILE = PREP / 'native-preload-r1/PROFILE.json'
PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
INVENTORY_SHA = 'e6662fbebf2210d73de5f64498fe7548a718570ef303181016802ea2c118dbef'
OPERATION = 'e8104bf11adc4959ad0b6078e7c97990'
META_LIMIT = 4 * 1024 ** 2
PINS = {
    'gateway_base': (MERGE / 'backend-guards-r2/BASE-VERIFICATION-b2c1fecd51cf43c2b0832e31b5e2cecd.json', '4c1b8b5f579989a054d04cd608b9690f81cdc71562162f083546b38be8636643'),
    'runner_base': (MERGE / 'backend-guards-r2/BASE-VERIFICATION-9711b46518024a61bdfc11c732bb7e9a.json', '4d4e895228020c5499c9ff37fc8d15db3e74a6b637abdb346952cb28c6ebf62a'),
    'host_base': (PREP / 'host-base-evidence-r1/REPORT.json', '27f3658cb5933bf1a7f5f4b3b0173ff761b2ab9c60fe6f3db42c936167da1048'),
    'host_candidate': (PREP / 'host-candidate-evidence-r1/REPORT.json', 'dd054654163e7f9852f0a121419a93835b900adfb88085d90547ff0af07d1e5a'),
    'frontend': (MERGE / 'frontend-qualification-r3/FINAL-REPORT.json', 'fed9821ad99c17df02c881278cff540a97b16ff1caa72e76214021f8b3e60fea'),
    'node_base': (MERGE / 'frontend-inputs-r2/NODE-BASE-VERIFICATION.json', '15091808873ec081f49d6894ced89ef45241927c7cc613715a98a46f411b53d9'),
    'gateway-forward': (MERGE / 'backend-bound-build-r1/gateway.BUILD-RESULT.json', 'e6efcc6eb5b98de0e3afd40c1ade0df9df0bfa181897e8af1fde1b9d4e0b5744'),
    'gateway-rollback-compatible': (MERGE / 'backend-bound-build-r1/remainder-r3/gateway-rollback-compatible.BUILD-RESULT.json', '1365f860f5e3d66996c1b957c08d8e68daed9bb6499a1486e5ad66795f0555d5'),
    'ppt-master-runner-forward': (MERGE / 'backend-bound-build-r1/remainder-r3/runner.BUILD-RESULT.json', '660c4403bf556cf1ba80bf0485e8032f5f2c4c199b868d10a363fba2e9a9b1df'),
    'ppt-master-runner-rollback-compatible': (MERGE / 'backend-bound-build-r1/remainder-r3/runner-rollback-compatible.BUILD-RESULT.json', '54da5840793d655a286964794269fa82bd7686398ac5cacf9cada6d35571bd52'),
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value), 'invalid_digest')
    return value


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate_json_key')
        value[key] = item
    return value


def parse(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def pinned(path, expected, evidence):
    raw = path.read_bytes()
    require(len(raw) <= META_LIMIT and sha(raw) == expected, 'pinned_receipt_changed')
    evidence.append(dict(path=str(path), bytes=len(raw), sha256=expected))
    return parse(raw)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def small_oci_metadata(path, row):
    """No layer payload reads/hash/decompression. Metadata digests are verified."""
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(), 'archive_not_regular')
    require(before.st_size == row['archive_bytes'], 'archive_size_changed')
    wanted = {'index.json', 'blobs/sha256/' + row['manifest_digest'][7:],
              'blobs/sha256/' + row['config_digest'][7:]}
    found, blobs, seen = {}, {}, set()
    with path.open('rb') as stream:
        require(identity(before) == identity(os.fstat(stream.fileno())), 'archive_open_changed')
        with tarfile.open(fileobj=stream, mode='r:') as archive:
            for number, member in enumerate(archive):
                require(number < 4096 and member.name not in seen, 'archive_header_limit_or_duplicate')
                seen.add(member.name)
                require(not member.pax_headers and not member.linkname, 'archive_extended_or_link_entry')
                if member.isdir():
                    require(member.name.rstrip('/') in ('blobs', 'blobs/sha256'), 'unexpected_archive_directory')
                    continue
                require(member.isfile(), 'archive_non_regular')
                if member.name.startswith('blobs/sha256/'):
                    dig = digest('sha256:' + member.name[len('blobs/sha256/'):])
                    require(member.size > 0, 'empty_archive_blob')
                    blobs[dig] = member.size
                else:
                    require(member.name in ('oci-layout', 'index.json', 'manifest.json'), 'unexpected_archive_file')
                if member.name in wanted:
                    require(member.size <= META_LIMIT, 'metadata_size_limit')
                    found[member.name] = archive.extractfile(member).read(META_LIMIT + 1)
        require(identity(before) == identity(os.fstat(stream.fileno())) == identity(path.lstat()), 'archive_changed_during_read')
    require(set(found) == wanted, 'missing_metadata')
    index = parse(found['index.json'])
    require(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1, 'index_shape')
    desc = index['manifests'][0]
    require(desc.get('digest') == row['manifest_digest'], 'index_identity')
    annotations = desc.get('annotations', {})
    require(annotations.get('io.containerd.image.name') == row['tag'] and
            annotations.get('org.opencontainers.image.ref.name') == row['tag'].rsplit(':', 1)[1], 'index_tag_identity')
    manifest_raw = found['blobs/sha256/' + row['manifest_digest'][7:]]
    config_raw = found['blobs/sha256/' + row['config_digest'][7:]]
    require('sha256:' + sha(manifest_raw) == row['manifest_digest'] and len(manifest_raw) == desc['size'], 'manifest_identity')
    manifest = parse(manifest_raw)
    require(manifest.get('schemaVersion') == 2 and manifest['config']['digest'] == row['config_digest'], 'config_descriptor_identity')
    require('sha256:' + sha(config_raw) == row['config_digest'] and len(config_raw) == manifest['config']['size'], 'config_identity')
    config = parse(config_raw)
    require(config.get('os') == 'linux' and config.get('architecture') == 'arm64', 'platform_not_arm64')
    diffs = config.get('rootfs', {}).get('diff_ids')
    layers = manifest.get('layers')
    require(isinstance(diffs, list) and isinstance(layers, list) and 0 < len(layers) <= 512 and len(diffs) == len(layers), 'layer_shape')
    selected = []
    for descriptor, diff in zip(layers, diffs):
        dig = digest(descriptor.get('digest'))
        require(type(descriptor.get('size')) is int and descriptor['size'] == blobs.get(dig), 'layer_blob_header_size')
        selected.append(dict(blob_digest=dig, compressed_bytes=descriptor['size'], diff_id=digest(diff)))
    # Only safe projections escape this function; config/Env/commands never do.
    return selected, dict(path=str(path), bytes=before.st_size, all_outer_blob_count=len(blobs),
                          max_actual_blob_bytes=max(blobs.values()), manifest_sha256=row['manifest_digest'][7:],
                          config_sha256=row['config_digest'][7:], whole_archive_rehashed=False,
                          archive_identity_basis='PREVIOUS_PINNED_RECEIPT_PLUS_CURRENT_SIZE_AND_STABLE_STAT',
                          current_metadata_digests_verified=True, payloads_read_or_decompressed=False)


def exact_layers(report):
    result = []
    for pos, row in enumerate(report['layers']):
        require(row['number'] == pos + 1, 'receipt_layer_order')
        result.append(dict(blob_digest=digest(row['blob_digest']), compressed_bytes=row['blob_bytes'],
                           diff_id=digest(row['diff_id']), uncompressed_bytes_upper_bound=row['uncompressed_bytes'],
                           bound_kind='VERIFIED_EXACT'))
    return result


def verify_layer(actual, expected):
    require(actual['blob_digest'] == expected['blob_digest'] and actual['diff_id'] == expected['diff_id'], 'receipt_layer_identity')
    if 'compressed_bytes' in expected:
        require(actual['compressed_bytes'] == expected['compressed_bytes'], 'receipt_compressed_size')


def assemble(profile, receipts, inventory):
    layer_table, layer_lookup, images, observations = [], {}, [], []
    bases = {key: exact_layers(receipts[key + '_base']) for key in ('gateway', 'runner', 'host')}
    exact_by_diff = {}
    for layer in sum(bases.values(), []):
        value = layer['uncompressed_bytes_upper_bound']
        require(type(value) is int and value > 0, 'invalid_expanded_size')
        require(layer['diff_id'] not in exact_by_diff or exact_by_diff[layer['diff_id']] == value, 'diff_size_conflict')
        exact_by_diff[layer['diff_id']] = value
    for row in receipts['node_base']['layers']:
        require(row['diff_id_verified'] and row['blob_sha_and_size_verified'], 'node_receipt_incomplete')
        exact_by_diff[row['diff_id']] = row['uncompressed_bytes']
    inventory_by_role = {row['role']: row for row in inventory['images']}
    for row in profile['images']:
        role = row['role']
        known = inventory_by_role[role]
        for field in ('tag', 'manifest_digest', 'config_digest', 'platform'):
            require(known[field] == row[field], 'inventory_profile_identity_mismatch')
        require(known['archive']['sha256'] == row['archive_sha256'] and known['archive']['bytes'] == row['archive_bytes'], 'inventory_archive_mismatch')
        out = {key: row[key] for key in ('role', 'basename', 'archive_sha256', 'archive_bytes', 'manifest_digest', 'config_digest', 'tag', 'digest_reference', 'platform')}
        out['source_sealed_path'] = '/opt/portal-ppt-native-sealed-r2-' + OPERATION + '/' + row['basename']
        out['expanded_image_upper_bound'] = row['expanded_bytes_upper_bound']
        out['expanded_groups'] = []
        if role == 'ppt-master-agent-host-forward':
            host = receipts['host_candidate']
            require(host['manifest_digest'] == row['manifest_digest'] and host['config_digest'] == row['config_digest'] and host['artifact']['sha256'] == row['archive_sha256'], 'host_receipt_identity')
            require(host['artifact']['layer_descriptors'] == 129 and host['base_layers_verified'] == 128, 'host_layer_count')
            chosen = bases['host']
            out['expected_layer_count'] = 129
            out['tail_metadata_required'] = dict(position=128, count=1, uncompressed_bytes_upper_bound=8 * 1024 ** 2,
                                                 bound_kind='FROZEN_APPEND_ENGINE_MAXIMUM',
                                                 blob_digest=None, compressed_bytes=None, diff_id=None)
        else:
            path = Path(known['archive']['path'])
            selected, observation = small_oci_metadata(path, row)
            observations.append(observation)
            out['expected_layer_count'] = len(selected)
            out['tail_metadata_required'] = None
            chosen = []
            if role == 'frontend':
                report = receipts['frontend']
                require(report['oci']['sha256'] == row['archive_sha256'] and report['oci']['exact_qualified_identity']['manifest_digest'] == row['manifest_digest'], 'frontend_receipt_identity')
                require(len(selected) == report['oci']['layers'], 'frontend_count')
                unknown = [i for i, item in enumerate(selected) if item['diff_id'] not in exact_by_diff]
                remaining = report['oci']['uncompressed_layer_bytes'] - sum(exact_by_diff.get(item['diff_id'], 0) for item in selected)
                require(remaining > 0 if unknown else remaining == 0, 'frontend_group_size')
                for i, item in enumerate(selected):
                    item = dict(item)
                    item['uncompressed_bytes_upper_bound'] = remaining if i in unknown else exact_by_diff[item['diff_id']]
                    item['bound_kind'] = 'GROUP_TOTAL_UPPER_BOUND' if i in unknown else 'VERIFIED_EXACT'
                    chosen.append(item)
                if unknown:
                    out['expanded_groups'].append(dict(layer_positions=unknown, uncompressed_bytes_upper_bound=remaining,
                                                      rule='SUM_OF_MISSING_MEMBERS_AT_MOST_GROUP_TOTAL_NOT_PER_MEMBER_SUM'))
            else:
                base_name = 'gateway' if role.startswith('gateway') else 'runner'
                base = bases[base_name]
                verification = receipts[role]['verification']
                require(verification['manifest_digest'] == row['manifest_digest'] and verification['config_digest'] == row['config_digest'], 'overlay_receipt_identity')
                added = verification['added_layers']
                require(len(selected) == len(base) + len(added), 'overlay_layer_count')
                for i, item in enumerate(selected):
                    expected = base[i] if i < len(base) else added[i - len(base)]
                    verify_layer(item, expected)
                    item = dict(item)
                    item['uncompressed_bytes_upper_bound'] = expected.get('uncompressed_bytes_upper_bound', expected.get('uncompressed_bytes'))
                    item['bound_kind'] = 'VERIFIED_EXACT'
                    chosen.append(item)
                require(sum(x['uncompressed_bytes_upper_bound'] for x in chosen) == row['expanded_bytes_upper_bound'], 'overlay_expanded_total')
        indices = []
        for item in chosen:
            key = canonical(item)
            if key not in layer_lookup:
                layer_lookup[key] = len(layer_table)
                layer_table.append(item)
            indices.append(layer_lookup[key])
        out['layer_indices'] = indices
        images.append(out)
    require(len(images) == 6, 'six_images_required')
    return dict(schema='portal-ppt-native-cache-catalog.v1', operation=OPERATION, profile_sha256=PROFILE_SHA,
                status='FIXED_LOCAL_METADATA_HOST_TAIL_REQUIRES_SOURCE_SEALED_METADATA', layers=layer_table, images=images,
                source_metadata_requirement='SEEK_ALL_SIX_SEALED_HEADERS_AND_HASH_SMALL_MANIFEST_CONFIG_BEFORE_REMOTE_DIAGNOSTIC',
                scratch_rule='MAX_ACTUAL_SIZE_OF_ALL_OUTER_BLOBS_IN_ALL_SIX_ARCHIVES_INCLUDING_NONREFERENCED',
                chain_rule='FIRST_DIFF_ID_THEN_SHA256_UTF8_PREVIOUS_CHAIN_SPACE_NEXT_DIFF_ID',
                capacity_pass=False, ready_for_rollout=False), observations


def main():
    evidence = []
    profile = pinned(PROFILE, PROFILE_SHA, evidence)
    inventory = pinned(MERGE / 'CANDIDATE-IMAGE-INVENTORY.json', INVENTORY_SHA, evidence)
    receipts = {key: pinned(path, expected, evidence) for key, (path, expected) in PINS.items()}
    catalog, observations = assemble(profile, receipts, inventory)
    raw = canonical(catalog)
    review = dict(schema='portal-ppt-native-cache-catalog-review.v1', status='LOCAL_METADATA_AND_PINNED_RECEIPTS_MATCH',
                  catalog_sha256=sha(raw), catalog_bytes=len(raw), builder_sha256=sha(Path(__file__).read_bytes()),
                  evidence=evidence, archive_metadata_observations=observations,
                  image_layer_counts={row['role']: row['expected_layer_count'] for row in catalog['images']},
                  global_distinct_layer_rows=len(catalog['layers']),
                  source_header_and_host_tail_completion_required=True,
                  frontend_unknown_layers_use_verified_aggregate_bound=True,
                  archive_payloads_rehashed_or_decompressed=False, server_access=False,
                  images_imported_or_executed=False, cache_reuse_or_capacity_pass=False)
    for name, data in [('CATALOG.json', raw), ('CATALOG-REVIEW.json', canonical(review))]:
        with (HERE / name).open('xb') as stream:
            stream.write(data)
    print(json.dumps(dict(status=review['status'], catalog_bytes=len(raw), catalog_sha256=sha(raw),
                          review_sha256=sha(canonical(review)), layer_rows=len(catalog['layers']))))


if __name__ == '__main__':
    main()
