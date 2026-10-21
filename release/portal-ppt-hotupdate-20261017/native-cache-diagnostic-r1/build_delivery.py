"""Assemble the single-file, read-only diagnostic; no server connection."""
import base64
import hashlib
import json
from pathlib import Path
import types
import zlib

HERE = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def build():
    catalog_raw = (HERE / 'CATALOG.json').read_bytes()
    assert sha(catalog_raw) == 'f68a13ce2bd6f50746ccdea53bf2f7b99627f2579c34f9f81b8d1e6b968ab5df'
    catalog = json.loads(catalog_raw)
    canonical = json.dumps(catalog, sort_keys=True, separators=(',', ':')).encode()
    code = (HERE / 'collect_cache_template.py').read_text(encoding='utf-8')
    code = code.replace('@@CATALOG_SHA@@', sha(canonical)).replace(
        '@@CATALOG_Z64@@', base64.b64encode(zlib.compress(canonical, 9)).decode())
    assert '@@CATALOG_' not in code
    compile(code, 'collect_cache.py', 'exec')
    module = types.ModuleType('diagnostic_local_qualification')
    exec(compile(code, 'collect_cache.py', 'exec'), module.__dict__)
    assert module.unpack(module.CATALOG_Z64) == catalog
    rows = module.catalog_rows(catalog)
    assert len(rows) == 6
    # Exercise the exact transport's size using every fixed chain plus one bound
    # synthetic Host tail. This does not assert the unseen tail's real identity.
    images, chains, blobs = [], {}, {}
    for row, known in rows.values():
        layers, parent = [], ''
        for layer in known:
            chain = layer['diff_id'] if not parent else 'sha256:' + sha((parent + ' ' + layer['diff_id']).encode())
            value = dict(chain_id=chain, parent=parent,
                         uncompressed_bytes_upper_bound=layer['uncompressed_bytes_upper_bound'])
            chains[chain] = value
            layers.append(value)
            blobs[layer['blob_digest']] = layer['compressed_bytes']
            parent = chain
        if row['tail_metadata_required']:
            diff = 'sha256:' + sha(b'qualification-only-host-tail')
            chain = 'sha256:' + sha((parent + ' ' + diff).encode())
            value = dict(chain_id=chain, parent=parent, uncompressed_bytes_upper_bound=8 * 1024 ** 2)
            chains[chain] = value
            layers.append(value)
            blobs['sha256:' + sha(b'qualification-only-host-blob')] = 8 * 1024 ** 2
        assert len(layers) == row['expected_layer_count']
        for key in ('manifest_digest', 'config_digest'):
            blobs[row[key]] = 65536
        images.append(dict(role=row['role'], expanded_groups=row['expanded_groups'], layers=layers))
    plan = dict(schema='portal-ppt-native-cache-plan.v1', prior_operation=module.OP,
                profile_sha256=module.PROFILE_SHA, images=images, blobs=blobs, chains=chains,
                maximum_outer_blob_bytes=max(blobs.values()), catalog_sha256=module.CATALOG_SHA)
    # Bound extra small imported metadata that may be present in outer headers.
    for number in range(64):
        plan['blobs']['sha256:' + sha(('qualification-extra-' + str(number)).encode())] = 65536
    remote_code = code.replace(module.CATALOG_Z64, '')
    wire = module.wire_plan(plan)
    assert module.unwire_plan(wire) == plan
    packed = module.pack(dict(code=remote_code, plan=wire))
    assert len(packed) + 512 < 110 * 1024, len(packed)
    assert module.unpack(packed)['code'] == remote_code
    raw = code.encode('utf-8')
    (HERE / 'collect_cache.py').write_bytes(raw)
    result = dict(schema='portal-ppt-native-cache-diagnostic-build.v1', script_bytes=len(raw), script_sha256=sha(raw),
                  catalog_sha256=sha(catalog_raw), catalog_roundtrip=True, image_count=6,
                  representative_chain_count=len(chains), transport_probe_base64_bytes=len(packed),
                  additional_small_metadata_probe_count=64,
                  source_or_remote_command_executed=False, image_payload_read_or_hashed=False,
                  ready_for_rollout=False)
    (HERE / 'BUILD.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    build()
