"""Fixed SkillHub datax-01 cache preload; never starts the application.
Generated from a sealed field-tested importer; original helper pins retained.
"""
#!/usr/bin/env python3
"""Single-attempt hash-bound frontend delta preload on the two admitted ARM64 nodes."""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import sys
import tarfile
import time
import types
import uuid

SOURCES = {'importer': b'#!/usr/bin/env python3\n"""Fixed-node, single-attempt Host thin OCI preload; no workload execution. Python 3.7+."""\nimport argparse\nimport gzip\nimport hashlib\nimport io\nimport json\nimport os\nimport re\nimport shutil\nimport signal\nimport stat\nimport subprocess\nimport sys\nimport tarfile\nimport threading\nimport time\nimport uuid\n\nHOST = \'ecs-zdz-pro-0012\'\nSOCKET = \'/run/containerd/containerd.sock\'\nCONTROL = \'/var/lib/portal-ppt-host-delta-import-r1\'\nINPUT = \'/home/cnooc/host.delta.three-blobs.tar\'\nINPUT_BYTES = 286720\nINPUT_SHA256 = \'0a26f0cb88036981b4f8b19bd1e1c2ff463eba852845aa968e5fb52988b2d3bb\'\nHANDOFF_SHA256 = \'26acc3664652cdad65089e1e40a8a7e8a932f8ac673767665320f5bb7f0c94b2\'\nAPPEND_DIGEST = \'sha256:bf6f9be084986fb9a1f8d6128d02b8e97b4f917d22b84aadb7113171fafb6e74\'\nAPPEND_DIFF_ID = \'sha256:5ed9d4fa761e32cf25bf23cc03317f42ddefb7967f022e55b806437eb3dc55b2\'\nIMAGEFS = \'/mnt/data/containerd/io.containerd.snapshotter.v1.overlayfs\'\nREPO = \'deerflow.local/deer-flow-aio-sandbox\'\nTAG = REPO + \':20261017-portal-ppt-confirm-host-r1-57f68a146414\'\nMANIFEST = \'sha256:540c96e05aa5c0c7709871494dd094047223eaaf6393b2117e9149c0d1467971\'\nCONFIG = \'sha256:aabaa4f970580fb5edac54745bedd558ddedc0768bebaeb663130272e76f043d\'\nOLD_TAG = REPO + \':20260927-ppt-runtime-r2-agent-host-3959a039ee854dca\'\nOLD_MANIFEST = \'sha256:126fabc46a5d4d3b9d50924722bf10e7a1003a4d1adbdf72ace8ae25e188f97e\'\nOLD_CONFIG = \'sha256:09ea44f66e1d47269b10e05f11f933f9f08897a1e5a55222a138f0a1b1acfaea\'\nAIO_TAG = REPO + \':20260921-enterprise-arm64-rc15-document-runtime1\'\nAIO_MANIFEST = \'sha256:b12286cbe09b19577896f8f00177eb3872e8bd100aa0faec6184e82ee66bb4f5\'\nDESCRIPTOR_PREFIX = \'0956e8470be3921140b2263f0e23ce294a41665c8e1cedc69041111fe92c03d4\'\nDIFF_ID_PREFIX = \'cf7215c42b1e8c6dd264c6d922613d7b4b26e64888baa5231d017e248744a482\'\nBASE_CHAIN = \'sha256:389ef3a9ff1e0e33c416188c729cd1f42c4173ce7af27f05f4c495717f9f0089\'\nSMALL, BUNDLE_MAX, LAYER_MAX, EXPANDED_MAX = 1024 * 1024, 12 * 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024\nTOTAL_SECONDS = 600\nGZIP_TYPES = (\'application/vnd.oci.image.layer.v1.tar+gzip\', \'application/vnd.docker.image.rootfs.diff.tar.gzip\')\nTAR_TYPES = (\'application/vnd.oci.image.layer.v1.tar\', \'application/vnd.docker.image.rootfs.diff.tar\')\n\n\nclass Failure(Exception):\n    def __init__(self, code, evidence=None):\n        self.code, self.evidence = code, evidence or {}\n        Exception.__init__(self, code)\n\n\ndef require(ok, code):\n    if not ok: raise Failure(code)\n\n\ndef canonical(value):\n    return json.dumps(value, sort_keys=True, separators=(\',\', \':\'), ensure_ascii=True).encode(\'utf-8\')\n\n\ndef sha(raw):\n    return hashlib.sha256(raw).hexdigest()\n\n\ndef digest(value):\n    require(type(value) is str and re.fullmatch(r\'sha256:[0-9a-f]{64}\', value), \'invalid_digest\')\n    return value\n\n\ndef parse(raw):\n    def pairs(rows):\n        result = {}\n        for k, v in rows:\n            require(k not in result, \'duplicate_json_key\'); result[k] = v\n        return result\n    try:\n        value = json.loads(raw.decode(\'utf-8\'), object_pairs_hook=pairs,\n                           parse_constant=lambda _: (_ for _ in ()).throw(Failure(\'nonfinite_json\')))\n        require(type(value) is dict, \'json_object_required\')\n        return value\n    except Failure: raise\n    except Exception: raise Failure(\'invalid_json\')\n\n\ndef chain_rows(ids):\n    rows, parent = [], \'\'\n    for d in ids:\n        digest(d)\n        key = d if not parent else \'sha256:\' + sha((parent + \' \' + d).encode(\'ascii\'))\n        rows.append({\'chain_id\': key, \'parent\': parent}); parent = key\n    return rows\n\n\ndef validate_metadata(mraw, craw, manifest_digest, config_digest, count):\n    require(0 < len(mraw) <= SMALL and \'sha256:\' + sha(mraw) == manifest_digest, \'manifest_digest_or_size\')\n    m = parse(mraw)\n    require(m.get(\'schemaVersion\') == 2 and type(m.get(\'layers\')) is list and len(m[\'layers\']) == count, \'manifest_layers\')\n    conf = m.get(\'config\', {})\n    require(conf.get(\'digest\') == config_digest and type(conf.get(\'size\')) is int and conf[\'size\'] == len(craw) <= SMALL, \'config_descriptor\')\n    require(\'sha256:\' + sha(craw) == config_digest, \'config_digest\')\n    c = parse(craw)\n    require(c.get(\'os\') == \'linux\' and c.get(\'architecture\') == \'arm64\', \'platform_mismatch\')\n    ids = c.get(\'rootfs\', {}).get(\'diff_ids\')\n    require(c.get(\'rootfs\', {}).get(\'type\') == \'layers\' and type(ids) is list and len(ids) == count, \'diff_id_count\')\n    require(sha(canonical(m[\'layers\'][:128])) == DESCRIPTOR_PREFIX and sha(canonical(ids[:128])) == DIFF_ID_PREFIX, \'base_prefix_mismatch\')\n    for layer in m[\'layers\']:\n        digest(layer.get(\'digest\'))\n        require(type(layer.get(\'size\')) is int and layer[\'size\'] > 0, \'layer_size\')\n        require(layer.get(\'mediaType\') in GZIP_TYPES + TAR_TYPES, \'layer_media_type\')\n    chains = chain_rows(ids)\n    require(chains[127][\'chain_id\'] == BASE_CHAIN, \'base_chain_mismatch\')\n    return m, c, chains\n\n\ndef verify_layer(raw, layer, diff_id):\n    require(0 < len(raw) == layer[\'size\'] <= LAYER_MAX and \'sha256:\' + sha(raw) == layer[\'digest\'], \'append_layer_digest_or_size\')\n    try:\n        stream = gzip.GzipFile(fileobj=io.BytesIO(raw), mode=\'rb\') if layer[\'mediaType\'] in GZIP_TYPES else io.BytesIO(raw)\n        with stream:\n            expanded = stream.read(EXPANDED_MAX + 1)\n            require(len(expanded) <= EXPANDED_MAX and not stream.read(1), \'append_layer_expansion_limit\')\n    except Failure: raise\n    except Exception: raise Failure(\'append_layer_decompression\')\n    require(\'sha256:\' + sha(expanded) == diff_id, \'append_layer_diff_id\')\n    return len(expanded)\n\n\ndef unpack_handoff(raw):\n    require(len(raw) == INPUT_BYTES and sha(raw) == INPUT_SHA256, \'handoff_transport_pin\')\n    allowed = {\'HANDOFF.json\': SMALL, \'candidate.manifest.json\': SMALL, \'candidate.config.json\': SMALL, \'append.layer.blob\': LAYER_MAX}\n    files = {}\n    try:\n        with tarfile.open(fileobj=io.BytesIO(raw), mode=\'r:\') as archive:\n            for entry in archive:\n                require(entry.name in allowed and entry.name not in files and entry.isreg() and not entry.issparse() and not entry.pax_headers,\n                        \'handoff_member_shape\')\n                require(0 < entry.size <= allowed[entry.name], \'handoff_member_limit\')\n                value = archive.extractfile(entry).read(allowed[entry.name] + 1)\n                require(len(value) == entry.size, \'handoff_member_truncated\'); files[entry.name] = value\n    except Failure: raise\n    except Exception: raise Failure(\'handoff_tar_invalid\')\n    require(set(files) == set(allowed), \'handoff_member_set\')\n    require(len(files[\'HANDOFF.json\']) == 1248 and sha(files[\'HANDOFF.json\']) == HANDOFF_SHA256, \'handoff_receipt_pin\')\n    require(len(files[\'candidate.manifest.json\']) == 20436 and len(files[\'candidate.config.json\']) == 123547,\n            \'candidate_metadata_fixed_sizes\')\n    m, c, chains = validate_metadata(files[\'candidate.manifest.json\'], files[\'candidate.config.json\'], MANIFEST, CONFIG, 129)\n    require(m[\'layers\'][-1][\'digest\'] == APPEND_DIGEST and m[\'layers\'][-1][\'size\'] == 130980 and c[\'rootfs\'][\'diff_ids\'][-1] == APPEND_DIFF_ID,\n            \'append_fixed_receipt_identity\')\n    expanded = verify_layer(files[\'append.layer.blob\'], m[\'layers\'][-1], c[\'rootfs\'][\'diff_ids\'][-1])\n    require(expanded == 563200, \'append_fixed_expanded_size\')\n    handoff = parse(files[\'HANDOFF.json\'])\n    require(handoff.get(\'schema\') == \'portal-ppt-host-three-blob-handoff.v1\' and handoff.get(\'manifest_digest\') == MANIFEST and handoff.get(\'config_digest\') == CONFIG,\n            \'handoff_identity\')\n    wanted = {k: {\'bytes\': len(v), \'sha256\': sha(v)} for k, v in files.items() if k != \'HANDOFF.json\'}\n    require(handoff.get(\'files\') == wanted, \'handoff_file_receipts\')\n    require(handoff.get(\'append_layer\') == {\'digest\': m[\'layers\'][-1][\'digest\'], \'size\': m[\'layers\'][-1][\'size\'],\n            \'media_type\': m[\'layers\'][-1][\'mediaType\'], \'config_size\': len(files[\'candidate.config.json\']),\n            \'diff_id\': c[\'rootfs\'][\'diff_ids\'][-1], \'uncompressed_bytes\': expanded}, \'handoff_layer_receipt\')\n    return {\'files\': files, \'manifest\': m, \'config\': c, \'chains\': chains, \'bundle_sha256\': sha(raw),\n            \'append_layer\': handoff[\'append_layer\']}\n\n\ndef temporary_tag(operation):\n    require(type(operation) is str and re.fullmatch(r\'[0-9a-f]{32}\', operation), \'operation_invalid\')\n    return REPO + \':portal-ppt-delta-private-\' + operation\n\n\ndef make_oci(prepared, operation):\n    name = temporary_tag(operation)\n    items = {\'oci-layout\': canonical({\'imageLayoutVersion\': \'1.0.0\'}),\n             \'index.json\': canonical({\'schemaVersion\': 2, \'manifests\': [{\'mediaType\': \'application/vnd.oci.image.manifest.v1+json\',\n                 \'digest\': MANIFEST, \'size\': len(prepared[\'files\'][\'candidate.manifest.json\']),\n                 \'platform\': {\'os\': \'linux\', \'architecture\': \'arm64\'}, \'annotations\': {\'io.containerd.image.name\': name}}]})}\n    for label, dg in [(\'candidate.manifest.json\', MANIFEST), (\'candidate.config.json\', CONFIG), (\'append.layer.blob\', prepared[\'append_layer\'][\'digest\'])]:\n        items[\'blobs/sha256/\' + dg.split(\':\')[1]] = prepared[\'files\'][label]\n    out = io.BytesIO()\n    with tarfile.open(fileobj=out, mode=\'w\', format=tarfile.USTAR_FORMAT) as archive:\n        for path in sorted(items):\n            blob = items[path]; header = tarfile.TarInfo(path)\n            header.size, header.mode, header.uid, header.gid, header.mtime = len(blob), 0o600, 0, 0, 0\n            archive.addfile(header, io.BytesIO(blob))\n    value = out.getvalue(); require(len(value) <= BUNDLE_MAX, \'oci_size_limit\')\n    return value\n\n\ndef image_target(raw, name):\n    try: rows = [r.split() for r in raw.decode(\'utf-8\').splitlines() if r.strip()]\n    except Exception: raise Failure(\'image_table_encoding\')\n    require(rows and rows[0][:3] == [\'REF\', \'TYPE\', \'DIGEST\'], \'image_table_header\')\n    require(len(rows) <= 2 and all(len(r) >= 3 and r[0] == name for r in rows[1:]), \'image_filter_response\')\n    return digest(rows[1][2]) if len(rows) == 2 else None\n\n\ndef content_names(raw, expected):\n    try: rows = raw.decode(\'ascii\').splitlines()\n    except Exception: raise Failure(\'content_table_encoding\')\n    require(len(rows) == len(set(rows)) and set(rows) == set(expected), \'content_closure_missing_or_unexpected\')\n\n\ndef content_labels(raw, dg):\n    try: rows = [r.split() for r in raw.decode(\'utf-8\').splitlines() if r.strip()]\n    except Exception: raise Failure(\'content_labels_encoding\')\n    require(len(rows) == 2 and rows[0] == [\'DIGEST\', \'SIZE\', \'AGE\', \'LABELS\'] and rows[1][0] == dg and len(rows[1]) >= 4, \'content_label_response\')\n    tokens = rows[1][-1].split(\',\'); values = {}\n    for token in tokens:\n        if token == \'-\': continue\n        require(\'=\' in token, \'content_label_shape\')\n        k, v = token.split(\'=\', 1); require(k not in values, \'content_label_duplicate\'); values[k] = v\n    return values\n\n\ndef verify_manifest_gc(labels, manifest):\n    expected = {\'containerd.io/gc.ref.content.config\': manifest[\'config\'][\'digest\']}\n    expected.update({\'containerd.io/gc.ref.content.l.\' + str(i): d[\'digest\'] for i, d in enumerate(manifest[\'layers\'])})\n    require(all(labels.get(k) == v for k, v in expected.items()), \'manifest_gc_closure_missing\')\n\n\ndef verify_snapshots(raw, chains):\n    try: rows = raw.decode(\'utf-8\').splitlines()\n    except Exception: raise Failure(\'snapshot_encoding\')\n    require(rows and rows[0].split() == [\'KEY\', \'PARENT\', \'KIND\'] and len(rows) <= 20001, \'snapshot_table_shape\')\n    wanted = {r[\'chain_id\']: r[\'parent\'] for r in chains}; found = {}\n    for line in rows[1:]:\n        words = line.split()\n        if not words or words[0] not in wanted: continue\n        require(len(words) in (2, 3) and words[0] not in found, \'snapshot_duplicate_or_shape\')\n        parent, kind = (\'\', words[1]) if len(words) == 2 else (words[1], words[2])\n        require(parent == wanted[words[0]] and kind == \'Committed\', \'snapshot_chain_not_committed\')\n        found[words[0]] = parent\n    require(set(found) == set(wanted), \'snapshot_chain_missing\')\n\n\ndef run_bounded(argv, timeout, limit, pass_fds=()):\n    started = time.monotonic(); proc = None; threads = []; outputs = {\'stdout\': bytearray(), \'stderr\': bytearray()}\n    counts = {\'stdout\': 0, \'stderr\': 0}; exceeded = threading.Event(); failed = threading.Event()\n    reason = None; rc = None\n    def drain(pipe, name):\n        try:\n            while True:\n                chunk = pipe.read(65536)\n                if not chunk: break\n                counts[name] += len(chunk)\n                outputs[name].extend(chunk[:max(0, limit - len(outputs[name]))])\n                if counts[name] > limit: exceeded.set()\n        except Exception: failed.set()\n        finally: pipe.close()\n    try:\n        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,\n            start_new_session=True, pass_fds=pass_fds,\n            env={\'PATH\': \'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\', \'HOME\': \'/root\', \'LANG\': \'C.UTF-8\'})\n        for name, stream in [(\'stdout\', proc.stdout), (\'stderr\', proc.stderr)]:\n            t = threading.Thread(target=drain, args=(stream, name), daemon=True); threads.append(t); t.start()\n        while proc.poll() is None:\n            if exceeded.is_set(): reason = \'command_output_limit\'; break\n            if time.monotonic() - started >= timeout: reason = \'command_timeout\'; break\n            time.sleep(0.02)\n        if reason is None:\n            rc = proc.returncode\n            for t in threads: t.join(timeout=0.5)\n            if any(t.is_alive() for t in threads): reason = \'command_pipe_open\'\n            elif exceeded.is_set(): reason = \'command_output_limit\'\n            elif failed.is_set(): reason = \'command_pipe_failed\'\n            elif rc != 0: reason = \'command_nonzero\'\n    except BaseException as exc:\n        reason = \'interrupted\' if isinstance(exc, KeyboardInterrupt) else \'command_start_or_io_failed\'\n    finally:\n        if proc is not None:\n            try: os.killpg(proc.pid, signal.SIGKILL)\n            except ProcessLookupError: pass\n            except OSError: reason = reason or \'command_cleanup_failed\'\n            try: rc = proc.wait(timeout=2)\n            except subprocess.TimeoutExpired: reason = reason or \'command_cleanup_timeout\'\n        for t in threads: t.join(timeout=0.5)\n    receipt = {\'returncode\': rc, \'error_code\': reason, \'elapsed_seconds\': round(time.monotonic() - started, 3),\n               \'stdout_bytes\': counts[\'stdout\'], \'stderr_bytes\': counts[\'stderr\'],\n               \'stdout_sha256\': sha(bytes(outputs[\'stdout\'])), \'stderr_sha256\': sha(bytes(outputs[\'stderr\'])),\n               \'output_limit_exceeded\': exceeded.is_set(), \'automatic_retry\': False}\n    return bytes(outputs[\'stdout\']), bytes(outputs[\'stderr\']), receipt\n\n\ndef write_new(directory, name, raw):\n    require(re.fullmatch(r\'[a-zA-Z0-9_.-]+\', name) and len(raw) <= BUNDLE_MAX, \'output_name_or_size\')\n    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)\n    try:\n        data = memoryview(raw)\n        while data:\n            n = os.write(f, data); require(n > 0, \'short_write\'); data = data[n:]\n        os.fsync(f)\n    finally: os.close(f)\n    os.fsync(directory)\n\n\nclass Importer:\n    def __init__(self, ctr, cri, operation, output_fd, archive_fd, prepared, runner=None, clock=None, disk=None, progress=None):\n        self.ctr = [ctr, \'--address\', SOCKET, \'--namespace\', \'k8s.io\']\n        self.cri, self.operation, self.directory, self.archive_fd, self.p = cri, operation, output_fd, archive_fd, prepared\n        self.runner, self.clock, self.disk = runner or run_bounded, clock or time.monotonic, disk or self.local_disk\n        self.progress = progress or (lambda phase: None)\n        self.started = self.clock(); self.temp = temporary_tag(operation)\n        self.report = {\'schema\': \'portal-ppt-host-delta-import.v1\', \'status\': \'INCOMPLETE\', \'operation\': operation,\n            \'hostname\': HOST, \'temporary_tag\': self.temp, \'tag\': TAG, \'manifest_digest\': MANIFEST, \'config_digest\': CONFIG,\n            \'bundle_sha256\': prepared[\'bundle_sha256\'], \'append_layer\': prepared[\'append_layer\'], \'commands\': [],\n            \'storage_checks\': [], \'phase_a\': \'NOT_REQUESTED\', \'phase_b\': \'NOT_REQUESTED\', \'canonical_names_created\': [],\n            \'image_import_requested\': False, \'image_import_may_be_partial\': False, \'automatic_retry\': False,\n            \'production_workload_mutation\': False, \'database_access\': False, \'ssh\': False, \'image_execution\': False,\n            \'image_deletion\': False, \'old_layer_bytes_read\': False, \'old_layer_hashes_verified_now\': False,\n            \'old_layer_sizes_verified_now\': False, \'kubernetes_node_uid_freshly_checked\': False,\n            \'pair_runtime_passed\': False, \'ready_for_rollout\': False}\n        self.report[\'ordinary_aio_digest_alias_readiness\'] = \'NOT_COLLECTED_TAG_PRESERVATION_ONLY\'\n\n    def call(self, phase, args, mutates=False, timeout=15, limit=SMALL, is_cri=False, pass_fds=()):\n        left = TOTAL_SECONDS - (self.clock() - self.started); require(left > 0, \'total_deadline\')\n        argv = ([self.cri, \'--runtime-endpoint\', \'unix://\' + SOCKET, \'--image-endpoint\', \'unix://\' + SOCKET] if is_cri else self.ctr) + args\n        row = {\'phase\': phase, \'argv_sha256\': sha(canonical(argv)), \'containerd_mutation_requested\': mutates}\n        self.report[\'commands\'].append(row)\n        out, err, result = self.runner(argv, timeout=min(timeout, left), limit=limit, pass_fds=pass_fds)\n        row.update(result)\n        # Raw config and runtime errors remain in the root-private control directory only.\n        number = len(self.report[\'commands\'])\n        for name, raw in [(\'stdout\', out), (\'stderr\', err)]:\n            write_new(self.directory, \'command-%03d.%s.private.log\' % (number, name), raw)\n        write_new(self.directory, \'command-%03d.json\' % number, canonical(row) + b\'\\n\')\n        require(result.get(\'error_code\') is None and result.get(\'returncode\') == 0, result.get(\'error_code\') or \'command_failed\')\n        return out\n\n    def target(self, name):\n        require(name in (TAG, REPO + \'@\' + MANIFEST, OLD_TAG, AIO_TAG, self.temp), \'image_scope\')\n        return image_target(self.call(\'image_reference\', [\'images\', \'ls\', \'name==\' + name]), name)\n\n    def preserved(self):\n        require(self.target(OLD_TAG) == OLD_MANIFEST and self.target(AIO_TAG) == AIO_MANIFEST, \'preserved_image_changed\')\n\n    def labels(self, dg):\n        return content_labels(self.call(\'content_gc_labels\', [\'content\', \'ls\', \'digest==\' + dg]), dg)\n\n    def closure(self, m, md):\n        expected = {md, m[\'config\'][\'digest\']} | {d[\'digest\'] for d in m[\'layers\']}\n        content_names(self.call(\'content_closure\', [\'content\', \'ls\', \'--quiet\'] + [\'digest==\' + d for d in sorted(expected)]), expected)\n        verify_manifest_gc(self.labels(md), m)\n\n    def snapshots(self, chains):\n        raw = self.call(\'snapshot_closure\', [\'snapshots\', \'--snapshotter\', \'overlayfs\', \'ls\'], limit=4 * SMALL)\n        verify_snapshots(raw, chains)\n\n    def ready(self, name):\n        raw = self.call(\'image_content_and_unpack\', [\'images\', \'check\', \'--quiet\', \'--snapshotter\', \'overlayfs\', \'name==\' + name])\n        require(raw.decode(\'utf-8\').splitlines() == [name], \'image_check_not_ready\')\n\n    def local_disk(self):\n        rows = []\n        for path in (\'/var/lib\', IMAGEFS):\n            require(os.path.realpath(path) == path and stat.S_ISDIR(os.lstat(path).st_mode), \'storage_path_changed\')\n            v = os.statvfs(path); s = os.stat(path)\n            # Small delta only: at most 12 MiB transport, 32 MiB unpack, logs; reserve is additional.\n            needed = 256 * SMALL + 1024 * SMALL\n            require(v.f_bavail * v.f_frsize >= needed and v.f_favail >= 4096, \'small_delta_storage_budget\')\n            rows.append({\'path\': path, \'device\': s.st_dev, \'available_bytes\': v.f_bavail * v.f_frsize,\n                         \'available_inodes\': v.f_favail, \'required_bytes\': needed})\n        return rows\n\n    def checkpoint(self, name):\n        write_new(self.directory, name + \'.json\', canonical(self.report) + b\'\\n\')\n\n    def import_phase(self, name, no_unpack):\n        self.progress(\'PHASE_A_IMPORT\' if no_unpack else \'PHASE_B_UNPACK\')\n        self.report[\'storage_checks\'].append({\'phase\': name, \'filesystems\': self.disk()})\n        self.preserved()\n        args = [\'images\', \'import\', \'--local\', \'--platform\', \'linux/arm64\', \'--snapshotter\', \'overlayfs\',\n                \'--label\', \'io.cri-containerd.image=managed\']\n        if no_unpack: args.append(\'--no-unpack\')\n        args.append(\'/proc/self/fd/\' + str(self.archive_fd))\n        self.report[name] = \'REQUESTED\'; self.report[\'image_import_requested\'] = True; self.report[\'image_import_may_be_partial\'] = True\n        self.checkpoint(name + \'-REQUESTED\')\n        os.lseek(self.archive_fd, 0, os.SEEK_SET)\n        self.call(name, args, mutates=True, timeout=180, pass_fds=(self.archive_fd,))\n        require(self.target(self.temp) == MANIFEST, \'temporary_target_changed\')\n        self.report[name] = \'PASS\'; self.checkpoint(name + \'-PASS\')\n\n    def publish(self, name):\n        current = self.target(name)\n        require(current in (None, MANIFEST), \'canonical_target_conflict\')\n        if current is None:\n            # No force and explicit local: ImageService.Create is atomic and cannot overwrite a concurrent name.\n            require(self.target(self.temp) == MANIFEST, \'temporary_target_changed\')\n            self.call(\'publish_canonical_reference\', [\'images\', \'tag\', \'--local\', self.temp, name], mutates=True)\n            self.report[\'canonical_names_created\'].append(name)\n        require(self.target(name) == MANIFEST, \'canonical_target_changed\')\n\n    def execute(self):\n        try:\n            self.progress(\'BASE_PREFLIGHT\')\n            self.report[\'storage_checks\'].append({\'phase\': \'BEFORE_IMPORT\', \'filesystems\': self.disk()})\n            version = self.call(\'containerd_server_version\', [\'version\']).decode(\'utf-8\')\n            require(re.search(r\'(?ms)^Server:\\s*\\n\\s*Version:\\s*v?2\\.3\\.3\\s*$\', version), \'containerd_server_version_mismatch\')\n            self.preserved()\n            for name in (TAG, REPO + \'@\' + MANIFEST):\n                require(self.target(name) in (None, MANIFEST), \'canonical_target_conflict\')\n            require(self.target(self.temp) is None, \'temporary_name_exists\')\n            mr = self.call(\'old_manifest\', [\'content\', \'get\', OLD_MANIFEST])\n            cr = self.call(\'old_config\', [\'content\', \'get\', OLD_CONFIG])\n            old, old_config, chains = validate_metadata(mr, cr, OLD_MANIFEST, OLD_CONFIG, 128)\n            require(old[\'layers\'] == self.p[\'manifest\'][\'layers\'][:128] and old_config[\'rootfs\'][\'diff_ids\'] == self.p[\'config\'][\'rootfs\'][\'diff_ids\'][:128], \'exact_old_new_prefix\')\n            require(old_config.get(\'config\') == self.p[\'config\'].get(\'config\'), \'runtime_config_changed\')\n            self.closure(old, OLD_MANIFEST); self.snapshots(chains); self.ready(OLD_TAG)\n            self.report[\'base_preflight\'] = \'EXACT_METADATA_CONTENT_NAMES_AND_COMMITTED_CHAIN_PASS\'\n            self.import_phase(\'phase_a\', True)\n            self.closure(self.p[\'manifest\'], MANIFEST)\n            # Read back the two small new metadata blobs; no existing layer bytes are read.\n            validate_metadata(self.call(\'new_manifest_readback\', [\'content\', \'get\', MANIFEST]),\n                              self.call(\'new_config_readback\', [\'content\', \'get\', CONFIG]), MANIFEST, CONFIG, 129)\n            self.import_phase(\'phase_b\', False)\n            self.closure(self.p[\'manifest\'], MANIFEST); self.snapshots(self.p[\'chains\']); self.ready(self.temp)\n            require(self.labels(CONFIG).get(\'containerd.io/gc.ref.snapshot.overlayfs\') == self.p[\'chains\'][-1][\'chain_id\'], \'config_snapshot_gc_missing\')\n            self.progress(\'PUBLISH_VERIFY\')\n            for name in (TAG, REPO + \'@\' + MANIFEST): self.publish(name)\n            for name in (TAG, REPO + \'@\' + MANIFEST):\n                self.ready(name)\n                obj = parse(self.call(\'cri_visibility\', [\'inspecti\', \'--output=json\', name], is_cri=True, limit=4 * SMALL))\n                status = obj.get(\'status\', {})\n                require(status.get(\'id\') == CONFIG and REPO + \'@\' + MANIFEST in status.get(\'repoDigests\', []) and TAG in status.get(\'repoTags\', []), \'cri_identity_or_references\')\n            self.preserved(); self.snapshots(self.p[\'chains\'])\n            for name in (TAG, REPO + \'@\' + MANIFEST): require(self.target(name) == MANIFEST, \'final_reference_changed\')\n            self.report.update(status=\'COMPLETE_HOST_DELTA_PRELOAD_REVIEW_REQUIRED\', image_import_may_be_partial=False,\n                content_and_unpack=\'PASS\', cri_visibility=\'PASS\', old_references_preserved=True,\n                temporary_reference_retained=True, filesystem_capacity_reserved=False)\n        except BaseException as exc:\n            self.report[\'error_code\'] = exc.code if isinstance(exc, Failure) else (\'interrupted\' if isinstance(exc, KeyboardInterrupt) else \'local_controller_failure\')\n        self.report[\'elapsed_seconds\'] = round(self.clock() - self.started, 3)\n        return self.report\n\n\ndef private_parent():\n    fd = os.open(\'/\', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)\n    try:\n        for part in (\'var\', \'lib\'):\n            s = os.fstat(fd); require(s.st_uid == 0 and not s.st_mode & 0o022, \'unsafe_control_parent\')\n            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd); os.close(fd); fd = nxt\n        s = os.fstat(fd); require(s.st_uid == 0 and not s.st_mode & 0o022, \'unsafe_control_parent\')\n        return fd\n    except BaseException: os.close(fd); raise\n\n\ndef persist_report(directory, report):\n    try:\n        raw = canonical(report) + b\'\\n\'\n        write_new(directory, \'REPORT.json\', raw)\n        return {\'report_path\': CONTROL + \'/REPORT.json\', \'report_bytes\': len(raw), \'report_sha256\': sha(raw)}\n    except Exception:\n        report[\'report_write_failed\'] = True\n        report[\'status\'] = \'INCOMPLETE\'\n        report[\'error_code\'] = \'final_report_persistence_failed\'\n\n\ndef main():\n    parser = argparse.ArgumentParser(description=__doc__)\n    parser.add_argument(\'--import-host-delta-once\', action=\'store_true\', required=True); parser.parse_args()\n    report = {\'schema\': \'portal-ppt-host-delta-import.v1\', \'status\': \'INCOMPLETE\', \'automatic_retry\': False,\n              \'image_import_requested\': False, \'ready_for_rollout\': False, \'control_directory\': CONTROL}\n    parent = directory = archive = source = None\n    try:\n        require(os.name == \'posix\' and os.geteuid() == 0 and os.uname().nodename.split(\'.\')[0] == HOST and os.uname().machine == \'aarch64\', \'fixed_arm64_root_host_required\')\n        ctr = shutil.which(\'ctr\', path=\'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\')\n        cri = shutil.which(\'crictl\', path=\'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\')\n        require(ctr is not None and cri is not None, \'required_tool_missing\')\n        parent = private_parent(); name = CONTROL.rsplit(\'/\', 1)[1]\n        os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)\n        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)\n        operation = uuid.uuid4().hex\n        write_new(directory, \'STARTED.json\', canonical({\'operation\': operation, \'host\': HOST, \'automatic_retry\': False}) + b\'\\n\')\n        source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n        st = os.fstat(source); require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and 0 < st.st_size <= BUNDLE_MAX, \'input_type_or_size\')\n        chunks, count = [], 0\n        while True:\n            part = os.read(source, 65536)\n            if not part: break\n            count += len(part); require(count <= BUNDLE_MAX, \'input_size_limit\'); chunks.append(part)\n        require(os.fstat(source).st_size == st.st_size == count, \'input_size_changed\')\n        prepared = unpack_handoff(b\'\'.join(chunks))\n        raw = make_oci(prepared, operation); write_new(directory, \'candidate.delta.oci.tar\', raw)\n        archive = os.open(\'candidate.delta.oci.tar\', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)\n        actual = os.fstat(archive)\n        require(stat.S_ISREG(actual.st_mode) and actual.st_uid == 0 and actual.st_size == len(raw), \'private_archive_binding\')\n        report = Importer(ctr, cri, operation, directory, archive, prepared,\n                          progress=lambda phase: print(json.dumps({\'phase\': phase}), file=sys.stderr, flush=True)).execute()\n        report.update(control_directory=CONTROL, oci_transport={\'bytes\': len(raw), \'sha256\': sha(raw), \'complete_oci_closure_in_archive\': False})\n    except BaseException as exc:\n        report[\'error_code\'] = exc.code if isinstance(exc, Failure) else (\'once_marker_exists_review_required\' if isinstance(exc, FileExistsError) else \'local_controller_failure\')\n    finally:\n        if directory is not None:\n            saved = persist_report(directory, report)\n            if saved is not None: report.update(saved)\n        for fd in (source, archive, directory, parent):\n            if fd is not None: os.close(fd)\n    print(json.dumps(report, sort_keys=True, indent=2), flush=True)\n    return 0 if report[\'status\'] == \'COMPLETE_HOST_DELTA_PRELOAD_REVIEW_REQUIRED\' else 1\n\n\nif __name__ == \'__main__\': sys.exit(main())\n', 'resource': b'import os,re,stat,time\nHOST=\'datax-01\'\nNODE_UID=\'ec5698b0-8d82-4eed-bae0-3711c7b7f36c\'\nCONTROL=\'/var/lib/portal-ppt-runner-host-capability-node-r1\'\nMIB=1024**2\nGIB=1024**3\nTOTAL_SECONDS=900\nWRITE_LIMIT=32*MIB\nREPORT_LIMIT=2*MIB\nNORMAL_WRITE_LIMIT=WRITE_LIMIT-2*REPORT_LIMIT\nCONTROL_COST=64*MIB\nEXTRA_HEADROOM=2*GIB\nclass Refused(Exception):pass\ndef require(ok,code):\n    if not ok:raise Refused(code)\nNODE_PATH = \'{.metadata.uid}{"\\\\n"}{.metadata.name}{"\\\\n"}{.status.nodeInfo.architecture}{"\\\\n"}{.status.nodeInfo.operatingSystem}{"\\\\n"}{range .status.conditions[*]}{.type}{"="}{.status}{"\\\\n"}{end}\'\nEXPECTED_HARD = {\'imagefs.available\':\'15%\', \'imagefs.inodesFree\':\'5%\', \'nodefs.available\':\'10%\',\n    \'nodefs.inodesFree\':\'5%\', \'memory.available\':\'100Mi\'}\n\ndef thresholds(value):\n    require(type(value) is dict and type(value.get(\'kubeletconfig\')) is dict, \'kubelet_configz_shape\')\n    config=value[\'kubeletconfig\']\n    require(type(config.get(\'imageGCHighThresholdPercent\')) is int and config[\'imageGCHighThresholdPercent\']==85\n        and type(config.get(\'imageGCLowThresholdPercent\')) is int and config[\'imageGCLowThresholdPercent\']==80\n        and config.get(\'evictionHard\')==EXPECTED_HARD and config.get(\'mergeDefaultEvictionSettings\',False) is False\n        and config.get(\'evictionSoft\',{}) in (None,{}) and config.get(\'evictionMinimumReclaim\',{}) in (None,{}), \'fixed_effective_kubelet_thresholds_changed\')\n    return {\'imageGCHighThresholdPercent\':85,\'imageGCLowThresholdPercent\':80,\'evictionHard\':dict(EXPECTED_HARD),\n        \'evictionSoft\':{},\'evictionMinimumReclaim\':{},\'mergeDefaultEvictionSettings\':False}\n\n\ndef node_identity(raw):\n    lines=raw.decode(\'ascii\').splitlines()\n    require(len(lines)>=6 and lines[:4]==[NODE_UID,HOST,\'arm64\',\'linux\'], \'fresh_datax01_node_identity\')\n    conditions={}\n    for line in lines[4:]:\n        parts=line.split(\'=\')\n        require(len(parts)==2 and re.fullmatch(\'[A-Za-z]{1,64}\',parts[0]) and parts[1] in (\'True\',\'False\',\'Unknown\')\n            and parts[0] not in conditions, \'fresh_node_conditions_shape\')\n        conditions[parts[0]]=parts[1]\n    require(conditions.get(\'Ready\')==\'True\' and conditions.get(\'DiskPressure\')==\'False\', \'fresh_node_not_ready_or_disk_pressure\')\n    return {\'name\':HOST,\'uid\':NODE_UID,\'architecture\':\'arm64\',\'operating_system\':\'linux\',\'Ready\':True,\'DiskPressure\':False}\n\n\ndef observed_directory(path):\n    require(os.path.realpath(path)==path and path.startswith(\'/\') and \'//\' not in path and \'..\' not in path.split(\'/\'), \'storage_path_symlink_or_shape\')\n    fd=os.open(\'/\',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n    try:\n        for part in path.strip(\'/\').split(\'/\'):\n            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt\n        info, space=os.fstat(fd),os.fstatvfs(fd)\n        require(stat.S_ISDIR(info.st_mode), \'storage_path_not_directory\')\n        return {\'path\':path,\'device\':info.st_dev,\'available_bytes\':space.f_bavail*space.f_frsize,\n            \'capacity_bytes\':space.f_blocks*space.f_frsize,\'available_inodes\':space.f_favail,\n            \'total_inodes\':space.f_files,\'fragment_size\':space.f_frsize}\n    finally:os.close(fd)\n\n\ndef resource_gate(scope, prepared, roles, samples, page_size):\n    # Frozen ALL incremental costs, including its original 1GiB reserve, remain intact.\n    old=scope.incremental_budget(prepared,roles,samples,page_size)\n    groups={}\n    for sample, inherited in zip(samples,old):\n        require(type(sample.get(\'capacity_bytes\')) is int and sample[\'capacity_bytes\']>0\n            and type(sample.get(\'total_inodes\')) is int and sample[\'total_inodes\']>0, \'fresh_capacity_or_inode_total\')\n        group=groups.setdefault(sample[\'device\'],{\'device\':sample[\'device\'],\'paths\':[],\'available_bytes\':sample[\'available_bytes\'],\n            \'capacity_bytes\':sample[\'capacity_bytes\'],\'available_inodes\':sample[\'available_inodes\'],\'total_inodes\':sample[\'total_inodes\'],\n            \'inherited_required_bytes\':0,\'inherited_required_inodes\':0,\'control_write_allowance_bytes\':0,\'original_cost_rows\':[]})\n        require(group[\'capacity_bytes\']==sample[\'capacity_bytes\'] and group[\'total_inodes\']==sample[\'total_inodes\'], \'same_device_capacity_changed\')\n        group[\'paths\'].append(sample[\'path\']);group[\'original_cost_rows\'].append(inherited)\n        group[\'available_bytes\']=min(group[\'available_bytes\'],sample[\'available_bytes\'])\n        group[\'available_inodes\']=min(group[\'available_inodes\'],sample[\'available_inodes\'])\n        # Each old row budgets the complete shared cost, not a separate content/snapshot component.\n        group[\'inherited_required_bytes\']=max(group[\'inherited_required_bytes\'],inherited[\'required_bytes\'])\n        group[\'inherited_required_inodes\']=max(group[\'inherited_required_inodes\'],max(4096,inherited[\'appended_header_count\']*8))\n        if sample[\'path\']==CONTROL:group[\'control_write_allowance_bytes\']=CONTROL_COST\n    result={\'policy\':\'EXACT_CACHED_BASE_THIN_COST_PLUS_KUBELET_GC_BOUNDARY_AND_EXPLICIT_HEADROOM\',\n        \'old_batch_twenty_percent_gate_replaced_explicitly\':True,\'kubelet_configuration_changed\':False,\n        \'original_incremental_cost_and_1GiB_reserve_retained\':True,\'filesystem_capacity_reserved\':False,\'quota_capacity_verified\':False,\n        \'devices\':list(groups.values()),\'pass\':False}\n    for group in result[\'devices\']:\n        cap=group[\'capacity_bytes\'];group[\'sixteen_percent_ceil_bytes\']=(cap*16+99)//100\n        group[\'extra_headroom_bytes\']=EXTRA_HEADROOM\n        group[\'required_bytes\']=group[\'inherited_required_bytes\']+group[\'sixteen_percent_ceil_bytes\']+EXTRA_HEADROOM+group[\'control_write_allowance_bytes\']\n        group[\'predicted_available_after_all_inherited_costs\']=group[\'available_bytes\']-group[\'inherited_required_bytes\']-group[\'control_write_allowance_bytes\']\n        group[\'predicted_gc_usage_percent\']=100-(group[\'predicted_available_after_all_inherited_costs\']*100//cap)\n        group[\'predicted_available_inodes_after_cost\']=group[\'available_inodes\']-group[\'inherited_required_inodes\']\n        group[\'pass\']=group[\'available_bytes\']>=group[\'required_bytes\'] and group[\'predicted_gc_usage_percent\']<85 \\\n            and group[\'predicted_available_inodes_after_cost\']*100>group[\'total_inodes\']*5\n    result[\'pass\']=all(group[\'pass\'] for group in result[\'devices\'])\n    return result\n\n\nclass BudgetIO:\n    def __init__(self, original, started, failure=Refused):\n        self.original=original;self.started=started;self.saved=0;self.calls=0;self.failure=failure\n    def __getattr__(self,name):return getattr(self.original,name)\n    def check(self,ok,code):\n        if not ok:raise self.failure(code)\n    def run_bounded(self,argv,timeout,limit,pass_fds=()):\n        left=TOTAL_SECONDS-(time.monotonic()-self.started)\n        self.check(left>0 and self.calls<400, \'global_command_time_or_count_limit\')\n        room=NORMAL_WRITE_LIMIT-self.saved-16384;self.check(room>=2048, \'aggregate_private_output_limit\')\n        self.calls+=1\n        return self.original.run_bounded(argv,timeout=min(timeout,left),limit=min(limit,room//2),pass_fds=pass_fds)\n    def write_new(self,directory,name,raw):\n        self.check(type(raw) is bytes and self.saved+len(raw)<=NORMAL_WRITE_LIMIT, \'aggregate_private_write_limit\')\n        self.saved+=len(raw);return self.original.write_new(directory,name,raw)\n\n', 'imagefs': b"def require(ok, code):\n    if not ok: raise ValueError(code)\n\ndef imagefs_paths(value):\n    if type(value.get('status')) is dict:\n        require('imageFilesystems' not in value and 'containerFilesystems' not in value,'ambiguous_imagefs_response');value=value['status']\n    images=value.get('imageFilesystems');containers=value.get('containerFilesystems',[])\n    require(type(images) is list and len(images)==1 and type(containers) is list and len(containers)<=1,'imagefs_response_shape')\n    paths={r.get('fsId',{}).get('mountpoint') for r in images+containers}\n    require(len(paths)==1,'separate_container_imagefs_unresolved')\n    snapshot=next(iter(paths))\n    require(type(snapshot) is str and snapshot.startswith('/') and len(snapshot)<=4096\n            and snapshot.endswith('/io.containerd.snapshotter.v1.overlayfs')\n            and '..' not in snapshot.split('/') and '//' not in snapshot,'imagefs_layout_unresolved')\n    root=snapshot.rsplit('/',1)[0]\n    return {'snapshot':snapshot,'content':root+'/io.containerd.content.v1.content'}\n"}
SOURCE_PINS = {'importer': {'bytes': 29458, 'sha256': '4b07f6546729c29ed0eebf2db62f531c20b2747b4d6eb80ee0f9ff0c1c7e807f'}, 'resource': {'bytes': 7326, 'sha256': 'afe7db4b8e95824d7ad40685124c79bb20070961e2ee6fe2705cd9051f72beba'}, 'imagefs': {'bytes': 1046, 'sha256': 'd3a6396108ce311feba6105ecd4c10f22d2096c6e2c33f3763735afe999b296e'}}
TRANSPORT_PIN = {'bytes': 848886784, 'sha256': 'd2e8fbebeddc1a4667dfae26eefb5a9f83e79ce4fcb1724361e09d1d4c561728'}
CONTROL = '/var/lib/portal-skillhub-registry-auth-preload-20261106-r1'
WORKSPACE_PARENT = '/var/lib'
EXPECTED_IMAGEFS = {'content':'/var/lib/containerd/io.containerd.content.v1.content','snapshot':'/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs'}
INPUT = '/home/cnooc/skillhub.registry-auth-r1.full.oci.tar'
HOST = 'UNSELECTED'
NODES = {'datax-01': 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'}
DESCRIPTORS = [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:0b613318ea879878918380aa3aeb220dfe824e311b83bc955cb8a1d4319650ab', 'size': 28887235}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:eddc3451cad891eb57d3979f3ea6ea8eee45eba4fdbd8401534b39284d613623', 'size': 16951779}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b3ac9f9c806f07843af17f4e67a86faf3ca768e97ec15d45e97fae38dcfdc4cc', 'size': 52273651}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:fda3adcb84456bb22c514a2e660753d08ef8187b34279c62a1c2a6feb072b1a2', 'size': 160}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b79577144548e0cc6f704acda68f028bf5d48b0f462dbdd8a6d6ccb0114a8c96', 'size': 2464}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b9872243667cd19109bada8f630bf0d10820b10a6f07e73cf3b605ef47cf06b5', 'size': 3428}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:6e6333b07ca5fb40744d5b37513940b2e07c5594a76c53f89232d4daa6639b49', 'size': 93}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:3484b0103557e94321199edb0a247f46d76cd0e6b90f0c2fdf435a1f805587d8', 'size': 93804189}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:cdb1b42647d37d2aa7bedc697028ecb04bc8fac5df40905402763f33900179b1', 'size': 93804342}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:e52617dff1b693c0735cd6e8aac9ed37f510275e6fd2b2961fb9f9d8302d7f77', 'size': 93807726}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:668097ee06129a5e8e5e0646c9766a5655d7c18077299e3503e3afbd2651397b', 'size': 93808053}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:aeeef48d72bc0f74eae9cc11e8e4ea33d191e1cc70ec12a2a7669408b852cf7b', 'size': 93947656}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:8903709147330b9b6bde3dd8a99ab1d2e184d37213b23f2ce607f97f9df79376', 'size': 93949507}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:0cbff6932119b26cf65249047492634a7ac3aadfecc41cd47aff4baea74e4023', 'size': 93807955}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:ef68ea8aad61900f51a8e8293f386e1549f10a50e396f2f9815fe76224fc11c1', 'size': 93808086}]
CACHED_DESCRIPTORS = []
PROTECTED = {'deerflow.local/skillhub-server:20260908-enterprise-arm64-rc15-skillhubsession2': 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2'}
OPTIONAL_PROTECTED = {'docker.io/library/import-2026-10-09@sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2': 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2', 'deerflow.local/skillhub-server@sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2': 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2', 'deerflow.local/skillhub-server@sha256:b4a18e1955e51ba8fb7ec65df91ee081378ba4f84a63177a850f25e82fd9971a': 'sha256:b4a18e1955e51ba8fb7ec65df91ee081378ba4f84a63177a850f25e82fd9971a'}
REPO = 'deerflow.local/skillhub-server'
TAG = 'deerflow.local/skillhub-server:20261106-registry-auth-r1'
MANIFEST = 'sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68'
CONFIG = 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7'
FINAL_CHAIN = 'sha256:295ce6a6804fd2f342cea69ae7d917c5e259c64ffd489cec2e3cc63beda9f22d'
MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = (3165, 9757, 15)
MIB, GIB = 1024**2, 1024**3
ARCHIVE_MAX, RAW_MAX, HEADERS_MAX = GIB, 2*GIB, 100000
TOTAL_SECONDS = 900
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def load_inputs():
    require(set(SOURCES) == set(SOURCE_PINS) == {'importer', 'resource', 'imagefs'}, 'source_set')
    result = {}
    for name, raw in SOURCES.items():
        require(len(raw) == SOURCE_PINS[name]['bytes'] and sha(raw) == SOURCE_PINS[name]['sha256'], 'source_pin')
        module = types.ModuleType('_fixed_provisioner_' + name)
        exec(compile(raw, '<fixed-' + name + '>', 'exec'), module.__dict__)
        result[name] = module
    I, R, F = (result[k] for k in ('importer', 'resource', 'imagefs'))
    require(SOURCE_PINS['importer'] == {'bytes': 29458, 'sha256': '4b07f6546729c29ed0eebf2db62f531c20b2747b4d6eb80ee0f9ff0c1c7e807f'}, 'fixed_importer')
    I.HOST, I.REPO, I.TAG, I.MANIFEST, I.CONFIG, I.CONTROL = HOST, REPO, TAG, MANIFEST, CONFIG, CONTROL
    I.TOTAL_SECONDS = TOTAL_SECONDS
    R.CONTROL = CONTROL
    R.HOST, R.NODE_UID = HOST, NODES[HOST]
    return I, R, F


def time_gate(started):
    require(time.monotonic() - started < TOTAL_SECONDS, 'total_deadline')


def validate_version(raw):
    require(re.search(r'(?ms)^Server:\s*\n\s*Version:\s*v?2\.3\.3\s*$', raw.decode('utf-8')), 'containerd_server_version_mismatch')


def write_all(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        require(count > 0, 'short_write')
        view = view[count:]


def seal_source(directory, started):
    transport_guard()
    require(os.path.realpath(INPUT) == INPUT, 'input_parent_symlink')
    source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    target = None
    try:
        before = os.fstat(source)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == TRANSPORT_PIN['bytes'], 'input_type_or_size')
        target = os.open('source.sealed.oci.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        digest, count = hashlib.sha256(), 0
        while True:
            time_gate(started)
            block = os.read(source, MIB)
            if not block:
                break
            count += len(block)
            require(count <= TRANSPORT_PIN['bytes'], 'input_bound')
            digest.update(block); write_all(target, block)
        os.fsync(target)
        require(stamp(before) == stamp(os.fstat(source)) and count == TRANSPORT_PIN['bytes']
                and digest.hexdigest() == TRANSPORT_PIN['sha256'], 'input_changed_or_digest')
        os.fchmod(target, 0o400); os.fsync(target); os.fsync(directory)
    finally:
        os.close(source)
        if target is not None:
            os.close(target)
    fd = os.open('source.sealed.oci.tar', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    require_private(fd, TRANSPORT_PIN['bytes'])
    return fd


def transport_guard():
    require(type(TRANSPORT_PIN) is dict and set(TRANSPORT_PIN) == {'bytes', 'sha256'}
            and type(TRANSPORT_PIN['bytes']) is int and 0 < TRANSPORT_PIN['bytes'] <= ARCHIVE_MAX
            and re.fullmatch('[0-9a-f]{64}', TRANSPORT_PIN['sha256']), 'actual_transport_not_sealed')


def require_private(fd, size):
    row = os.fstat(fd)
    require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and row.st_nlink == 1
            and not row.st_mode & 0o277 and row.st_size == size, 'private_fd_identity')


class StrictOuter(tarfile.TarInfo):
    def _proc_member(self, archive):
        require(self.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE), 'outer_header_type')
        return super()._proc_member(archive)


class DigestReader:
    def __init__(self, stream, started):
        self.stream, self.started, self.count, self.digest = stream, started, 0, hashlib.sha256()
    def read(self, size=-1):
        time_gate(self.started)
        require(size >= 0, 'unbounded_layer_read')
        raw = self.stream.read(min(size, MIB))
        self.count += len(raw)
        require(self.count <= RAW_MAX, 'layer_expansion_limit')
        self.digest.update(raw)
        return raw


class LayerHeader(tarfile.TarInfo):
    def _proc_member(self, archive):
        # Never extract; reject huge metadata extensions before tarfile allocates.
        require(self.type not in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
                or self.size <= MIB, 'inner_metadata_bound')
        return super()._proc_member(archive)


def layer_stats(stream, media_type, diff_id, started):
    raw = gzip.GzipFile(fileobj=stream, mode='rb') if media_type.endswith('gzip') else stream
    reader = DigestReader(raw, started)
    headers, regular_bytes = 0, 0
    with tarfile.open(fileobj=reader, mode='r|', tarinfo=LayerHeader) as archive:
        for member in archive:
            time_gate(started); headers += 1
            require(headers <= HEADERS_MAX and 0 <= member.size <= RAW_MAX and not member.issparse(), 'inner_header_limit')
            if member.isreg():
                regular_bytes += member.size
                require(regular_bytes <= RAW_MAX, 'inner_regular_logical_bound')
    while reader.read(MIB):
        pass
    require('sha256:' + reader.digest.hexdigest() == diff_id, 'layer_diff_id')
    if raw is not stream:
        raw.close()
    return {'raw_tar_bytes': reader.count, 'headers': headers, 'regular_file_logical_bytes': regular_bytes}


def inspect_archive(fd, I, started):
    before = os.fstat(fd)
    members = {}
    with os.fdopen(os.dup(fd), 'rb') as stream:
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode='r:', tarinfo=StrictOuter) as archive:
            for item in archive:
                time_gate(started)
                require(len(members) < 64 and item.name not in members and not item.linkname and not item.pax_headers
                        and not item.issparse() and item.offset_data - item.offset == 512, 'outer_member_shape')
                if item.isdir():
                    require(item.name.rstrip('/') in ('blobs', 'blobs/sha256') and item.size == 0, 'outer_directory')
                else:
                    require(item.isreg() and 0 < item.size <= ARCHIVE_MAX, 'outer_regular_bound')
                members[item.name] = item
            def small(name, limit):
                require(name in members and members[name].isreg() and members[name].size <= limit, 'small_metadata_missing')
                raw = archive.extractfile(members[name]).read(limit + 1)
                require(len(raw) == members[name].size, 'small_metadata_short')
                return raw
            mr = small('blobs/sha256/' + MANIFEST[7:], MIB)
            cr = small('blobs/sha256/' + CONFIG[7:], MIB)
            require(len(mr) == MANIFEST_BYTES and 'sha256:' + sha(mr) == MANIFEST and len(cr) == CONFIG_BYTES
                    and 'sha256:' + sha(cr) == CONFIG, 'metadata_fixed_pin')
            m, c = I.parse(mr), I.parse(cr)
            require(m.get('schemaVersion') == 2 and m.get('config', {}).get('digest') == CONFIG
                    and m['config']['size'] == len(cr) and len(m.get('layers', [])) == LAYER_COUNT, 'manifest_contract')
            require(c.get('os') == 'linux' and c.get('architecture') == 'arm64' and c.get('rootfs', {}).get('type') == 'layers'
                    and len(c['rootfs'].get('diff_ids', [])) == LAYER_COUNT, 'config_contract')
            chains = I.chain_rows(c['rootfs']['diff_ids'])
            require(chains[-1]['chain_id'] == FINAL_CHAIN, 'final_chain_identity')
            expected = {MANIFEST: len(mr), CONFIG: len(cr)}
            stats, unique = {}, {}
            for desc, diff in zip(m['layers'], c['rootfs']['diff_ids']):
                I.digest(desc.get('digest')); I.digest(diff)
                require(type(desc.get('size')) is int and 0 < desc['size'] <= ARCHIVE_MAX
                        and desc.get('mediaType') in I.GZIP_TYPES + I.TAR_TYPES, 'layer_descriptor')
                key = desc['digest']
                require(key not in expected or expected[key] == desc['size'], 'duplicate_descriptor_size')
                require(key not in unique or unique[key][1] == diff, 'duplicate_diff_id')
                expected[key] = desc['size']; unique[key] = (desc, diff)
            files = {n for n, item in members.items() if item.isreg()}
            require(files == {'oci-layout', 'index.json'} | {'blobs/sha256/' + d[7:] for d in expected}, 'full_blob_closure')
            layout, index = I.parse(small('oci-layout', MIB)), I.parse(small('index.json', MIB))
            require(layout == {'imageLayoutVersion': '1.0.0'} and index.get('schemaVersion') == 2
                    and type(index.get('manifests')) is list and len(index['manifests']) == 1, 'oci_index_shape')
            top = index['manifests'][0]
            require(top.get('digest') == MANIFEST and top.get('size') == len(mr)
                    and top.get('mediaType') == m.get('mediaType'), 'oci_index_identity')
            for dg, size in expected.items():
                member = members['blobs/sha256/' + dg[7:]]
                require(member.size == size, 'descriptor_size')
                digest, count = hashlib.sha256(), 0
                with archive.extractfile(member) as payload:
                    while True:
                        time_gate(started); block = payload.read(MIB)
                        if not block:
                            break
                        digest.update(block); count += len(block)
                require(count == size and 'sha256:' + digest.hexdigest() == dg, 'blob_digest')
                if dg in unique:
                    desc, diff = unique[dg]
                    with archive.extractfile(member) as payload:
                        stats[dg] = layer_stats(payload, desc['mediaType'], diff, started)
            require(stamp(before) == stamp(os.fstat(fd)), 'sealed_archive_changed')
    return {'manifest': m, 'config': c, 'manifest_raw': mr, 'config_raw': cr, 'chains': chains,
            'blob_sizes': expected, 'layer_stats': stats, 'bundle_sha256': TRANSPORT_PIN['sha256'], 'append_layer': {}}


def rewrite_private(fd, directory, prepared, temporary, I, started):
    target = os.open('import.private.oci.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    before = os.fstat(fd)
    try:
        with os.fdopen(os.dup(fd), 'rb') as source, os.fdopen(target, 'wb', closefd=False) as output:
            source.seek(0)
            with tarfile.open(fileobj=source, mode='r:', tarinfo=StrictOuter) as old, tarfile.open(fileobj=output, mode='w|', format=tarfile.USTAR_FORMAT) as new:
                index = {'schemaVersion': 2, 'manifests': [{'mediaType': prepared['manifest']['mediaType'],
                    'digest': MANIFEST, 'size': len(prepared['manifest_raw']), 'platform': {'os': 'linux', 'architecture': 'arm64'},
                    'annotations': {'io.containerd.image.name': temporary}}]}
                for name, raw in [('oci-layout', canonical({'imageLayoutVersion': '1.0.0'})), ('index.json', canonical(index))]:
                    item = tarfile.TarInfo(name); item.size = len(raw); item.mode = 0o600
                    new.addfile(item, io.BytesIO(raw))
                for dg, size in sorted(prepared['blob_sizes'].items()):
                    time_gate(started); name = 'blobs/sha256/' + dg[7:]
                    original = old.getmember(name); require(original.size == size and original.isreg(), 'private_rewrite_identity')
                    item = tarfile.TarInfo(name); item.size = size; item.mode = 0o600
                    with old.extractfile(original) as payload:
                        new.addfile(item, payload)
                require(output.tell() <= ARCHIVE_MAX, 'private_rewrite_bound')
            output.flush(); os.fsync(target)
        require(stamp(before) == stamp(os.fstat(fd)), 'sealed_archive_changed')
        size = os.fstat(target).st_size
        require(size <= ARCHIVE_MAX, 'private_rewrite_final_bound')
        os.fchmod(target, 0o400); os.fsync(target); os.fsync(directory)
    finally:
        os.close(target)
    result = os.open('import.private.oci.tar', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    require_private(result, size)
    return result


def cache_projection(prepared, content_raw, snapshots_raw):
    expected = set(prepared['blob_sizes'])
    names = content_raw.decode('ascii').splitlines()
    require(len(names) == len(set(names)) and set(names) <= expected, 'cache_content_response')
    lines = snapshots_raw.decode('utf-8').splitlines()
    require(lines and lines[0].split() == ['KEY', 'PARENT', 'KIND'] and len(lines) <= 20001, 'cache_snapshot_shape')
    wanted = {row['chain_id']: row['parent'] for row in prepared['chains']}
    found = set()
    for line in lines[1:]:
        words = line.split()
        if not words or words[0] not in wanted:
            continue
        require(len(words) in (2, 3) and words[0] not in found, 'cache_snapshot_duplicate')
        parent, kind = ('', words[1]) if len(words) == 2 else (words[1], words[2])
        require(parent == wanted[words[0]] and kind == 'Committed', 'cache_snapshot_conflict')
        found.add(words[0])
    require(all(not wanted[key] or wanted[key] in found for key in found), 'cache_snapshot_parent_missing')
    return {'content_present': sorted(names), 'committed_chains': sorted(found),
            'missing_content': sorted(expected-set(names)), 'missing_chains': sorted(set(wanted)-found),
            'scope': 'FRESH_METADATA_ONLY_NOT_A_GC_RESERVATION'}


def budget(prepared, samples, page_size, initial=False, cache=None):
    quantum = max([65536, page_size] + [r['fragment_size'] for r in samples])
    missing = set(cache['missing_chains']) if cache is not None else {r['chain_id'] for r in prepared.get('chains', [])}
    # Duplicate blobs can occur at different chain positions. Unpack cost is
    # counted in ordered chain space, never deduplicated by blob digest.
    costs_by_chain = [prepared['layer_stats'][desc['digest']] for desc, chain in
        zip(prepared.get('manifest', {}).get('layers', []), prepared.get('chains', [])) if chain['chain_id'] in missing]
    raw = sum(row['raw_tar_bytes'] for row in costs_by_chain)
    headers = sum(row['headers'] for row in costs_by_chain)
    physical = 4*(raw + headers*quantum) + 64*MIB
    content_missing = cache['missing_content'] if cache is not None else prepared.get('blob_sizes', {})
    compressed = sum(prepared['blob_sizes'][dg] for dg in content_missing)
    ingest_scratch = sum(prepared.get('blob_sizes', {}).values())
    costs = [3*ARCHIVE_MAX + 96*MIB, compressed + ingest_scratch + 64*MIB, physical]
    groups = {}
    for index, row in enumerate(samples):
        item = groups.setdefault(row['device'], dict(row, paths=[], cost_bytes=0, needed_inodes=4096))
        require(item['capacity_bytes'] == row['capacity_bytes'] and item['total_inodes'] == row['total_inodes'], 'same_device_capacity_changed')
        item['paths'].append(row['path']); item['cost_bytes'] += costs[index]
        item['available_bytes'] = min(item['available_bytes'], row['available_bytes'])
        item['available_inodes'] = min(item['available_inodes'], row['available_inodes'])
        if index == 2:
            item['needed_inodes'] += headers*8
    for item in groups.values():
        # Same explicit datax-01 GC/headroom policy as the accepted small preload;
        # full image and both private archive copies are included in addition.
        item['reserve_bytes'] = (item['capacity_bytes']*16+99)//100 + 3*GIB
        item['required_bytes'] = item['cost_bytes'] + item['reserve_bytes']
        require(item['available_bytes'] >= item['required_bytes']
                and (item['available_inodes']-item['needed_inodes'])*100 > item['total_inodes']*5, 'full_image_resource_budget')
    return {'pass': True, 'initial_before_archive_read': initial, 'devices': list(groups.values()),
            'raw_tar_bytes': raw, 'headers': headers, 'allocation_quantum': quantum,
            'missing_content_bytes': compressed, 'full_ingest_scratch_bytes': ingest_scratch,
            'missing_ordered_chain_count': len(costs_by_chain), 'cache': cache,
            'physical_allowance_bytes': physical, 'filesystem_capacity_reserved': False,
            'twenty_percent_gate_used': False, 'kubelet_configuration_changed': False}


def controller_class(I, R, F):
    class Importer(I.Importer):
        def __init__(self, ctr, cri, kubectl, operation, directory, prepared, started):
            super().__init__(ctr, cri, operation, directory, None, prepared,
                progress=lambda phase: print(json.dumps({'phase': phase}), file=sys.stderr, flush=True))
            self.kubectl, self.started, self.refs = kubectl, started, {}
            self.I, self.R, self.F = I, R, F
            self.disk = self.fresh_budget
            self.report.update(schema='portal-skillhub-registry-auth-cache-preload.v1', expected_node_uid=R.NODE_UID,
                source_pins=SOURCE_PINS, transport_pin=TRANSPORT_PIN, resource_policy_checks=[],
                image_execution=False, existing_image_overwrite=False, same_version_cache_repair=False, expected_node_uid_basis='OPERATOR_PIN_AND_FRESH_API_BEFORE_EACH_IMPORT_PHASE',
                deployment_patch=False, pod_deletion=False, running_cutover_process_modified=False,
                application_started=False, provider_requests=0, tool_revision='r1')
            self.report.pop('append_layer', None); self.report.pop('ordinary_aio_digest_alias_readiness', None)
            for key in ('old_layer_bytes_read', 'old_layer_hashes_verified_now', 'old_layer_sizes_verified_now'):
                self.report.pop(key, None)
            self.report['cached_containerd_payload_bytes_read'] = False

        def call(self, phase, args, mutates=False, timeout=15, limit=MIB, is_cri=False, pass_fds=()):
            require(len(self.report['commands']) < 140, 'command_limit')
            if mutates:
                base = ['images', 'import', '--local', '--platform', 'linux/arm64', '--snapshotter', 'overlayfs', '--label', 'io.cri-containerd.image=managed']
                path = '/proc/self/fd/' + str(self.archive_fd)
                imports = [base+[path], base+['--no-unpack', path]]
                tags = [['images', 'tag', '--local', self.temp, n] for n in (TAG, REPO+'@'+MANIFEST)]
                require(not is_cri and ((args in imports and pass_fds == (self.archive_fd,)) or (args in tags and not pass_fds)), 'mutation_scope')
            return super().call(phase, args, mutates, timeout, limit, is_cri, pass_fds)

        def target(self, name):
            require(name in (TAG, REPO+'@'+MANIFEST, self.temp) or name in PROTECTED or name in OPTIONAL_PROTECTED, 'reference_scope')
            return I.image_target(self.call('image_reference', ['images', 'ls', 'name=='+name]), name)

        def preserved(self):
            for name, expected in self.refs.items():
                actual = self.target(name)
                # Newly created names were absent at start; all existing names
                # must retain their exact digest, never be overwritten.
                require(actual in (None, MANIFEST) if name in (TAG, REPO+'@'+MANIFEST) and expected is None else actual == expected, 'existing_reference_changed')

        def query(self, phase, args):
            require(len(self.report['commands']) < 140, 'command_limit')
            require(args in (['get', 'node', HOST, '-o=jsonpath='+R.NODE_PATH],
                ['get', '--raw', '/api/v1/nodes/'+HOST+'/proxy/configz']), 'kubernetes_read_scope')
            argv = [self.kubectl, '--kubeconfig', '/etc/kubernetes/admin.conf', '--request-timeout=20s'] + args
            out, err, row = self.runner(argv, timeout=min(20, max(0.1, TOTAL_SECONDS-(time.monotonic()-self.started))), limit=MIB)
            row.update(phase=phase, argv_sha256=sha(canonical(argv)), containerd_mutation_requested=False)
            self.report['commands'].append(row); number = len(self.report['commands'])
            for suffix, raw in [('stdout.private.log', out), ('stderr.private.log', err), ('json', canonical(row)+b'\n')]:
                I.write_new(self.directory, 'command-%03d.%s'%(number, suffix), raw)
            require(row.get('returncode') == 0 and row.get('error_code') is None, 'fresh_kubernetes_query_failed')
            return out

        def fresh_budget(self):
            node = self.R.node_identity(self.query('fresh_node_identity', ['get', 'node', HOST, '-o=jsonpath='+self.R.NODE_PATH]))
            policy = self.R.thresholds(self.I.parse(self.query('fresh_kubelet_thresholds', ['get', '--raw', '/api/v1/nodes/'+HOST+'/proxy/configz'])))
            self.report['kubernetes_node_uid_freshly_checked'] = True
            self.report['fresh_node'] = node
            self.report['effective_kubelet_policy'] = policy
            return worker_budget(self)

        def closure(self, manifest, md):
            wanted = {md, manifest['config']['digest']} | {r['digest'] for r in manifest['layers']}
            I.content_names(self.call('content_closure', ['content', 'ls', '--quiet']+['digest=='+d for d in sorted(wanted)], timeout=60), wanted)
            I.verify_manifest_gc(self.labels(md), manifest)

        def verify(self, publish=False):
            require(self.call('manifest_readback', ['content', 'get', MANIFEST]) == self.p['manifest_raw']
                    and self.call('config_readback', ['content', 'get', CONFIG]) == self.p['config_raw'], 'metadata_readback')
            self.closure(self.p['manifest'], MANIFEST); self.snapshots(self.p['chains'])
            require(self.labels(CONFIG).get('containerd.io/gc.ref.snapshot.overlayfs') == FINAL_CHAIN, 'config_snapshot_gc_missing')
            if publish:
                self.ready(self.temp)
                for name in (TAG, REPO+'@'+MANIFEST):
                    self.publish(name)
            for name in (TAG, REPO+'@'+MANIFEST):
                require(self.target(name) == MANIFEST, 'final_reference')
                self.ready(name)
                status = I.parse(self.call('cri_visibility', ['inspecti', '--output=json', name], is_cri=True, limit=4*MIB)).get('status', {})
                require(status.get('id') == CONFIG and TAG in status.get('repoTags', [])
                        and REPO+'@'+MANIFEST in status.get('repoDigests', []), 'cri_identity')

        def execute_import(self):
            for name in (TAG, REPO+'@'+MANIFEST):
                actual = self.target(name); require(actual in (None, MANIFEST), 'canonical_target_conflict'); self.refs[name] = actual
            for name,expected in PROTECTED.items():
                require(self.target(name)==expected,'original_skillhub_cache_identity_changed');self.refs[name]=expected
            for name, expected in OPTIONAL_PROTECTED.items():
                actual = self.target(name)
                require(actual in (None, expected), 'optional_old_reference_identity_changed')
                self.refs[name] = actual
            self.report['references_before'] = dict(self.refs)
            require(self.target(self.temp) is None, 'temporary_reference_exists')
            # Exact full image may already be cached under the original tag. If
            # both names exist, validate rather than re-import or repair silently.
            if all(self.refs[name] == MANIFEST for name in (TAG,REPO+'@'+MANIFEST)):
                self.verify(); self.report['import_skipped_already_complete'] = True
            else:
                self.import_phase('phase_a', True)
                self.closure(self.p['manifest'], MANIFEST)
                self.import_phase('phase_b', False)
                self.verify(publish=True)
            self.preserved(); self.fresh_budget()
            self.report.update(status='SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED',
                image_import_may_be_partial=False, content_and_unpack='PASS', cri_visibility='PASS',
                old_references_preserved=True, references_after={n:self.target(n) for n in self.refs},
                temporary_reference_retained=not self.report.get('import_skipped_already_complete', False))
            return self.report
    return Importer


"""Reconstruct a private complete OCI from fixed delta plus hash-verified local blobs."""
def trusted_directory(path):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in path.strip('/').split('/'):
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
            st=os.fstat(fd);require(st.st_uid==0 and not st.st_mode&0o022,'untrusted_workspace_ancestor')
        return fd
    except BaseException:
        os.close(fd);raise

def worker_budget(self):
    paths=self.F.imagefs_paths(self.I.parse(self.call('actual_imagefs',['imagefsinfo','--output','json'],is_cri=True)))
    require(paths==EXPECTED_IMAGEFS,'audited_datax01_imagefs_changed')
    root=self.R.observed_directory('/var/lib')
    # This node stores images and private working files on the root filesystem.
    # Keep nodefs checks and count every archive/unpack cost in the same device
    # group below; the imagefs 16% + 3GiB reserve is unchanged.
    reserve=(root['capacity_bytes']+9)//10+256*MIB
    require(root['available_bytes']>=reserve and root['available_inodes']*100>root['total_inodes']*5,'root_nodefs_headroom')
    samples=[self.R.observed_directory(CONTROL)]+[self.R.observed_directory(paths[k]) for k in ('content','snapshot')]
    require(len({x['device'] for x in samples})==1,'private_workspace_not_on_imagefs_device')
    cache=None
    if self.p.get('layer_stats'):
        raw=self.call('budget_content_presence',['content','ls','--quiet']+['digest=='+dg for dg in sorted(self.p['blob_sizes'])],timeout=60)
        snaps=self.call('budget_committed_chains',['snapshots','--snapshotter','overlayfs','ls'],limit=4*MIB)
        cache=cache_projection(self.p,raw,snaps)
    result=budget(self.p,samples,os.sysconf('SC_PAGESIZE'),not bool(self.p.get('layer_stats')),cache)
    result['root_nodefs']=dict(root,required_bytes=reserve)
    result['private_workspace_on_imagefs']=True
    result['observed_layout']='DATAX01_ROOT_IMAGEFS'
    result['workspace_parent']=WORKSPACE_PARENT
    return result

def assemble_complete(fd, directory, controller, original, started):
    # The sealed full export is independently checked by inspect_archive before import.
    time_gate(started)
    controller.report.update(cached_containerd_payload_bytes_read=False, cached_payload_bytes=0,
        transport_kind='FULL_HASH_BOUND_OCI', reused_base_blob_count=0)
    return os.dup(fd)


EARLY_BUDGET = {'manifest': {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7', 'size': 9757}, 'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:0b613318ea879878918380aa3aeb220dfe824e311b83bc955cb8a1d4319650ab', 'size': 28887235}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:eddc3451cad891eb57d3979f3ea6ea8eee45eba4fdbd8401534b39284d613623', 'size': 16951779}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b3ac9f9c806f07843af17f4e67a86faf3ca768e97ec15d45e97fae38dcfdc4cc', 'size': 52273651}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:fda3adcb84456bb22c514a2e660753d08ef8187b34279c62a1c2a6feb072b1a2', 'size': 160}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b79577144548e0cc6f704acda68f028bf5d48b0f462dbdd8a6d6ccb0114a8c96', 'size': 2464}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:b9872243667cd19109bada8f630bf0d10820b10a6f07e73cf3b605ef47cf06b5', 'size': 3428}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:6e6333b07ca5fb40744d5b37513940b2e07c5594a76c53f89232d4daa6639b49', 'size': 93}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:3484b0103557e94321199edb0a247f46d76cd0e6b90f0c2fdf435a1f805587d8', 'size': 93804189}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:cdb1b42647d37d2aa7bedc697028ecb04bc8fac5df40905402763f33900179b1', 'size': 93804342}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:e52617dff1b693c0735cd6e8aac9ed37f510275e6fd2b2961fb9f9d8302d7f77', 'size': 93807726}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:668097ee06129a5e8e5e0646c9766a5655d7c18077299e3503e3afbd2651397b', 'size': 93808053}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:aeeef48d72bc0f74eae9cc11e8e4ea33d191e1cc70ec12a2a7669408b852cf7b', 'size': 93947656}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:8903709147330b9b6bde3dd8a99ab1d2e184d37213b23f2ce607f97f9df79376', 'size': 93949507}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:0cbff6932119b26cf65249047492634a7ac3aadfecc41cd47aff4baea74e4023', 'size': 93807955}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:ef68ea8aad61900f51a8e8293f386e1549f10a50e396f2f9815fe76224fc11c1', 'size': 93808086}]}, 'chains': [{'chain_id': 'sha256:646eea22414270d74b0c9e9d6d3b9550701ae62e658a099825d4d15045a3630b', 'parent': ''}, {'chain_id': 'sha256:697b57b78d28fdda8759f720ba4296406fdab0eabebf7267f36a4f6c56e9686c', 'parent': 'sha256:646eea22414270d74b0c9e9d6d3b9550701ae62e658a099825d4d15045a3630b'}, {'chain_id': 'sha256:eb59c0a7aabfcf65e5f7ccc1f474250c94fd042611e967046c9c6f8d469a631d', 'parent': 'sha256:697b57b78d28fdda8759f720ba4296406fdab0eabebf7267f36a4f6c56e9686c'}, {'chain_id': 'sha256:9533f81ace8dbade2b9f623c44a8a0208b69925779ef87f3f16c173488c77036', 'parent': 'sha256:eb59c0a7aabfcf65e5f7ccc1f474250c94fd042611e967046c9c6f8d469a631d'}, {'chain_id': 'sha256:7561515aa8a73803200cbffa04c1efed29a96e22290f66c37335bab828983389', 'parent': 'sha256:9533f81ace8dbade2b9f623c44a8a0208b69925779ef87f3f16c173488c77036'}, {'chain_id': 'sha256:fbc1a0ab4f3ce3962dad1227b9f70883ae35af536b71d5d888522d110effeac3', 'parent': 'sha256:7561515aa8a73803200cbffa04c1efed29a96e22290f66c37335bab828983389'}, {'chain_id': 'sha256:fea5c0eb1d87683af3a14d3cdce96e80f4a7f27a951f4f7d42e5ef1399cb1e47', 'parent': 'sha256:fbc1a0ab4f3ce3962dad1227b9f70883ae35af536b71d5d888522d110effeac3'}, {'chain_id': 'sha256:47706e909245690034eae69ce71f3e9b76c5d95e8b3d4ef9b385a69819e9159b', 'parent': 'sha256:fea5c0eb1d87683af3a14d3cdce96e80f4a7f27a951f4f7d42e5ef1399cb1e47'}, {'chain_id': 'sha256:565833f1ee9bf0b828a27b493c091cd3e5f99fdf1fcbbd35e2d80cbc1bd6ad64', 'parent': 'sha256:47706e909245690034eae69ce71f3e9b76c5d95e8b3d4ef9b385a69819e9159b'}, {'chain_id': 'sha256:6c9a5151bc1dd2467348bc0c53e755b1620f2e5f175213bfa11c85ab34849deb', 'parent': 'sha256:565833f1ee9bf0b828a27b493c091cd3e5f99fdf1fcbbd35e2d80cbc1bd6ad64'}, {'chain_id': 'sha256:92befa58c0bc3eb3f27ef0f784c97b6b7709be3786c094445c6e206ca2142cd7', 'parent': 'sha256:6c9a5151bc1dd2467348bc0c53e755b1620f2e5f175213bfa11c85ab34849deb'}, {'chain_id': 'sha256:0633c3b557680b507f9f3384ebc40c6c69a9bee1851e5ae63e92aca8cd52991b', 'parent': 'sha256:92befa58c0bc3eb3f27ef0f784c97b6b7709be3786c094445c6e206ca2142cd7'}, {'chain_id': 'sha256:78a8f36efa39b841fceca336e23a73a00ca278e65d4e1d86a066498831d2d1a8', 'parent': 'sha256:0633c3b557680b507f9f3384ebc40c6c69a9bee1851e5ae63e92aca8cd52991b'}, {'chain_id': 'sha256:44ee48825c2564c2e41e12bfd24d8bbec007d4f5925d77e37907a4bb66d295e8', 'parent': 'sha256:78a8f36efa39b841fceca336e23a73a00ca278e65d4e1d86a066498831d2d1a8'}, {'chain_id': 'sha256:295ce6a6804fd2f342cea69ae7d917c5e259c64ffd489cec2e3cc63beda9f22d', 'parent': 'sha256:44ee48825c2564c2e41e12bfd24d8bbec007d4f5925d77e37907a4bb66d295e8'}], 'blob_sizes': {'sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68': 3165, 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7': 9757, 'sha256:0b613318ea879878918380aa3aeb220dfe824e311b83bc955cb8a1d4319650ab': 28887235, 'sha256:eddc3451cad891eb57d3979f3ea6ea8eee45eba4fdbd8401534b39284d613623': 16951779, 'sha256:b3ac9f9c806f07843af17f4e67a86faf3ca768e97ec15d45e97fae38dcfdc4cc': 52273651, 'sha256:fda3adcb84456bb22c514a2e660753d08ef8187b34279c62a1c2a6feb072b1a2': 160, 'sha256:b79577144548e0cc6f704acda68f028bf5d48b0f462dbdd8a6d6ccb0114a8c96': 2464, 'sha256:b9872243667cd19109bada8f630bf0d10820b10a6f07e73cf3b605ef47cf06b5': 3428, 'sha256:6e6333b07ca5fb40744d5b37513940b2e07c5594a76c53f89232d4daa6639b49': 93, 'sha256:3484b0103557e94321199edb0a247f46d76cd0e6b90f0c2fdf435a1f805587d8': 93804189, 'sha256:cdb1b42647d37d2aa7bedc697028ecb04bc8fac5df40905402763f33900179b1': 93804342, 'sha256:e52617dff1b693c0735cd6e8aac9ed37f510275e6fd2b2961fb9f9d8302d7f77': 93807726, 'sha256:668097ee06129a5e8e5e0646c9766a5655d7c18077299e3503e3afbd2651397b': 93808053, 'sha256:aeeef48d72bc0f74eae9cc11e8e4ea33d191e1cc70ec12a2a7669408b852cf7b': 93947656, 'sha256:8903709147330b9b6bde3dd8a99ab1d2e184d37213b23f2ce607f97f9df79376': 93949507, 'sha256:0cbff6932119b26cf65249047492634a7ac3aadfecc41cd47aff4baea74e4023': 93807955, 'sha256:ef68ea8aad61900f51a8e8293f386e1549f10a50e396f2f9815fe76224fc11c1': 93808086}, 'layer_stats': {'sha256:0b613318ea879878918380aa3aeb220dfe824e311b83bc955cb8a1d4319650ab': {'raw_tar_bytes': 103204352, 'headers': 3440, 'regular_file_logical_bytes': 100728390}, 'sha256:eddc3451cad891eb57d3979f3ea6ea8eee45eba4fdbd8401534b39284d613623': {'raw_tar_bytes': 47755776, 'headers': 2268, 'regular_file_logical_bytes': 46129636}, 'sha256:b3ac9f9c806f07843af17f4e67a86faf3ca768e97ec15d45e97fae38dcfdc4cc': {'raw_tar_bytes': 165308416, 'headers': 334, 'regular_file_logical_bytes': 165103827}, 'sha256:fda3adcb84456bb22c514a2e660753d08ef8187b34279c62a1c2a6feb072b1a2': {'raw_tar_bytes': 2560, 'headers': 3, 'regular_file_logical_bytes': 0}, 'sha256:b79577144548e0cc6f704acda68f028bf5d48b0f462dbdd8a6d6ccb0114a8c96': {'raw_tar_bytes': 7680, 'headers': 1, 'regular_file_logical_bytes': 5977}, 'sha256:b9872243667cd19109bada8f630bf0d10820b10a6f07e73cf3b605ef47cf06b5': {'raw_tar_bytes': 19968, 'headers': 14, 'regular_file_logical_bytes': 9512}, 'sha256:6e6333b07ca5fb40744d5b37513940b2e07c5594a76c53f89232d4daa6639b49': {'raw_tar_bytes': 1536, 'headers': 1, 'regular_file_logical_bytes': 0}, 'sha256:3484b0103557e94321199edb0a247f46d76cd0e6b90f0c2fdf435a1f805587d8': {'raw_tar_bytes': 103447040, 'headers': 2, 'regular_file_logical_bytes': 103444765}, 'sha256:cdb1b42647d37d2aa7bedc697028ecb04bc8fac5df40905402763f33900179b1': {'raw_tar_bytes': 103449088, 'headers': 6, 'regular_file_logical_bytes': 103444765}, 'sha256:e52617dff1b693c0735cd6e8aac9ed37f510275e6fd2b2961fb9f9d8302d7f77': {'raw_tar_bytes': 103451648, 'headers': 2, 'regular_file_logical_bytes': 103449577}, 'sha256:668097ee06129a5e8e5e0646c9766a5655d7c18077299e3503e3afbd2651397b': {'raw_tar_bytes': 103451648, 'headers': 2, 'regular_file_logical_bytes': 103449596}, 'sha256:aeeef48d72bc0f74eae9cc11e8e4ea33d191e1cc70ec12a2a7669408b852cf7b': {'raw_tar_bytes': 103723008, 'headers': 2, 'regular_file_logical_bytes': 103720788}, 'sha256:8903709147330b9b6bde3dd8a99ab1d2e184d37213b23f2ce607f97f9df79376': {'raw_tar_bytes': 103724544, 'headers': 2, 'regular_file_logical_bytes': 103722294}, 'sha256:0cbff6932119b26cf65249047492634a7ac3aadfecc41cd47aff4baea74e4023': {'raw_tar_bytes': 103453184, 'headers': 2, 'regular_file_logical_bytes': 103450647}, 'sha256:ef68ea8aad61900f51a8e8293f386e1549f10a50e396f2f9815fe76224fc11c1': {'raw_tar_bytes': 103444992, 'headers': 2, 'regular_file_logical_bytes': 103442872}}}

def early_preload_gate(I, R, F, tools, original, started):
    """Reuse the native cache-aware budget without creating a directory or log."""
    parent_path = CONTROL.rsplit('/', 1)[0]
    commands = []
    expected_reads = {
        'actual_imagefs': (True, ['imagefsinfo', '--output', 'json']),
        'budget_content_presence': (False, ['content', 'ls', '--quiet'] + ['digest==' + dg for dg in sorted(EARLY_BUDGET['blob_sizes'])]),
        'budget_committed_chains': (False, ['snapshots', '--snapshotter', 'overlayfs', 'ls']),
    }

    def call(phase, args, mutates=False, timeout=15, limit=MIB, is_cri=False, pass_fds=()):
        require(not mutates and not pass_fds and phase in expected_reads
                and (is_cri, args) == expected_reads[phase] and len(commands) < 3,
                'preclaim_read_scope')
        time_gate(started)
        argv = ([tools[1], '--runtime-endpoint', 'unix://' + I.SOCKET, '--image-endpoint', 'unix://' + I.SOCKET]
                if is_cri else [tools[0], '--address', I.SOCKET, '--namespace', 'k8s.io']) + args
        out, unused, receipt = original.run_bounded(argv, timeout=min(timeout, TOTAL_SECONDS - (time.monotonic() - started)), limit=limit)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'preclaim_metadata_query_failed')
        commands.append({'phase': phase, 'containerd_mutation_requested': False})
        return out

    # Before mkdir, the parent supplies exactly the future control directory's
    # device/capacity. The unchanged worker_budget retains both nodefs and
    # imagefs reserves and accounts for every missing ordered unpack chain.
    resource = types.SimpleNamespace(observed_directory=lambda path: R.observed_directory(parent_path if path == CONTROL else path))
    probe = types.SimpleNamespace(I=I, R=resource, F=F, p=EARLY_BUDGET, call=call)
    capacity = worker_budget(probe)
    require(os.path.realpath(INPUT) == INPUT, 'input_parent_symlink')
    source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(source)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == TRANSPORT_PIN['bytes'], 'input_type_or_size')
        digest, count = hashlib.sha256(), 0
        while True:
            time_gate(started)
            block = os.read(source, MIB)
            if not block:
                break
            count += len(block)
            require(count <= TRANSPORT_PIN['bytes'], 'input_bound')
            digest.update(block)
        require(stamp(before) == stamp(os.fstat(source)) and count == TRANSPORT_PIN['bytes']
                and digest.hexdigest() == TRANSPORT_PIN['sha256'], 'input_changed_or_digest')
    finally:
        os.close(source)
    return {'pass': True, 'capacity': capacity, 'commands': commands, 'input_hash_verified': True,
            'filesystem_capacity_reserved': False, 'files_written': False, 'containerd_mutation_requested': False}


def fresh_controlplane(R, kubectl, runner, started):
    results = []
    queries = [(['get', 'node', HOST, '-o=jsonpath='+R.NODE_PATH], R.node_identity),
               (['get', '--raw', '/api/v1/nodes/'+HOST+'/proxy/configz'], lambda raw: R.thresholds(json.loads(raw)))]
    for args, validate in queries:
        time_gate(started)
        argv = [kubectl, '--kubeconfig', '/etc/kubernetes/admin.conf', '--request-timeout=20s'] + args
        raw, unused, receipt = runner(argv, timeout=min(20, TOTAL_SECONDS-(time.monotonic()-started)), limit=MIB)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'fresh_controlplane_read_failed')
        results.append(validate(raw))
    return {'node': results[0], 'kubelet_policy': results[1], 'queries': 2, 'mutations': 0}


def preclaim_references(I, ctr, runner, started):
    refs = {}
    expected = dict(PROTECTED)
    expected.update(OPTIONAL_PROTECTED)
    expected.update({TAG: MANIFEST, REPO+'@'+MANIFEST: MANIFEST})
    for name, wanted in sorted(expected.items()):
        time_gate(started)
        argv = [ctr, '--address', I.SOCKET, '--namespace', 'k8s.io', 'images', 'ls', 'name=='+name]
        raw, unused, receipt = runner(argv, timeout=min(20, TOTAL_SECONDS-(time.monotonic()-started)), limit=MIB)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'preclaim_reference_read_failed')
        actual = I.image_target(raw, name)
        require(actual == wanted if name in PROTECTED else actual in (None, wanted), 'preclaim_image_reference_changed')
        refs[name] = actual
    return refs


def main():
    started = time.monotonic()
    phase = 'HOST_AND_ARGUMENTS'
    directory = parent = source = rewritten = None
    I = R = accounting = controller = None
    report = {'schema': 'portal-skillhub-registry-auth-cache-preload.v1', 'status': 'INCOMPLETE',
        'image_import_requested': False, 'image_import_may_be_partial': False, 'ready_for_rollout': False,
        'automatic_retry': False, 'production_workload_mutation': False, 'database_access': False,
        'image_execution': False, 'image_deletion': False, 'mutation_once_claimed': False}
    try:
        global HOST
        HOST = os.uname().nodename.split('.')[0]
        report['hostname'] = HOST
        require(HOST in NODES,'fixed_skillhub_node_required')
        require(sys.argv[1:] == ['--preload-skillhub-registry-auth-once'], 'arguments')
        require(os.name == 'posix' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode
                and os.uname().machine in ('arm64', 'aarch64') and os.uname().nodename.split('.')[0] == HOST, 'fixed_target_platform')
        phase = 'EMBEDDED_HELPERS'
        I, R, F = load_inputs()
        transport_guard()
        accounting = R.BudgetIO(I, started, Refused)
        # Module functions resolve write_new dynamically; retain original bound
        # functions in an independent namespace to prevent wrapper recursion.
        original = types.SimpleNamespace(**I.__dict__)
        accounting.original = original
        I.write_new = accounting.write_new
        I.run_bounded = accounting.run_bounded
        phase = 'LOCAL_TOOLS'
        tools = [shutil.which(n, path=SAFE_PATH) for n in ('ctr', 'crictl')]
        require(all(tools), 'required_tools_missing')
        tools.append(shutil.which('kubectl', path=SAFE_PATH))
        require(tools[2], 'kubectl_required_for_fresh_node_check')
        phase = 'WORKSPACE_PARENT_OPEN'
        parent = trusted_directory(WORKSPACE_PARENT)
        if os.path.lexists(CONTROL):
            report['mutation_once_claimed'] = True
            raise Refused('once_exists_review_required')
        fresh = fresh_controlplane(R, tools[2], original.run_bounded, started)
        preclaim = early_preload_gate(I, R, F, tools, original, started)
        preclaim['controlplane'] = fresh
        preclaim['old_and_candidate_references'] = preclaim_references(I, tools[0], original.run_bounded, started)
        report['preclaim'] = preclaim
        report['operation'] = uuid.uuid4().hex
        report['mutation_once_claimed'] = False
        phase = 'WORKSPACE_CREATE'
        os.mkdir(CONTROL.rsplit('/', 1)[1], 0o700, dir_fd=parent)
        report['mutation_once_claimed'] = True
        os.fsync(parent)
        directory = os.open(CONTROL.rsplit('/', 1)[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        operation = report['operation']
        report['mutation_once_claimed'] = True
        phase = 'START_RECEIPT_WRITE'
        I.write_new(directory, 'STARTED.json', canonical({'operation': operation, 'source_pins': SOURCE_PINS, 'transport_pin': TRANSPORT_PIN})+b'\n')
        def interrupted(_number, _frame):
            raise KeyboardInterrupt()
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(number, interrupted)
        phase = 'CONTROLLER_INITIALIZE'
        controller = controller_class(I, R, F)(*tools, operation, directory,
            {'bundle_sha256': TRANSPORT_PIN['sha256'], 'append_layer': {}}, started)
        report = controller.report
        report['preclaim'] = preclaim
        report['mutation_once_claimed'] = True
        report['mutation_once_claimed'] = True
        report['observed_layout'] = 'DATAX01_ROOT_IMAGEFS'
        phase = 'CONTAINERD_PREFLIGHT'
        validate_version(controller.call('containerd_server_version', ['version']))
        report['storage_checks'].append({'phase': 'BEFORE_ARCHIVE_SEAL', 'budget': controller.fresh_budget()})
        phase = 'INPUT_SEAL'
        source = seal_source(directory, started)
        delta_source = source
        phase = 'COMPLETE_OCI_RECONSTRUCTION'
        source = assemble_complete(delta_source,directory,controller,original,started)
        os.close(delta_source)
        phase = 'COMPLETE_OCI_INSPECTION'
        controller.p = inspect_archive(source, I, started)
        report['verified_archive'] = {'transport_pin': TRANSPORT_PIN, 'layers': LAYER_COUNT,
            'unique_blob_count': len(controller.p['blob_sizes']), 'all_blob_hashes_verified': True,
            'all_layer_diff_ids_verified': True, 'tar_stats': controller.p['layer_stats']}
        report['storage_checks'].append({'phase': 'BEFORE_PRIVATE_REWRITE', 'budget': controller.fresh_budget()})
        phase = 'PRIVATE_ARCHIVE_AND_BUDGET'
        rewritten = rewrite_private(source, directory, controller.p, controller.temp, I, started)
        controller.archive_fd = rewritten
        phase = 'IMPORT_AND_UNPACK'
        report = controller.execute_import()
    except BaseException as error:
        if isinstance(error, FileExistsError):
            report['mutation_once_claimed'] = True
        code = str(error) if isinstance(error, Refused) else getattr(error, 'code', None)
        if not isinstance(code, str) or not re.fullmatch('[a-zA-Z0-9_]{1,120}', code):
            code = 'once_exists_review_required' if isinstance(error, FileExistsError) else 'local_failure_or_interrupted'
        report.update(status='INCOMPLETE', error_code=code, error_class=type(error).__name__, os_errno=getattr(error,'errno',None), failure_phase=phase)
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        if directory is not None and I is not None:
            try:
                raw = canonical(report)+b'\n'
                require(len(raw) <= 2*MIB, 'safe_report_size')
                accounting.original.write_new(directory, 'REPORT.json', raw)
                report.update(report_path=CONTROL+'/REPORT.json', report_bytes=len(raw), report_sha256=sha(raw))
            except BaseException:
                report.update(status='INCOMPLETE', error_code='report_persistence_failed')
        for fd in (rewritten, source, directory, parent):
            if fd is not None:
                os.close(fd)
    good = report.get('status') == 'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED'
    summary = {k: report.get(k) for k in ('schema', 'status', 'operation', 'hostname', 'manifest_digest', 'config_digest',
        'error_code', 'error_class', 'os_errno', 'failure_phase', 'mutation_once_claimed', 'observed_layout', 'image_import_requested', 'image_import_may_be_partial', 'content_and_unpack', 'cri_visibility',
        'old_references_preserved', 'kubernetes_node_uid_freshly_checked', 'phase_a', 'phase_b', 'canonical_names_created',
        'report_path', 'report_bytes', 'report_sha256', 'elapsed_seconds')}
    summary.update(mutation_once_claimed=report.get('mutation_once_claimed', False),
                   preclaim_passed=report.get('preclaim', {}).get('pass', False),
                   preflight_retry_available=not report.get('mutation_once_claimed', False),
                   expected_node_uid=NODES.get(HOST), exitcode=0 if good else 1, automatic_retry=False, production_workload_mutation=False, ready_for_rollout=False,
                   database_access=False, application_started=False, provider_requests=0, tool_revision='r1')
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary['exitcode']


if __name__ == '__main__':
    sys.exit(main())
