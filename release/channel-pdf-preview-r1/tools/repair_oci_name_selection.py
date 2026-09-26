"""One-time, auditable correction to the already-uploaded R1 build control."""
import hashlib
import json
import os
import sys
from pathlib import Path

OLD_HASH = 'a1741cf6243235b49b9459249ac3dc46d0f602820a2c855f97e9834e9595d04c'
OLD = "matches=[r for r in rows if r.get('annotations',{}).get('org.opencontainers.image.ref.name')==image]\nif len(matches)!=1: raise SystemExit('base_manifest_ambiguous='+image)"
NEW = "matches=[r for r in rows if r.get('annotations',{}).get(\n    'io.containerd.image.name', r.get('annotations',{}).get('org.opencontainers.image.ref.name')\n)==image]\nif len(matches)!=1: raise SystemExit('base_manifest_match_count='+str(len(matches))+' image='+image)"


def repair(root):
    root = Path(root).resolve()
    script = root / 'build-candidate-datax-01.sh'
    manifest = root / 'PACKAGE.SHA256'
    original = script.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    source = original.decode('utf-8')
    rows = []
    for line in manifest.read_text(encoding='ascii').splitlines():
        expected, name = line.split('  ', 1)
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            raise ValueError('Unsafe package member: ' + name)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError('Existing package checksum mismatch: ' + name)
        rows.append((expected, name))
    if sum(name == script.name for _, name in rows) != 1:
        raise ValueError('Build script must occur exactly once in the manifest')
    if before != OLD_HASH:
        receipt = root / 'OCI-FIX1.json'
        if receipt.exists() and source.count(NEW) == 1 and OLD not in source:
            record = json.loads(receipt.read_text(encoding='utf-8'))
            if record.get('before') == OLD_HASH and record.get('after') == before:
                print('channel_pdf_oci_name_fix1=ALREADY_APPLIED')
                return
        raise ValueError('Unknown build-script baseline; refusing to modify')
    if source.count(OLD) != 1:
        raise ValueError('Expected OCI selection block not found exactly once')
    updated = source.replace(OLD, NEW, 1).encode('utf-8')
    after = hashlib.sha256(updated).hexdigest()
    new_manifest = ''.join((after if name == script.name else digest) + '  ' + name + '\n' for digest, name in rows).encode('ascii')
    backups = [(script, script.with_name(script.name + '.before-oci-fix1')),
               (manifest, manifest.with_name(manifest.name + '.before-oci-fix1'))]
    for item, backup in backups:
        if backup.exists():
            raise ValueError('Backup already exists; inspect before retrying: ' + str(backup))
    for item, backup in backups:
        with backup.open('xb') as stream:
            stream.write(item.read_bytes())
    for item, data in ((script, updated), (manifest, new_manifest)):
        pending = item.with_name(item.name + '.oci-fix1.pending')
        with pending.open('xb') as stream:
            stream.write(data)
        os.chmod(pending, item.stat().st_mode)
        os.replace(pending, item)
    (root / 'OCI-FIX1.json').write_bytes(json.dumps({'before': before, 'after': after,
        'scope': 'build_control_only', 'production_workloads_mutated': False}, indent=2).encode('utf-8') + b'\n')
    print('channel_pdf_oci_name_fix1=PASS')
    print('builder_before_sha256=' + before)
    print('builder_after_sha256=' + after)
    print('production_workloads_mutated=no')


if __name__ == '__main__':
    repair(sys.argv[1])
