"""One receipt-bound frontend image CAS; package only after actual field receipts."""
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import time
import types
import uuid

FRONT_COMMON = 'import copy,hashlib,json,os,re,time,types\nHELPER=b\'#!/usr/bin/env python3\\n"""Fixed-node, single-attempt Host thin OCI preload; no workload execution. Python 3.7+."""\\nimport argparse\\nimport gzip\\nimport hashlib\\nimport io\\nimport json\\nimport os\\nimport re\\nimport shutil\\nimport signal\\nimport stat\\nimport subprocess\\nimport sys\\nimport tarfile\\nimport threading\\nimport time\\nimport uuid\\n\\nHOST = \\\'ecs-zdz-pro-0012\\\'\\nSOCKET = \\\'/run/containerd/containerd.sock\\\'\\nCONTROL = \\\'/var/lib/portal-ppt-host-delta-import-r1\\\'\\nINPUT = \\\'/home/cnooc/host.delta.three-blobs.tar\\\'\\nINPUT_BYTES = 286720\\nINPUT_SHA256 = \\\'0a26f0cb88036981b4f8b19bd1e1c2ff463eba852845aa968e5fb52988b2d3bb\\\'\\nHANDOFF_SHA256 = \\\'26acc3664652cdad65089e1e40a8a7e8a932f8ac673767665320f5bb7f0c94b2\\\'\\nAPPEND_DIGEST = \\\'sha256:bf6f9be084986fb9a1f8d6128d02b8e97b4f917d22b84aadb7113171fafb6e74\\\'\\nAPPEND_DIFF_ID = \\\'sha256:5ed9d4fa761e32cf25bf23cc03317f42ddefb7967f022e55b806437eb3dc55b2\\\'\\nIMAGEFS = \\\'/mnt/data/containerd/io.containerd.snapshotter.v1.overlayfs\\\'\\nREPO = \\\'deerflow.local/deer-flow-aio-sandbox\\\'\\nTAG = REPO + \\\':20261017-portal-ppt-confirm-host-r1-57f68a146414\\\'\\nMANIFEST = \\\'sha256:540c96e05aa5c0c7709871494dd094047223eaaf6393b2117e9149c0d1467971\\\'\\nCONFIG = \\\'sha256:aabaa4f970580fb5edac54745bedd558ddedc0768bebaeb663130272e76f043d\\\'\\nOLD_TAG = REPO + \\\':20260927-ppt-runtime-r2-agent-host-3959a039ee854dca\\\'\\nOLD_MANIFEST = \\\'sha256:126fabc46a5d4d3b9d50924722bf10e7a1003a4d1adbdf72ace8ae25e188f97e\\\'\\nOLD_CONFIG = \\\'sha256:09ea44f66e1d47269b10e05f11f933f9f08897a1e5a55222a138f0a1b1acfaea\\\'\\nAIO_TAG = REPO + \\\':20260921-enterprise-arm64-rc15-document-runtime1\\\'\\nAIO_MANIFEST = \\\'sha256:b12286cbe09b19577896f8f00177eb3872e8bd100aa0faec6184e82ee66bb4f5\\\'\\nDESCRIPTOR_PREFIX = \\\'0956e8470be3921140b2263f0e23ce294a41665c8e1cedc69041111fe92c03d4\\\'\\nDIFF_ID_PREFIX = \\\'cf7215c42b1e8c6dd264c6d922613d7b4b26e64888baa5231d017e248744a482\\\'\\nBASE_CHAIN = \\\'sha256:389ef3a9ff1e0e33c416188c729cd1f42c4173ce7af27f05f4c495717f9f0089\\\'\\nSMALL, BUNDLE_MAX, LAYER_MAX, EXPANDED_MAX = 1024 * 1024, 12 * 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024\\nTOTAL_SECONDS = 600\\nGZIP_TYPES = (\\\'application/vnd.oci.image.layer.v1.tar+gzip\\\', \\\'application/vnd.docker.image.rootfs.diff.tar.gzip\\\')\\nTAR_TYPES = (\\\'application/vnd.oci.image.layer.v1.tar\\\', \\\'application/vnd.docker.image.rootfs.diff.tar\\\')\\n\\n\\nclass Failure(Exception):\\n    def __init__(self, code, evidence=None):\\n        self.code, self.evidence = code, evidence or {}\\n        Exception.__init__(self, code)\\n\\n\\ndef require(ok, code):\\n    if not ok: raise Failure(code)\\n\\n\\ndef canonical(value):\\n    return json.dumps(value, sort_keys=True, separators=(\\\',\\\', \\\':\\\'), ensure_ascii=True).encode(\\\'utf-8\\\')\\n\\n\\ndef sha(raw):\\n    return hashlib.sha256(raw).hexdigest()\\n\\n\\ndef digest(value):\\n    require(type(value) is str and re.fullmatch(r\\\'sha256:[0-9a-f]{64}\\\', value), \\\'invalid_digest\\\')\\n    return value\\n\\n\\ndef parse(raw):\\n    def pairs(rows):\\n        result = {}\\n        for k, v in rows:\\n            require(k not in result, \\\'duplicate_json_key\\\'); result[k] = v\\n        return result\\n    try:\\n        value = json.loads(raw.decode(\\\'utf-8\\\'), object_pairs_hook=pairs,\\n                           parse_constant=lambda _: (_ for _ in ()).throw(Failure(\\\'nonfinite_json\\\')))\\n        require(type(value) is dict, \\\'json_object_required\\\')\\n        return value\\n    except Failure: raise\\n    except Exception: raise Failure(\\\'invalid_json\\\')\\n\\n\\ndef chain_rows(ids):\\n    rows, parent = [], \\\'\\\'\\n    for d in ids:\\n        digest(d)\\n        key = d if not parent else \\\'sha256:\\\' + sha((parent + \\\' \\\' + d).encode(\\\'ascii\\\'))\\n        rows.append({\\\'chain_id\\\': key, \\\'parent\\\': parent}); parent = key\\n    return rows\\n\\n\\ndef validate_metadata(mraw, craw, manifest_digest, config_digest, count):\\n    require(0 < len(mraw) <= SMALL and \\\'sha256:\\\' + sha(mraw) == manifest_digest, \\\'manifest_digest_or_size\\\')\\n    m = parse(mraw)\\n    require(m.get(\\\'schemaVersion\\\') == 2 and type(m.get(\\\'layers\\\')) is list and len(m[\\\'layers\\\']) == count, \\\'manifest_layers\\\')\\n    conf = m.get(\\\'config\\\', {})\\n    require(conf.get(\\\'digest\\\') == config_digest and type(conf.get(\\\'size\\\')) is int and conf[\\\'size\\\'] == len(craw) <= SMALL, \\\'config_descriptor\\\')\\n    require(\\\'sha256:\\\' + sha(craw) == config_digest, \\\'config_digest\\\')\\n    c = parse(craw)\\n    require(c.get(\\\'os\\\') == \\\'linux\\\' and c.get(\\\'architecture\\\') == \\\'arm64\\\', \\\'platform_mismatch\\\')\\n    ids = c.get(\\\'rootfs\\\', {}).get(\\\'diff_ids\\\')\\n    require(c.get(\\\'rootfs\\\', {}).get(\\\'type\\\') == \\\'layers\\\' and type(ids) is list and len(ids) == count, \\\'diff_id_count\\\')\\n    require(sha(canonical(m[\\\'layers\\\'][:128])) == DESCRIPTOR_PREFIX and sha(canonical(ids[:128])) == DIFF_ID_PREFIX, \\\'base_prefix_mismatch\\\')\\n    for layer in m[\\\'layers\\\']:\\n        digest(layer.get(\\\'digest\\\'))\\n        require(type(layer.get(\\\'size\\\')) is int and layer[\\\'size\\\'] > 0, \\\'layer_size\\\')\\n        require(layer.get(\\\'mediaType\\\') in GZIP_TYPES + TAR_TYPES, \\\'layer_media_type\\\')\\n    chains = chain_rows(ids)\\n    require(chains[127][\\\'chain_id\\\'] == BASE_CHAIN, \\\'base_chain_mismatch\\\')\\n    return m, c, chains\\n\\n\\ndef verify_layer(raw, layer, diff_id):\\n    require(0 < len(raw) == layer[\\\'size\\\'] <= LAYER_MAX and \\\'sha256:\\\' + sha(raw) == layer[\\\'digest\\\'], \\\'append_layer_digest_or_size\\\')\\n    try:\\n        stream = gzip.GzipFile(fileobj=io.BytesIO(raw), mode=\\\'rb\\\') if layer[\\\'mediaType\\\'] in GZIP_TYPES else io.BytesIO(raw)\\n        with stream:\\n            expanded = stream.read(EXPANDED_MAX + 1)\\n            require(len(expanded) <= EXPANDED_MAX and not stream.read(1), \\\'append_layer_expansion_limit\\\')\\n    except Failure: raise\\n    except Exception: raise Failure(\\\'append_layer_decompression\\\')\\n    require(\\\'sha256:\\\' + sha(expanded) == diff_id, \\\'append_layer_diff_id\\\')\\n    return len(expanded)\\n\\n\\ndef unpack_handoff(raw):\\n    require(len(raw) == INPUT_BYTES and sha(raw) == INPUT_SHA256, \\\'handoff_transport_pin\\\')\\n    allowed = {\\\'HANDOFF.json\\\': SMALL, \\\'candidate.manifest.json\\\': SMALL, \\\'candidate.config.json\\\': SMALL, \\\'append.layer.blob\\\': LAYER_MAX}\\n    files = {}\\n    try:\\n        with tarfile.open(fileobj=io.BytesIO(raw), mode=\\\'r:\\\') as archive:\\n            for entry in archive:\\n                require(entry.name in allowed and entry.name not in files and entry.isreg() and not entry.issparse() and not entry.pax_headers,\\n                        \\\'handoff_member_shape\\\')\\n                require(0 < entry.size <= allowed[entry.name], \\\'handoff_member_limit\\\')\\n                value = archive.extractfile(entry).read(allowed[entry.name] + 1)\\n                require(len(value) == entry.size, \\\'handoff_member_truncated\\\'); files[entry.name] = value\\n    except Failure: raise\\n    except Exception: raise Failure(\\\'handoff_tar_invalid\\\')\\n    require(set(files) == set(allowed), \\\'handoff_member_set\\\')\\n    require(len(files[\\\'HANDOFF.json\\\']) == 1248 and sha(files[\\\'HANDOFF.json\\\']) == HANDOFF_SHA256, \\\'handoff_receipt_pin\\\')\\n    require(len(files[\\\'candidate.manifest.json\\\']) == 20436 and len(files[\\\'candidate.config.json\\\']) == 123547,\\n            \\\'candidate_metadata_fixed_sizes\\\')\\n    m, c, chains = validate_metadata(files[\\\'candidate.manifest.json\\\'], files[\\\'candidate.config.json\\\'], MANIFEST, CONFIG, 129)\\n    require(m[\\\'layers\\\'][-1][\\\'digest\\\'] == APPEND_DIGEST and m[\\\'layers\\\'][-1][\\\'size\\\'] == 130980 and c[\\\'rootfs\\\'][\\\'diff_ids\\\'][-1] == APPEND_DIFF_ID,\\n            \\\'append_fixed_receipt_identity\\\')\\n    expanded = verify_layer(files[\\\'append.layer.blob\\\'], m[\\\'layers\\\'][-1], c[\\\'rootfs\\\'][\\\'diff_ids\\\'][-1])\\n    require(expanded == 563200, \\\'append_fixed_expanded_size\\\')\\n    handoff = parse(files[\\\'HANDOFF.json\\\'])\\n    require(handoff.get(\\\'schema\\\') == \\\'portal-ppt-host-three-blob-handoff.v1\\\' and handoff.get(\\\'manifest_digest\\\') == MANIFEST and handoff.get(\\\'config_digest\\\') == CONFIG,\\n            \\\'handoff_identity\\\')\\n    wanted = {k: {\\\'bytes\\\': len(v), \\\'sha256\\\': sha(v)} for k, v in files.items() if k != \\\'HANDOFF.json\\\'}\\n    require(handoff.get(\\\'files\\\') == wanted, \\\'handoff_file_receipts\\\')\\n    require(handoff.get(\\\'append_layer\\\') == {\\\'digest\\\': m[\\\'layers\\\'][-1][\\\'digest\\\'], \\\'size\\\': m[\\\'layers\\\'][-1][\\\'size\\\'],\\n            \\\'media_type\\\': m[\\\'layers\\\'][-1][\\\'mediaType\\\'], \\\'config_size\\\': len(files[\\\'candidate.config.json\\\']),\\n            \\\'diff_id\\\': c[\\\'rootfs\\\'][\\\'diff_ids\\\'][-1], \\\'uncompressed_bytes\\\': expanded}, \\\'handoff_layer_receipt\\\')\\n    return {\\\'files\\\': files, \\\'manifest\\\': m, \\\'config\\\': c, \\\'chains\\\': chains, \\\'bundle_sha256\\\': sha(raw),\\n            \\\'append_layer\\\': handoff[\\\'append_layer\\\']}\\n\\n\\ndef temporary_tag(operation):\\n    require(type(operation) is str and re.fullmatch(r\\\'[0-9a-f]{32}\\\', operation), \\\'operation_invalid\\\')\\n    return REPO + \\\':portal-ppt-delta-private-\\\' + operation\\n\\n\\ndef make_oci(prepared, operation):\\n    name = temporary_tag(operation)\\n    items = {\\\'oci-layout\\\': canonical({\\\'imageLayoutVersion\\\': \\\'1.0.0\\\'}),\\n             \\\'index.json\\\': canonical({\\\'schemaVersion\\\': 2, \\\'manifests\\\': [{\\\'mediaType\\\': \\\'application/vnd.oci.image.manifest.v1+json\\\',\\n                 \\\'digest\\\': MANIFEST, \\\'size\\\': len(prepared[\\\'files\\\'][\\\'candidate.manifest.json\\\']),\\n                 \\\'platform\\\': {\\\'os\\\': \\\'linux\\\', \\\'architecture\\\': \\\'arm64\\\'}, \\\'annotations\\\': {\\\'io.containerd.image.name\\\': name}}]})}\\n    for label, dg in [(\\\'candidate.manifest.json\\\', MANIFEST), (\\\'candidate.config.json\\\', CONFIG), (\\\'append.layer.blob\\\', prepared[\\\'append_layer\\\'][\\\'digest\\\'])]:\\n        items[\\\'blobs/sha256/\\\' + dg.split(\\\':\\\')[1]] = prepared[\\\'files\\\'][label]\\n    out = io.BytesIO()\\n    with tarfile.open(fileobj=out, mode=\\\'w\\\', format=tarfile.USTAR_FORMAT) as archive:\\n        for path in sorted(items):\\n            blob = items[path]; header = tarfile.TarInfo(path)\\n            header.size, header.mode, header.uid, header.gid, header.mtime = len(blob), 0o600, 0, 0, 0\\n            archive.addfile(header, io.BytesIO(blob))\\n    value = out.getvalue(); require(len(value) <= BUNDLE_MAX, \\\'oci_size_limit\\\')\\n    return value\\n\\n\\ndef image_target(raw, name):\\n    try: rows = [r.split() for r in raw.decode(\\\'utf-8\\\').splitlines() if r.strip()]\\n    except Exception: raise Failure(\\\'image_table_encoding\\\')\\n    require(rows and rows[0][:3] == [\\\'REF\\\', \\\'TYPE\\\', \\\'DIGEST\\\'], \\\'image_table_header\\\')\\n    require(len(rows) <= 2 and all(len(r) >= 3 and r[0] == name for r in rows[1:]), \\\'image_filter_response\\\')\\n    return digest(rows[1][2]) if len(rows) == 2 else None\\n\\n\\ndef content_names(raw, expected):\\n    try: rows = raw.decode(\\\'ascii\\\').splitlines()\\n    except Exception: raise Failure(\\\'content_table_encoding\\\')\\n    require(len(rows) == len(set(rows)) and set(rows) == set(expected), \\\'content_closure_missing_or_unexpected\\\')\\n\\n\\ndef content_labels(raw, dg):\\n    try: rows = [r.split() for r in raw.decode(\\\'utf-8\\\').splitlines() if r.strip()]\\n    except Exception: raise Failure(\\\'content_labels_encoding\\\')\\n    require(len(rows) == 2 and rows[0] == [\\\'DIGEST\\\', \\\'SIZE\\\', \\\'AGE\\\', \\\'LABELS\\\'] and rows[1][0] == dg and len(rows[1]) >= 4, \\\'content_label_response\\\')\\n    tokens = rows[1][-1].split(\\\',\\\'); values = {}\\n    for token in tokens:\\n        if token == \\\'-\\\': continue\\n        require(\\\'=\\\' in token, \\\'content_label_shape\\\')\\n        k, v = token.split(\\\'=\\\', 1); require(k not in values, \\\'content_label_duplicate\\\'); values[k] = v\\n    return values\\n\\n\\ndef verify_manifest_gc(labels, manifest):\\n    expected = {\\\'containerd.io/gc.ref.content.config\\\': manifest[\\\'config\\\'][\\\'digest\\\']}\\n    expected.update({\\\'containerd.io/gc.ref.content.l.\\\' + str(i): d[\\\'digest\\\'] for i, d in enumerate(manifest[\\\'layers\\\'])})\\n    require(all(labels.get(k) == v for k, v in expected.items()), \\\'manifest_gc_closure_missing\\\')\\n\\n\\ndef verify_snapshots(raw, chains):\\n    try: rows = raw.decode(\\\'utf-8\\\').splitlines()\\n    except Exception: raise Failure(\\\'snapshot_encoding\\\')\\n    require(rows and rows[0].split() == [\\\'KEY\\\', \\\'PARENT\\\', \\\'KIND\\\'] and len(rows) <= 20001, \\\'snapshot_table_shape\\\')\\n    wanted = {r[\\\'chain_id\\\']: r[\\\'parent\\\'] for r in chains}; found = {}\\n    for line in rows[1:]:\\n        words = line.split()\\n        if not words or words[0] not in wanted: continue\\n        require(len(words) in (2, 3) and words[0] not in found, \\\'snapshot_duplicate_or_shape\\\')\\n        parent, kind = (\\\'\\\', words[1]) if len(words) == 2 else (words[1], words[2])\\n        require(parent == wanted[words[0]] and kind == \\\'Committed\\\', \\\'snapshot_chain_not_committed\\\')\\n        found[words[0]] = parent\\n    require(set(found) == set(wanted), \\\'snapshot_chain_missing\\\')\\n\\n\\ndef run_bounded(argv, timeout, limit, pass_fds=()):\\n    started = time.monotonic(); proc = None; threads = []; outputs = {\\\'stdout\\\': bytearray(), \\\'stderr\\\': bytearray()}\\n    counts = {\\\'stdout\\\': 0, \\\'stderr\\\': 0}; exceeded = threading.Event(); failed = threading.Event()\\n    reason = None; rc = None\\n    def drain(pipe, name):\\n        try:\\n            while True:\\n                chunk = pipe.read(65536)\\n                if not chunk: break\\n                counts[name] += len(chunk)\\n                outputs[name].extend(chunk[:max(0, limit - len(outputs[name]))])\\n                if counts[name] > limit: exceeded.set()\\n        except Exception: failed.set()\\n        finally: pipe.close()\\n    try:\\n        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,\\n            start_new_session=True, pass_fds=pass_fds,\\n            env={\\\'PATH\\\': \\\'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\\\', \\\'HOME\\\': \\\'/root\\\', \\\'LANG\\\': \\\'C.UTF-8\\\'})\\n        for name, stream in [(\\\'stdout\\\', proc.stdout), (\\\'stderr\\\', proc.stderr)]:\\n            t = threading.Thread(target=drain, args=(stream, name), daemon=True); threads.append(t); t.start()\\n        while proc.poll() is None:\\n            if exceeded.is_set(): reason = \\\'command_output_limit\\\'; break\\n            if time.monotonic() - started >= timeout: reason = \\\'command_timeout\\\'; break\\n            time.sleep(0.02)\\n        if reason is None:\\n            rc = proc.returncode\\n            for t in threads: t.join(timeout=0.5)\\n            if any(t.is_alive() for t in threads): reason = \\\'command_pipe_open\\\'\\n            elif exceeded.is_set(): reason = \\\'command_output_limit\\\'\\n            elif failed.is_set(): reason = \\\'command_pipe_failed\\\'\\n            elif rc != 0: reason = \\\'command_nonzero\\\'\\n    except BaseException as exc:\\n        reason = \\\'interrupted\\\' if isinstance(exc, KeyboardInterrupt) else \\\'command_start_or_io_failed\\\'\\n    finally:\\n        if proc is not None:\\n            try: os.killpg(proc.pid, signal.SIGKILL)\\n            except ProcessLookupError: pass\\n            except OSError: reason = reason or \\\'command_cleanup_failed\\\'\\n            try: rc = proc.wait(timeout=2)\\n            except subprocess.TimeoutExpired: reason = reason or \\\'command_cleanup_timeout\\\'\\n        for t in threads: t.join(timeout=0.5)\\n    receipt = {\\\'returncode\\\': rc, \\\'error_code\\\': reason, \\\'elapsed_seconds\\\': round(time.monotonic() - started, 3),\\n               \\\'stdout_bytes\\\': counts[\\\'stdout\\\'], \\\'stderr_bytes\\\': counts[\\\'stderr\\\'],\\n               \\\'stdout_sha256\\\': sha(bytes(outputs[\\\'stdout\\\'])), \\\'stderr_sha256\\\': sha(bytes(outputs[\\\'stderr\\\'])),\\n               \\\'output_limit_exceeded\\\': exceeded.is_set(), \\\'automatic_retry\\\': False}\\n    return bytes(outputs[\\\'stdout\\\']), bytes(outputs[\\\'stderr\\\']), receipt\\n\\n\\ndef write_new(directory, name, raw):\\n    require(re.fullmatch(r\\\'[a-zA-Z0-9_.-]+\\\', name) and len(raw) <= BUNDLE_MAX, \\\'output_name_or_size\\\')\\n    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)\\n    try:\\n        data = memoryview(raw)\\n        while data:\\n            n = os.write(f, data); require(n > 0, \\\'short_write\\\'); data = data[n:]\\n        os.fsync(f)\\n    finally: os.close(f)\\n    os.fsync(directory)\\n\\n\\nclass Importer:\\n    def __init__(self, ctr, cri, operation, output_fd, archive_fd, prepared, runner=None, clock=None, disk=None, progress=None):\\n        self.ctr = [ctr, \\\'--address\\\', SOCKET, \\\'--namespace\\\', \\\'k8s.io\\\']\\n        self.cri, self.operation, self.directory, self.archive_fd, self.p = cri, operation, output_fd, archive_fd, prepared\\n        self.runner, self.clock, self.disk = runner or run_bounded, clock or time.monotonic, disk or self.local_disk\\n        self.progress = progress or (lambda phase: None)\\n        self.started = self.clock(); self.temp = temporary_tag(operation)\\n        self.report = {\\\'schema\\\': \\\'portal-ppt-host-delta-import.v1\\\', \\\'status\\\': \\\'INCOMPLETE\\\', \\\'operation\\\': operation,\\n            \\\'hostname\\\': HOST, \\\'temporary_tag\\\': self.temp, \\\'tag\\\': TAG, \\\'manifest_digest\\\': MANIFEST, \\\'config_digest\\\': CONFIG,\\n            \\\'bundle_sha256\\\': prepared[\\\'bundle_sha256\\\'], \\\'append_layer\\\': prepared[\\\'append_layer\\\'], \\\'commands\\\': [],\\n            \\\'storage_checks\\\': [], \\\'phase_a\\\': \\\'NOT_REQUESTED\\\', \\\'phase_b\\\': \\\'NOT_REQUESTED\\\', \\\'canonical_names_created\\\': [],\\n            \\\'image_import_requested\\\': False, \\\'image_import_may_be_partial\\\': False, \\\'automatic_retry\\\': False,\\n            \\\'production_workload_mutation\\\': False, \\\'database_access\\\': False, \\\'ssh\\\': False, \\\'image_execution\\\': False,\\n            \\\'image_deletion\\\': False, \\\'old_layer_bytes_read\\\': False, \\\'old_layer_hashes_verified_now\\\': False,\\n            \\\'old_layer_sizes_verified_now\\\': False, \\\'kubernetes_node_uid_freshly_checked\\\': False,\\n            \\\'pair_runtime_passed\\\': False, \\\'ready_for_rollout\\\': False}\\n        self.report[\\\'ordinary_aio_digest_alias_readiness\\\'] = \\\'NOT_COLLECTED_TAG_PRESERVATION_ONLY\\\'\\n\\n    def call(self, phase, args, mutates=False, timeout=15, limit=SMALL, is_cri=False, pass_fds=()):\\n        left = TOTAL_SECONDS - (self.clock() - self.started); require(left > 0, \\\'total_deadline\\\')\\n        argv = ([self.cri, \\\'--runtime-endpoint\\\', \\\'unix://\\\' + SOCKET, \\\'--image-endpoint\\\', \\\'unix://\\\' + SOCKET] if is_cri else self.ctr) + args\\n        row = {\\\'phase\\\': phase, \\\'argv_sha256\\\': sha(canonical(argv)), \\\'containerd_mutation_requested\\\': mutates}\\n        self.report[\\\'commands\\\'].append(row)\\n        out, err, result = self.runner(argv, timeout=min(timeout, left), limit=limit, pass_fds=pass_fds)\\n        row.update(result)\\n        # Raw config and runtime errors remain in the root-private control directory only.\\n        number = len(self.report[\\\'commands\\\'])\\n        for name, raw in [(\\\'stdout\\\', out), (\\\'stderr\\\', err)]:\\n            write_new(self.directory, \\\'command-%03d.%s.private.log\\\' % (number, name), raw)\\n        write_new(self.directory, \\\'command-%03d.json\\\' % number, canonical(row) + b\\\'\\\\n\\\')\\n        require(result.get(\\\'error_code\\\') is None and result.get(\\\'returncode\\\') == 0, result.get(\\\'error_code\\\') or \\\'command_failed\\\')\\n        return out\\n\\n    def target(self, name):\\n        require(name in (TAG, REPO + \\\'@\\\' + MANIFEST, OLD_TAG, AIO_TAG, self.temp), \\\'image_scope\\\')\\n        return image_target(self.call(\\\'image_reference\\\', [\\\'images\\\', \\\'ls\\\', \\\'name==\\\' + name]), name)\\n\\n    def preserved(self):\\n        require(self.target(OLD_TAG) == OLD_MANIFEST and self.target(AIO_TAG) == AIO_MANIFEST, \\\'preserved_image_changed\\\')\\n\\n    def labels(self, dg):\\n        return content_labels(self.call(\\\'content_gc_labels\\\', [\\\'content\\\', \\\'ls\\\', \\\'digest==\\\' + dg]), dg)\\n\\n    def closure(self, m, md):\\n        expected = {md, m[\\\'config\\\'][\\\'digest\\\']} | {d[\\\'digest\\\'] for d in m[\\\'layers\\\']}\\n        content_names(self.call(\\\'content_closure\\\', [\\\'content\\\', \\\'ls\\\', \\\'--quiet\\\'] + [\\\'digest==\\\' + d for d in sorted(expected)]), expected)\\n        verify_manifest_gc(self.labels(md), m)\\n\\n    def snapshots(self, chains):\\n        raw = self.call(\\\'snapshot_closure\\\', [\\\'snapshots\\\', \\\'--snapshotter\\\', \\\'overlayfs\\\', \\\'ls\\\'], limit=4 * SMALL)\\n        verify_snapshots(raw, chains)\\n\\n    def ready(self, name):\\n        raw = self.call(\\\'image_content_and_unpack\\\', [\\\'images\\\', \\\'check\\\', \\\'--quiet\\\', \\\'--snapshotter\\\', \\\'overlayfs\\\', \\\'name==\\\' + name])\\n        require(raw.decode(\\\'utf-8\\\').splitlines() == [name], \\\'image_check_not_ready\\\')\\n\\n    def local_disk(self):\\n        rows = []\\n        for path in (\\\'/var/lib\\\', IMAGEFS):\\n            require(os.path.realpath(path) == path and stat.S_ISDIR(os.lstat(path).st_mode), \\\'storage_path_changed\\\')\\n            v = os.statvfs(path); s = os.stat(path)\\n            # Small delta only: at most 12 MiB transport, 32 MiB unpack, logs; reserve is additional.\\n            needed = 256 * SMALL + 1024 * SMALL\\n            require(v.f_bavail * v.f_frsize >= needed and v.f_favail >= 4096, \\\'small_delta_storage_budget\\\')\\n            rows.append({\\\'path\\\': path, \\\'device\\\': s.st_dev, \\\'available_bytes\\\': v.f_bavail * v.f_frsize,\\n                         \\\'available_inodes\\\': v.f_favail, \\\'required_bytes\\\': needed})\\n        return rows\\n\\n    def checkpoint(self, name):\\n        write_new(self.directory, name + \\\'.json\\\', canonical(self.report) + b\\\'\\\\n\\\')\\n\\n    def import_phase(self, name, no_unpack):\\n        self.progress(\\\'PHASE_A_IMPORT\\\' if no_unpack else \\\'PHASE_B_UNPACK\\\')\\n        self.report[\\\'storage_checks\\\'].append({\\\'phase\\\': name, \\\'filesystems\\\': self.disk()})\\n        self.preserved()\\n        args = [\\\'images\\\', \\\'import\\\', \\\'--local\\\', \\\'--platform\\\', \\\'linux/arm64\\\', \\\'--snapshotter\\\', \\\'overlayfs\\\',\\n                \\\'--label\\\', \\\'io.cri-containerd.image=managed\\\']\\n        if no_unpack: args.append(\\\'--no-unpack\\\')\\n        args.append(\\\'/proc/self/fd/\\\' + str(self.archive_fd))\\n        self.report[name] = \\\'REQUESTED\\\'; self.report[\\\'image_import_requested\\\'] = True; self.report[\\\'image_import_may_be_partial\\\'] = True\\n        self.checkpoint(name + \\\'-REQUESTED\\\')\\n        os.lseek(self.archive_fd, 0, os.SEEK_SET)\\n        self.call(name, args, mutates=True, timeout=180, pass_fds=(self.archive_fd,))\\n        require(self.target(self.temp) == MANIFEST, \\\'temporary_target_changed\\\')\\n        self.report[name] = \\\'PASS\\\'; self.checkpoint(name + \\\'-PASS\\\')\\n\\n    def publish(self, name):\\n        current = self.target(name)\\n        require(current in (None, MANIFEST), \\\'canonical_target_conflict\\\')\\n        if current is None:\\n            # No force and explicit local: ImageService.Create is atomic and cannot overwrite a concurrent name.\\n            require(self.target(self.temp) == MANIFEST, \\\'temporary_target_changed\\\')\\n            self.call(\\\'publish_canonical_reference\\\', [\\\'images\\\', \\\'tag\\\', \\\'--local\\\', self.temp, name], mutates=True)\\n            self.report[\\\'canonical_names_created\\\'].append(name)\\n        require(self.target(name) == MANIFEST, \\\'canonical_target_changed\\\')\\n\\n    def execute(self):\\n        try:\\n            self.progress(\\\'BASE_PREFLIGHT\\\')\\n            self.report[\\\'storage_checks\\\'].append({\\\'phase\\\': \\\'BEFORE_IMPORT\\\', \\\'filesystems\\\': self.disk()})\\n            version = self.call(\\\'containerd_server_version\\\', [\\\'version\\\']).decode(\\\'utf-8\\\')\\n            require(re.search(r\\\'(?ms)^Server:\\\\s*\\\\n\\\\s*Version:\\\\s*v?2\\\\.3\\\\.3\\\\s*$\\\', version), \\\'containerd_server_version_mismatch\\\')\\n            self.preserved()\\n            for name in (TAG, REPO + \\\'@\\\' + MANIFEST):\\n                require(self.target(name) in (None, MANIFEST), \\\'canonical_target_conflict\\\')\\n            require(self.target(self.temp) is None, \\\'temporary_name_exists\\\')\\n            mr = self.call(\\\'old_manifest\\\', [\\\'content\\\', \\\'get\\\', OLD_MANIFEST])\\n            cr = self.call(\\\'old_config\\\', [\\\'content\\\', \\\'get\\\', OLD_CONFIG])\\n            old, old_config, chains = validate_metadata(mr, cr, OLD_MANIFEST, OLD_CONFIG, 128)\\n            require(old[\\\'layers\\\'] == self.p[\\\'manifest\\\'][\\\'layers\\\'][:128] and old_config[\\\'rootfs\\\'][\\\'diff_ids\\\'] == self.p[\\\'config\\\'][\\\'rootfs\\\'][\\\'diff_ids\\\'][:128], \\\'exact_old_new_prefix\\\')\\n            require(old_config.get(\\\'config\\\') == self.p[\\\'config\\\'].get(\\\'config\\\'), \\\'runtime_config_changed\\\')\\n            self.closure(old, OLD_MANIFEST); self.snapshots(chains); self.ready(OLD_TAG)\\n            self.report[\\\'base_preflight\\\'] = \\\'EXACT_METADATA_CONTENT_NAMES_AND_COMMITTED_CHAIN_PASS\\\'\\n            self.import_phase(\\\'phase_a\\\', True)\\n            self.closure(self.p[\\\'manifest\\\'], MANIFEST)\\n            # Read back the two small new metadata blobs; no existing layer bytes are read.\\n            validate_metadata(self.call(\\\'new_manifest_readback\\\', [\\\'content\\\', \\\'get\\\', MANIFEST]),\\n                              self.call(\\\'new_config_readback\\\', [\\\'content\\\', \\\'get\\\', CONFIG]), MANIFEST, CONFIG, 129)\\n            self.import_phase(\\\'phase_b\\\', False)\\n            self.closure(self.p[\\\'manifest\\\'], MANIFEST); self.snapshots(self.p[\\\'chains\\\']); self.ready(self.temp)\\n            require(self.labels(CONFIG).get(\\\'containerd.io/gc.ref.snapshot.overlayfs\\\') == self.p[\\\'chains\\\'][-1][\\\'chain_id\\\'], \\\'config_snapshot_gc_missing\\\')\\n            self.progress(\\\'PUBLISH_VERIFY\\\')\\n            for name in (TAG, REPO + \\\'@\\\' + MANIFEST): self.publish(name)\\n            for name in (TAG, REPO + \\\'@\\\' + MANIFEST):\\n                self.ready(name)\\n                obj = parse(self.call(\\\'cri_visibility\\\', [\\\'inspecti\\\', \\\'--output=json\\\', name], is_cri=True, limit=4 * SMALL))\\n                status = obj.get(\\\'status\\\', {})\\n                require(status.get(\\\'id\\\') == CONFIG and REPO + \\\'@\\\' + MANIFEST in status.get(\\\'repoDigests\\\', []) and TAG in status.get(\\\'repoTags\\\', []), \\\'cri_identity_or_references\\\')\\n            self.preserved(); self.snapshots(self.p[\\\'chains\\\'])\\n            for name in (TAG, REPO + \\\'@\\\' + MANIFEST): require(self.target(name) == MANIFEST, \\\'final_reference_changed\\\')\\n            self.report.update(status=\\\'COMPLETE_HOST_DELTA_PRELOAD_REVIEW_REQUIRED\\\', image_import_may_be_partial=False,\\n                content_and_unpack=\\\'PASS\\\', cri_visibility=\\\'PASS\\\', old_references_preserved=True,\\n                temporary_reference_retained=True, filesystem_capacity_reserved=False)\\n        except BaseException as exc:\\n            self.report[\\\'error_code\\\'] = exc.code if isinstance(exc, Failure) else (\\\'interrupted\\\' if isinstance(exc, KeyboardInterrupt) else \\\'local_controller_failure\\\')\\n        self.report[\\\'elapsed_seconds\\\'] = round(self.clock() - self.started, 3)\\n        return self.report\\n\\n\\ndef private_parent():\\n    fd = os.open(\\\'/\\\', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)\\n    try:\\n        for part in (\\\'var\\\', \\\'lib\\\'):\\n            s = os.fstat(fd); require(s.st_uid == 0 and not s.st_mode & 0o022, \\\'unsafe_control_parent\\\')\\n            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd); os.close(fd); fd = nxt\\n        s = os.fstat(fd); require(s.st_uid == 0 and not s.st_mode & 0o022, \\\'unsafe_control_parent\\\')\\n        return fd\\n    except BaseException: os.close(fd); raise\\n\\n\\ndef persist_report(directory, report):\\n    try:\\n        raw = canonical(report) + b\\\'\\\\n\\\'\\n        write_new(directory, \\\'REPORT.json\\\', raw)\\n        return {\\\'report_path\\\': CONTROL + \\\'/REPORT.json\\\', \\\'report_bytes\\\': len(raw), \\\'report_sha256\\\': sha(raw)}\\n    except Exception:\\n        report[\\\'report_write_failed\\\'] = True\\n        report[\\\'status\\\'] = \\\'INCOMPLETE\\\'\\n        report[\\\'error_code\\\'] = \\\'final_report_persistence_failed\\\'\\n\\n\\ndef main():\\n    parser = argparse.ArgumentParser(description=__doc__)\\n    parser.add_argument(\\\'--import-host-delta-once\\\', action=\\\'store_true\\\', required=True); parser.parse_args()\\n    report = {\\\'schema\\\': \\\'portal-ppt-host-delta-import.v1\\\', \\\'status\\\': \\\'INCOMPLETE\\\', \\\'automatic_retry\\\': False,\\n              \\\'image_import_requested\\\': False, \\\'ready_for_rollout\\\': False, \\\'control_directory\\\': CONTROL}\\n    parent = directory = archive = source = None\\n    try:\\n        require(os.name == \\\'posix\\\' and os.geteuid() == 0 and os.uname().nodename.split(\\\'.\\\')[0] == HOST and os.uname().machine == \\\'aarch64\\\', \\\'fixed_arm64_root_host_required\\\')\\n        ctr = shutil.which(\\\'ctr\\\', path=\\\'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\\\')\\n        cri = shutil.which(\\\'crictl\\\', path=\\\'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\\\')\\n        require(ctr is not None and cri is not None, \\\'required_tool_missing\\\')\\n        parent = private_parent(); name = CONTROL.rsplit(\\\'/\\\', 1)[1]\\n        os.mkdir(name, 0o700, dir_fd=parent); os.fsync(parent)\\n        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)\\n        operation = uuid.uuid4().hex\\n        write_new(directory, \\\'STARTED.json\\\', canonical({\\\'operation\\\': operation, \\\'host\\\': HOST, \\\'automatic_retry\\\': False}) + b\\\'\\\\n\\\')\\n        source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\\n        st = os.fstat(source); require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and 0 < st.st_size <= BUNDLE_MAX, \\\'input_type_or_size\\\')\\n        chunks, count = [], 0\\n        while True:\\n            part = os.read(source, 65536)\\n            if not part: break\\n            count += len(part); require(count <= BUNDLE_MAX, \\\'input_size_limit\\\'); chunks.append(part)\\n        require(os.fstat(source).st_size == st.st_size == count, \\\'input_size_changed\\\')\\n        prepared = unpack_handoff(b\\\'\\\'.join(chunks))\\n        raw = make_oci(prepared, operation); write_new(directory, \\\'candidate.delta.oci.tar\\\', raw)\\n        archive = os.open(\\\'candidate.delta.oci.tar\\\', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)\\n        actual = os.fstat(archive)\\n        require(stat.S_ISREG(actual.st_mode) and actual.st_uid == 0 and actual.st_size == len(raw), \\\'private_archive_binding\\\')\\n        report = Importer(ctr, cri, operation, directory, archive, prepared,\\n                          progress=lambda phase: print(json.dumps({\\\'phase\\\': phase}), file=sys.stderr, flush=True)).execute()\\n        report.update(control_directory=CONTROL, oci_transport={\\\'bytes\\\': len(raw), \\\'sha256\\\': sha(raw), \\\'complete_oci_closure_in_archive\\\': False})\\n    except BaseException as exc:\\n        report[\\\'error_code\\\'] = exc.code if isinstance(exc, Failure) else (\\\'once_marker_exists_review_required\\\' if isinstance(exc, FileExistsError) else \\\'local_controller_failure\\\')\\n    finally:\\n        if directory is not None:\\n            saved = persist_report(directory, report)\\n            if saved is not None: report.update(saved)\\n        for fd in (source, archive, directory, parent):\\n            if fd is not None: os.close(fd)\\n    print(json.dumps(report, sort_keys=True, indent=2), flush=True)\\n    return 0 if report[\\\'status\\\'] == \\\'COMPLETE_HOST_DELTA_PRELOAD_REVIEW_REQUIRED\\\' else 1\\n\\n\\nif __name__ == \\\'__main__\\\': sys.exit(main())\\n\'\n\nHELPER_PIN={\'bytes\': 29458, \'sha256\': \'4b07f6546729c29ed0eebf2db62f531c20b2747b4d6eb80ee0f9ff0c1c7e807f\'}\n\ndef canonical(v):return json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(\',\',\':\')).encode(\'ascii\')\n\ndef pin(b):return dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())\n\ndef binding(d):return dict(uid=d[\'metadata\'][\'uid\'],generation=d[\'metadata\'][\'generation\'],resource_version=d[\'metadata\'][\'resourceVersion\'],spec_sha256=pin(canonical(d[\'spec\']))[\'sha256\'])\n\ndef prepare_patch(d,original):\n    global SPEC\n    require(d[\'metadata\'][\'uid\']==original[\'metadata\'][\'uid\']==UID and d[\'spec\']==original[\'spec\'] and pin(canonical(original[\'spec\']))[\'sha256\']==OLD_SPEC,\'accepted_old_frontend_spec_changed\')\n    cs=d[\'spec\'][\'template\'][\'spec\'][\'containers\'];found=[i for i,c in enumerate(cs) if c[\'name\']==\'frontend\']\n    require(len(found)==1 and d[\'spec\'][\'replicas\']==2,\'two_frontend_replicas_required\');i=found[0]\n    require(cs[i][\'image\']==OLD and cs[i][\'imagePullPolicy\']==\'Never\',\'old_image_or_pull_policy_changed\')\n    desired=copy.deepcopy(d[\'spec\']);desired[\'template\'][\'spec\'][\'containers\'][i][\'image\']=NEW\n    SPEC=pin(canonical(desired))[\'sha256\']\n    return [dict(op=\'test\',path=\'/apiVersion\',value=\'apps/v1\'),dict(op=\'test\',path=\'/kind\',value=\'Deployment\')]+[\n        dict(op=\'test\',path=\'/metadata/\'+k,value=d[\'metadata\'][k]) for k in (\'uid\',\'resourceVersion\',\'generation\')]+[\n        dict(op=\'test\',path=\'/spec\',value=d[\'spec\']),dict(op=\'replace\',path=\'/spec/template/spec/containers/%d/image\'%i,value=NEW)]\n\ndef node_gate(rows):\n    require({n[\'metadata\'][\'name\']:n[\'metadata\'][\'uid\'] for n in rows}==NODES,\'node_uid_changed\')\n    for n in rows:\n        c={c[\'type\']:c[\'status\'] for c in n.get(\'status\',{}).get(\'conditions\',[])}\n        require(c.get(\'Ready\')==\'True\' and all(c.get(k)==\'False\' for k in (\'MemoryPressure\',\'DiskPressure\',\'PIDPressure\')),\'frontend_node_unhealthy\')\n        require(n[\'status\'][\'nodeInfo\'][\'architecture\']==\'arm64\',\'frontend_node_platform_changed\')\n        names={name for i in n[\'status\'].get(\'images\',[]) for name in i.get(\'names\',[])}\n        require(NEW in names or TAG in names,\'new_image_not_observed_in_kubelet_cache\')\n    return [dict(name=n,uid=u) for n,u in sorted(NODES.items())]\n\ndef controller(x,uid):return any(o.get(\'uid\')==uid and o.get(\'controller\') is True for o in x[\'metadata\'].get(\'ownerReferences\',[]))\n\ndef ready(d,inventory,service,slices):\n    require(d[\'metadata\'][\'uid\']==UID and pin(canonical(d[\'spec\']))[\'sha256\']==SPEC,\'frontend_spec_changed\')\n    s=d.get(\'status\',{})\n    if s.get(\'observedGeneration\',0)<d[\'metadata\'][\'generation\'] or any(s.get(k)!=2 for k in (\'replicas\',\'updatedReplicas\',\'readyReplicas\',\'availableReplicas\')) or s.get(\'unavailableReplicas\',0) or s.get(\'terminatingReplicas\',0):return None\n    owned={r[\'metadata\'][\'uid\'] for r in inventory if r.get(\'kind\')==\'ReplicaSet\' and controller(r,UID)}\n    current={r[\'metadata\'][\'uid\'] for r in inventory if r.get(\'kind\')==\'ReplicaSet\' and controller(r,UID) and r[\'spec\'][\'template\'][\'spec\']==d[\'spec\'][\'template\'][\'spec\']}\n    pods=[p for p in inventory if p.get(\'kind\')==\'Pod\' and any(controller(p,r) for r in owned) and p.get(\'status\',{}).get(\'phase\') not in (\'Failed\',\'Succeeded\') and not p[\'metadata\'].get(\'deletionTimestamp\')]\n    if len(pods)!=2 or any(not any(controller(p,r) for r in current) for p in pods):return None\n    if {p[\'spec\'].get(\'nodeName\') for p in pods}!=set(NODES):return None\n    normal=normal_inventory(types.SimpleNamespace(require=require),d,pods)\n    for p in normal:\n        s=p.get(\'status\',{});cs=s.get(\'containerStatuses\',[])\n        if s.get(\'phase\')!=\'Running\' or not any(c.get(\'type\')==\'Ready\' and c.get(\'status\')==\'True\' for c in s.get(\'conditions\',[])):return None\n        states=[c for c in cs if c.get(\'name\')==\'frontend\']\n        if len(states)!=1 or states[0].get(\'ready\') is not True:return None\n        require(states[0].get(\'imageID\',\'\').rsplit(\'@\',1)[-1] in (MANIFEST,CONFIG),\'frontend_runtime_digest_unbound\')\n        require(states[0].get(\'containerID\',\'\').startswith(\'containerd://\'),\'frontend_container_id_missing\')\n    require(service[\'metadata\'][\'name\']==\'frontend\' and service[\'metadata\'][\'namespace\']==\'deer-flow\' and service[\'spec\'][\'selector\'].get(\'app\')==\'frontend\',\'service_identity\')\n    pmap={p[\'metadata\'][\'uid\']:p for p in pods};observed=set()\n    for sl in slices:\n        require(sl[\'metadata\'].get(\'labels\',{}).get(\'kubernetes.io/service-name\')==\'frontend\' and any(o.get(\'kind\')==\'Service\' and o.get(\'uid\')==service[\'metadata\'][\'uid\'] for o in sl[\'metadata\'].get(\'ownerReferences\',[])),\'endpoint_service_identity\')\n        for ep in sl.get(\'endpoints\',[]):\n            if ep.get(\'conditions\',{}).get(\'ready\') is not True:continue\n            ref=ep.get(\'targetRef\',{});p=pmap.get(ref.get(\'uid\'))\n            require(p is not None and ref.get(\'kind\')==\'Pod\' and ref.get(\'namespace\')==\'deer-flow\' and ref.get(\'name\')==p[\'metadata\'][\'name\'] and ep.get(\'conditions\',{}).get(\'terminating\') is not True,\'unexpected_ready_endpoint\')\n            require(ep.get(\'addresses\') and set(ep[\'addresses\'])<={x[\'ip\'] for x in p[\'status\'].get(\'podIPs\',[])},\'endpoint_address_changed\')\n            observed.add(ref[\'uid\'])\n    return pods if observed==set(pmap) else None\n\ndef normal_inventory(e, d, items):\n    """Permit only Kubernetes\' verified read-only service-account projection."""\n    result = copy.deepcopy(items)\n    templates = {c[\'name\']: c for c in d[\'spec\'][\'template\'][\'spec\'][\'containers\']}\n    for p in result:\n        if p.get(\'kind\') != \'Pod\' or p.get(\'status\', {}).get(\'phase\') in (\'Failed\', \'Succeeded\'):\n            continue\n        spec = p[\'spec\']\n        volumes = {v[\'name\']: v for v in spec.get(\'volumes\', [])}\n        for c in spec[\'containers\']:\n            e.require(c[\'name\'] in templates, \'unexpected_pod_container\')\n            expected = templates[c[\'name\']].get(\'volumeMounts\', [])\n            kept = []\n            for m in c.get(\'volumeMounts\', []):\n                if m in expected:\n                    kept.append(m)\n                    continue\n                v = volumes.get(m.get(\'name\'), {})\n                sources = v.get(\'projected\', {}).get(\'sources\', [])\n                e.require(set(m) == {\'name\', \'mountPath\', \'readOnly\'} and m.get(\'readOnly\') is True\n                    and m.get(\'mountPath\') == \'/var/run/secrets/kubernetes.io/serviceaccount\'\n                    and re.fullmatch(\'kube-api-access-[a-z0-9]+\', m.get(\'name\', \'\'))\n                    and set(v) == {\'name\', \'projected\'} and sources\n                    and any(set(s) == {\'serviceAccountToken\'} for s in sources)\n                    and all(set(s) in ({\'serviceAccountToken\'}, {\'configMap\'}, {\'downwardAPI\'}) for s in sources),\n                    \'unexpected_extra_pod_mount\')\n            if \'volumeMounts\' in c:\n                if \'volumeMounts\' not in templates[c[\'name\']] and not kept:\n                    del c[\'volumeMounts\']\n                else:\n                    c[\'volumeMounts\'] = kept\n            e.require(c == templates[c[\'name\']], \'non_projection_container_drift\')\n    return result\n\nclass Execution:\n    def __init__(self, helper, directory, kubectl, report):\n        self.H, self.directory, self.kubectl, self.report = helper, directory, kubectl, report\n        self.started = self.progress = time.monotonic()\n        self.captured = 0\n\n    def write(self, name, value):\n        self.H.write_new(self.directory, name, canonical(value)+b\'\\n\')\n\n    def call(self, phase, args, pass_fds=(), must_succeed=True):\n        left = SECONDS-(time.monotonic()-self.started)\n        require(left > 0 and len(self.report[\'commands\']) < 700, \'frontend_observation_deadline\')\n        out, err, receipt = self.H.run_bounded([self.kubectl]+BASE+args,\n            timeout=min(25, left), limit=2097152, pass_fds=pass_fds)\n        n = len(self.report[\'commands\'])+1\n        receipt.update(ordinal=n, phase=phase)\n        self.report[\'commands\'].append(receipt)\n        self.captured += len(out)+len(err)\n        require(self.captured <= 64*1024**2, \'private_capture_budget\')\n        self.H.write_new(self.directory, \'%04d.stdout.private.log\' % n, out)\n        self.H.write_new(self.directory, \'%04d.stderr.private.log\' % n, err)\n        if time.monotonic()-self.progress >= 20:\n            print(json.dumps({\'phase\': \'FRONTEND_UI_CUTOVER_OBSERVATION\', \'commands\': n,\n                \'elapsed_seconds\': round(time.monotonic()-self.started, 3)}), flush=True)\n            self.progress = time.monotonic()\n        if must_succeed:\n            require(receipt.get(\'returncode\') == 0 and receipt.get(\'error_code\') is None, \'bounded_command_failed\')\n        return out, receipt\n\n    def get(self, phase, args):\n        return json.loads(self.call(phase, [\'get\']+args+[\'-o\', \'json\'])[0])\n\n    def patch_once(self, patch):\n        require(self.report[\'patch_request_count\'] == 0, \'second_patch_forbidden\')\n        self.write(\'CUTOVER.private.patch.json\', patch)\n        fd = os.open(\'CUTOVER.private.patch.json\', os.O_RDONLY|os.O_NOFOLLOW, dir_fd=self.directory)\n        try:\n            self.report.update(patch_requested=True, patch_request_count=1, patch_may_have_applied=True)\n            self.call(\'single-guarded-patch\', [\'patch\', \'deployment\', \'frontend\', \'--type=json\',\n                \'--patch-file=/proc/self/fd/\'+str(fd), \'-o\', \'json\'], pass_fds=(fd,), must_succeed=False)\n        finally:\n            os.close(fd)\n        # A timeout/nonzero response never permits a second patch. Read back.\n        d = self.get(\'readback-after-single-patch\', [\'deployment\', \'frontend\'])\n        require(d[\'metadata\'][\'uid\'] == UID and pin(canonical(d[\'spec\']))[\'sha256\'] == SPEC,\n            \'patch_result_requires_manual_review_no_retry\')\n        return d\n'
PRELOADS = {'datax-02': {'automatic_retry': False, 'canonical_names_created': ['deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1', 'deerflow.local/deer-flow-frontend@sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'], 'config_digest': 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821', 'content_and_unpack': None, 'cri_visibility': None, 'elapsed_seconds': 58.365, 'error_class': 'Refused', 'error_code': 'command_limit', 'exitcode': 1, 'expected_node_uid': '3148d4c1-6a97-42ed-a78c-950dcea5cbec', 'failure_phase': 'IMPORT_AND_UNPACK', 'hostname': 'datax-02', 'image_import_may_be_partial': True, 'image_import_requested': True, 'kubernetes_node_uid_freshly_checked': False, 'manifest_digest': 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3', 'mutation_once_claimed': True, 'observed_layout': 'DATAX02_ROOT_IMAGEFS', 'old_references_preserved': None, 'operation': '281ac71347fa44a59ef78d56c690c8f3', 'os_errno': None, 'phase_a': 'PASS', 'phase_b': 'PASS', 'preclaim_passed': True, 'preflight_retry_available': False, 'production_workload_mutation': False, 'ready_for_rollout': False, 'report_bytes': 115986, 'report_path': '/var/lib/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json', 'report_sha256': 'b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120', 'schema': 'portal-frontend-ui-cache-preload.v1', 'status': 'INCOMPLETE'}, 'ecs-zdz-pro-0015': {'automatic_retry': False, 'canonical_names_created': ['deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1', 'deerflow.local/deer-flow-frontend@sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'], 'config_digest': 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821', 'content_and_unpack': 'PASS', 'cri_visibility': 'PASS', 'elapsed_seconds': 15.132, 'error_code': None, 'exitcode': 0, 'expected_node_uid': 'de6ee5e8-dbfb-4272-9ebf-ae82182db085', 'hostname': 'ecs-zdz-pro-0015', 'image_import_may_be_partial': False, 'image_import_requested': True, 'kubernetes_node_uid_freshly_checked': False, 'manifest_digest': 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3', 'mutation_once_claimed': True, 'old_references_preserved': True, 'operation': 'f7ea0c04a2ce4d03a1de272c7578518f', 'phase_a': 'PASS', 'phase_b': 'PASS', 'preclaim_passed': True, 'preflight_retry_available': False, 'production_workload_mutation': False, 'ready_for_rollout': False, 'report_bytes': 118975, 'report_path': '/mnt/data/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json', 'report_sha256': '4442dbd901bbb96e19dd22c8011aac8407ab318dcf9fc4f1168af765fb5eb51e', 'schema': 'portal-frontend-ui-cache-preload.v1', 'status': 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED'}}
PRIOR_RECEIPT = {'scope': 'operator_observed_deployment_identity', 'frontend': {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 241, 'spec_sha256': 'f48dcd7f2a54bc8867fcfa77f84682c22d3e6993fd0091779d1e639f8fee313a'}, 'gateway': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 408, 'spec_sha256': '05691890fb9a36e8075fe947d5214501f3ac10f86b35188bbb3a8d48d4ef630e'}}
SOURCE_PINS = {'/app/frontend/.next/BUILD_ID': {'bytes': 21, 'sha256': 'bcc3068d24025fa6c47b5570094507fc5d58c0a0048ce9674d28234ae2d5eb6b'}, '/app/frontend/.next/routes-manifest.json': {'bytes': 10355, 'sha256': '7d59bf4ddab10c4e621624635239ad3b105524b376d8aa4c7a88d1f37aac6d9e'}, '/app/frontend/.next/static/chunks/0_d5_gp7ib4x4.js': {'bytes': 352346, 'sha256': 'ebcd9a51c6c610f6bfc1a19a2681f09a5ffb9dc7d4455f3e72f0921b1e2345ad'}, '/app/frontend/.next/static/chunks/19t6w5y4h_fyb.js': {'bytes': 92784, 'sha256': 'e0b6526cb0634eefd8bb384d87e6c26b1103ebd1b517be3884ee90219293ffbb'}, '/app/frontend/.next/static/chunks/1b_x9_dkmteil.js': {'bytes': 16611, 'sha256': '4ac3a43d12db09b0ccc95af08cf953d3a4511b4e411de3460d8e0cd8429e030c'}, '/app/frontend/.next/static/chunks/1r54373p94ucy.js': {'bytes': 123189, 'sha256': '411aff51208a8fcd1a6d83401e1e868bd4a44ac6e2c524c40ccc6abe58d17a15'}, '/app/frontend/.next/static/chunks/3lo0kxcubwj1x.js': {'bytes': 38221, 'sha256': 'e4c08f994b617a920eda43d763587e471b8352869d0c6d3da3cf8b5f41eb7c55'}, '/app/frontend/.next/static/chunks/3non8_6sa2786.css': {'bytes': 135068, 'sha256': '69038567b6ffb8fc9f0ba9f15317136f999c73ec2e8372c5113ae599cc584225'}, '/app/frontend/.next/static/chunks/3pw5sf112gbc-.js': {'bytes': 43882, 'sha256': 'cbbd6d906244cca3250b5710bdba1618898cd9d42964c08a49c1bac7c746f292'}, '/app/frontend/.next/static/chunks/43xxd4nu1phu0.js': {'bytes': 85867, 'sha256': 'd72a907e916ebbdc28a5ef5dd50e967ce232c37bc37899e2e6f5f3084ef92027'}, '/app/frontend/.next/static/chunks/44s_-_kix5hr_.js': {'bytes': 35548, 'sha256': 'abb4a5fe8cace36127a9f7557e89c43b693d7e64030f1ad864483fb91afbab8e'}, '/app/frontend/public/images/cnooc-workspace-concept-bg.png': {'bytes': 1254279, 'sha256': '5b00ca5a43fe16fa47dbcd15649f2ada852f383155ebe801caacbba1c64b518d'}}
PROVENANCE = {'template': {'bytes': 91617, 'sha256': 'c7837771aa6259f39642777d733a14befcb7eebabf49fa03163b97ed6a23d11b'}, 'package': {'bytes': 1951, 'sha256': '2063e6ccc328a0912fd88900cab9622921d65a5f5b6b5d910c02cfbdeee458d3'}, 'result': {'bytes': 2200, 'sha256': '344494a8a1b9513a5816266a070dfea536523c5ed42688b8c16cdc9324849aa8'}, 'assets': {'bytes': 9519, 'sha256': '8fd89633d124f2972e2f7571e8602fefa2b85f956501973f70ad7db21f0bc1c0'}, 'browser_summary': {'bytes': 679, 'sha256': '673e2c4d0a5ee4fbe96b81ea7e1ed8eda6faeb217feb5406c0d12b2b0d04f0f3'}, 'source': {'bytes': 171511, 'sha256': '6af8cd3cb41f08dda3719d73076f160f6c9715d66a72c83c778a88272aea1b3a'}, 'operator_cache_field': {'bytes': 5843, 'sha256': '46eee378d71bcc44aaf8d71b67e71582a60f9a1143d1aa6c7451440d8b9b449c'}, 'fresh_operator_baseline': {'bytes': 3269, 'sha256': '32ceb17d33f0eb301ea82f1e0fabe3e3be875d4cdc798eb7e33bf7f50da2131d'}, 'baseline_scope': 'fresh_readonly_deployment_identity', 'preload_evidence_scope': 'operator_supplied_summaries_with_exact_private_report_pins', 'browser_evidence_scope': 'archived_local_summary_not_structured_browser_report', 'nginx_configuration_semantics_verified': False, 'skillhub_native_auth_remediation_included': False}
FAILED_R2 = None

FRONT = {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 241, 'spec_sha256': 'f48dcd7f2a54bc8867fcfa77f84682c22d3e6993fd0091779d1e639f8fee313a'}
MANIFEST = 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'
CONFIG = 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821'
OLD = 'deerflow.local/deer-flow-frontend@sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d'
NEW = 'deerflow.local/deer-flow-frontend@sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'
TAG = 'deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1'
NODES = {'datax-02': '3148d4c1-6a97-42ed-a78c-950dcea5cbec',
         'ecs-zdz-pro-0015': 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'}
ONCE = 'portal-frontend-sidechat-security-activation-20261106-r1.once'
ACCEPTED = 'FRONTEND_SIDECHAT_SECURITY_RUNTIME_ACCEPTED_BROWSER_PENDING'
SECONDS = 1800
ROLES = ['frontend', 'gateway', 'ppt-master-runner', 'provisioner', 'nginx', 'nginx-security-heai-20261012-r1']
PRELOAD_PATHS = {'datax-02': '/var/lib/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json', 'ecs-zdz-pro-0015': '/mnt/data/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json'}


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def binding(d):
    return {'uid': d['metadata']['uid'], 'generation': d['metadata']['generation'],
            'spec_sha256': pin(canonical(d['spec']))['sha256']}




def validate_preloads(rows, rechecks=None):
    if rechecks is None:
        rechecks = RECHECKS
    require(type(rows) is dict and set(rows) == set(NODES)
        and type(rechecks) is dict and set(rechecks) == {'datax-02'}, 'actual_cache_receipts_required')
    for host, receipt in rows.items():
        expected = PRELOAD_PINS[host]
        require(receipt.get('schema') == 'portal-frontend-ui-cache-preload.v1'
            and receipt.get('hostname') == host and receipt.get('expected_node_uid') == NODES[host]
            and receipt.get('operation') == expected['operation']
            and receipt.get('report_path') == PRELOAD_PATHS[host]
            and receipt.get('report_bytes') == expected['bytes']
            and receipt.get('report_sha256') == expected['sha256'], 'preload_identity_or_status')
        require(receipt.get('manifest_digest') == MANIFEST and receipt.get('config_digest') == CONFIG
            and receipt.get('preclaim_passed') is True and receipt.get('mutation_once_claimed') is True
            and receipt.get('image_import_requested') is True
            and receipt.get('phase_a') == receipt.get('phase_b') == 'PASS'
            and receipt.get('production_workload_mutation') is False
            and set(receipt.get('canonical_names_created', [])) == {NEW, TAG}, 'preload_image_or_preservation')
    success = rows['ecs-zdz-pro-0015']
    require(success.get('status') == 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED'
        and success.get('exitcode') == 0 and success.get('error_code') is None
        and success.get('content_and_unpack') == success.get('cri_visibility') == 'PASS'
        and success.get('old_references_preserved') is True
        and success.get('image_import_may_be_partial') is False, '0015_preload_not_accepted')
    failed = rows['datax-02']
    require(failed.get('status') == 'INCOMPLETE' and failed.get('exitcode') == 1
        and failed.get('error_code') == 'command_limit' and failed.get('error_class') == 'Refused'
        and failed.get('failure_phase') == 'IMPORT_AND_UNPACK'
        and failed.get('image_import_may_be_partial') is True
        and all(failed.get(k) is None for k in ('content_and_unpack', 'cri_visibility', 'old_references_preserved')),
        'original_datax02_failure_must_be_preserved')
    receipt = rechecks['datax-02']
    require(receipt.get('schema') == 'portal-frontend-sidechat-cache-recheck.v1'
        and receipt.get('status') == 'FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED'
        and receipt.get('hostname') == 'datax-02' and receipt.get('expected_node_uid') == NODES['datax-02']
        and receipt.get('operation') == RECHECK_PIN['operation']
        and receipt.get('report_path') == RECHECK_PATH and receipt.get('report_bytes') == RECHECK_PIN['bytes']
        and receipt.get('report_sha256') == RECHECK_PIN['sha256']
        and receipt.get('exitcode') == 0 and receipt.get('manifest_digest') == MANIFEST
        and receipt.get('config_digest') == CONFIG, 'datax02_recheck_identity_or_status')
    require(receipt.get('queries') == 80 and receipt.get('content_and_unpack') == receipt.get('cri_visibility') == 'PASS'
        and receipt.get('protected_reference_count') == 31 and receipt.get('snapshot_chains_verified') == 11
        and receipt.get('content_blobs_present') == 13 and receipt.get('old_references_preserved') is True
        and receipt.get('temporary_reference_preserved') is True and receipt.get('stable_readback') is True
        and receipt.get('report_directory_created') is True and receipt.get('last_step') == 'stable_prior_receipt',
        'datax02_cache_not_accepted')
    require(all(receipt.get(k) is False for k in ('automatic_retry', 'image_import_requested',
        'image_retag_requested', 'image_deleted', 'production_workload_mutation', 'configuration_modified',
        'database_access', 'original_receipt_modified', 'ready_for_rollout', 'kubernetes_node_uid_freshly_checked',
        'layer_payloads_rehashed_now', 'storage_capacity_rechecked'))
        and receipt.get('provider_requests') == 0 and receipt.get('original_marker_preserved') is True,
        'datax02_recheck_scope_changed')
    prior = receipt.get('prior_receipt', {})
    expected = PRELOAD_PINS['datax-02']
    require(prior.get('path') == PRELOAD_PATHS['datax-02'] and prior.get('bytes') == expected['bytes']
        and prior.get('sha256') == expected['sha256'] and prior.get('operation') == expected['operation']
        and prior.get('status') == 'INCOMPLETE' and prior.get('error_code') == 'command_limit'
        and prior.get('commands') == 200 and prior.get('verified_stop') == 'final_reference_readback',
        'datax02_failed_receipt_binding_changed')





def load_modules():
    require(FRONT_COMMON and SOURCE_PINS and PROVENANCE, 'package_incomplete')
    front = types.ModuleType('_proven_frontend_activation')
    exec(compile(FRONT_COMMON, '<proven-frontend-helper>', 'exec'), front.__dict__)
    front.require = require
    front.UID, front.OLD, front.OLD_SPEC = FRONT['uid'], OLD, FRONT['spec_sha256']
    front.MANIFEST, front.CONFIG, front.NEW, front.TAG, front.NODES = MANIFEST, CONFIG, NEW, TAG, NODES
    front.SPEC = None
    front.node_gate = node_gate
    require(pin(front.HELPER) == front.HELPER_PIN, 'embedded_bounded_helper_changed')
    helper = types.ModuleType('_bounded_helper')
    exec(compile(front.HELPER, '<bounded-helper>', 'exec'), helper.__dict__)
    return front, helper


def node_gate(rows):
    require({n['metadata']['name']: n['metadata']['uid'] for n in rows} == NODES, 'node_uid_changed')
    observed = []
    for n in rows:
        c = {c['type']: c['status'] for c in n.get('status', {}).get('conditions', [])}
        require(c.get('Ready') == 'True' and all(c.get(k) == 'False' for k in
                ('MemoryPressure', 'DiskPressure', 'PIDPressure')), 'frontend_node_unhealthy')
        require(n['status']['nodeInfo']['architecture'] == 'arm64', 'frontend_node_platform_changed')
        require(not n.get('spec', {}).get('unschedulable', False) and not any(t.get('effect') == 'NoExecute'
                for t in n.get('spec', {}).get('taints', [])), 'frontend_node_unavailable')
        images = n['status'].get('images', [])
        names = {name for image in images for name in image.get('names', [])}
        # NodeStatus.Images is capped and is not the CRI's complete image inventory.
        # Fresh imagePullPolicy=Never Jobs below supply the positive execution evidence.
        observed.append({'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                         'reported_images': len(images), 'candidate_in_node_status': NEW in names or TAG in names})
    return sorted(observed, key=lambda row: row['name'])























def make_execution(front):
    class Execution(front.Execution):
        def call(self, phase, args, pass_fds=(), must_succeed=True):
            require(args and args[0] in ('get', 'exec', 'patch', 'create', 'logs'), 'command_forbidden')
            if args[0] == 'create':
                require(getattr(self, 'approved_check', None) is not None and len(pass_fds) == 1
                        and args == ['create', '-f', '/proc/self/fd/%d' % pass_fds[0], '-o', 'json'],
                        'check_creation_scope_forbidden')
                approved = self.approved_check
                require(not approved.get('dispatched') and self.report.get('once_claimed') is True
                        and pin(os.pread(pass_fds[0], 65537, 0)) == approved['pin'], 'check_creation_binding')
                approved['dispatched'] = True
            if args[0] == 'logs':
                require(getattr(self, 'approved_check_logs', None) == args, 'check_logs_scope_forbidden')
            if args[0] == 'patch':
                require(self.report.get('once_claimed') is True and self.report['patch_request_count'] == 1
                        and args[:4] == ['patch', 'deployment', 'frontend', '--type=json']
                        and len(args) == 7 and args[4].startswith('--patch-file=/proc/self/fd/')
                        and args[5:] == ['-o', 'json'] and len(pass_fds) == 1,
                        'patch_scope_forbidden')
                require(not self.report.get('patch_dispatched'), 'second_patch_forbidden')
                self.report['patch_dispatched'] = True
            left = SECONDS - (time.monotonic() - self.started)
            require(left > 0 and len(self.report['commands']) < 700, 'activation_deadline')
            timeout = 35 if phase.startswith('backend-contract-') else (180 if args[0] == 'exec' else 15)
            base = [self.kubectl, '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=%ds' % timeout, '-n', 'deer-flow']
            out, err, result = self.H.run_bounded(base + args, timeout=min(timeout + 25, left),
                limit=4 * 1024**2, pass_fds=pass_fds)
            n = len(self.report['commands']) + 1
            result.update(ordinal=n, phase=phase)
            self.report['commands'].append(result)
            self.captured += len(out) + len(err)
            require(self.captured <= 64 * 1024**2, 'private_capture_budget')
            self.H.write_new(self.directory, '%04d.stdout.private.log' % n, out)
            self.H.write_new(self.directory, '%04d.stderr.private.log' % n, err)
            if time.monotonic() - self.progress >= 20:
                print(json.dumps({'phase': phase, 'commands': n}), flush=True)
                self.progress = time.monotonic()
            if must_succeed:
                require(result.get('returncode') == 0 and result.get('error_code') is None, 'bounded_command_failed')
            return out, result

        def patch_once(self, patch):
            require(self.report['patch_request_count'] == 0 and self.report.get('once_claimed') is True, 'second_patch_forbidden')
            # Durable dispatch intent survives a lost CLI response or process death.
            self.write('DISPATCH.json', {'operation': self.report['operation'], 'patch_request_count': 1,
                'patch_may_have_applied': True, 'automatic_retry': False, 'patch_sha256': pin(canonical(patch))['sha256']})
            return super().patch_once(patch)

        def create_check_once(self, spec):
            name = spec['metadata']['name']
            raw = canonical(spec) + b'\n'
            require(len(raw) <= 65536, 'check_manifest_limit')
            self.write(name + '.json', spec)
            fd = os.open(name + '.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
            try:
                # The inherited writer serializes canonical JSON plus one newline.
                require(pin(os.pread(fd, 65537, 0)) == pin(raw), 'check_manifest_write_changed')
                self.approved_check = {'pin': pin(raw), 'dispatched': False}
                self.write(name + '.DISPATCH.json', {'name': name, 'creation_may_have_applied': True,
                           'manifest': pin(raw), 'automatic_retry': False})
                self.report['temporary_jobs_created'].append({'name': name, 'creation_requested': True})
                output, result = self.call('check-create', ['create', '-f', '/proc/self/fd/%d' % fd, '-o', 'json'],
                                           pass_fds=(fd,), must_succeed=False)
                require(result.get('returncode') == 0 and result.get('error_code') is None,
                        'check_create_outcome_requires_review')
                return self.H.parse(output)
            finally:
                self.approved_check = None
                os.close(fd)

    return Execution


def cache_probe_code():
    return """const fs=require('fs'),crypto=require('crypto');
const pins=JSON.parse(process.argv[1]);
try {for(const [p,v] of Object.entries(pins)) {
 if(fs.realpathSync(p)!==p||v.bytes>2097152)throw Error('source');
 const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try {const s=fs.fstatSync(fd);if(!s.isFile()||s.size!==v.bytes)throw Error('source');
 const b=fs.readFileSync(fd),t=fs.fstatSync(fd);
 if(s.dev!==t.dev||s.ino!==t.ino||s.size!==t.size||s.mtimeMs!==t.mtimeMs||s.ctimeMs!==t.ctimeMs||crypto.createHash('sha256').update(b).digest('hex')!==v.sha256)throw Error('source');
 }finally{fs.closeSync(fd)}
} console.log(JSON.stringify({status:'LOCAL_PRELOADED_IMAGE_EXECUTED',sources_verified:true,files_verified:Object.keys(pins).length,provider_requests:0}));
}catch(e){console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1)}"""


def check_spec(host, operation, ordinal):
    require(host in NODES and re.fullmatch('[a-f0-9]{32}', operation), 'check_identity')
    name = 'portal-sidechat-security-cache-' + operation[:16] + '-' + str(ordinal)
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'deer-flow'},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 180, 'ttlSecondsAfterFinished': 3600,
                     'template': {'metadata': {'labels': {'portal-sidechat-security-cache-check': operation}}, 'spec': {
                         'nodeName': host, 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                         'enableServiceLinks': False, 'terminationGracePeriodSeconds': 5,
                         'containers': [{'name': 'check', 'image': NEW, 'imagePullPolicy': 'Never',
                             'command': ['node'], 'args': ['-e', cache_probe_code(), json.dumps(SOURCE_PINS)],
                             'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                           'limits': {'cpu': '500m', 'memory': '128Mi'}},
                             'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'runAsNonRoot': True,
                                 'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}}}]}}}}


def check_pod(pod, job, spec):
    require(pod.get('metadata', {}).get('namespace') == 'deer-flow' and any(o.get('uid') == job['metadata']['uid']
            and o.get('controller') is True and o.get('kind') == 'Job'
            for o in pod['metadata'].get('ownerReferences', [])), 'check_pod_owner')
    s = pod['spec']
    expected = spec['spec']['template']['spec']
    require(s.get('nodeName') == expected['nodeName'] and s.get('automountServiceAccountToken') is False
            and not s.get('hostNetwork', False) and not s.get('hostPID', False) and not s.get('hostIPC', False)
            and not s.get('volumes') and not s.get('initContainers') and not s.get('ephemeralContainers')
            and len(s.get('containers', [])) == 1, 'check_pod_scope')
    c = s['containers'][0]
    require(all(c.get(k) == expected['containers'][0][k] for k in ('name', 'image', 'imagePullPolicy', 'command', 'args'))
            and not c.get('envFrom') and not c.get('env') and not c.get('volumeMounts')
            and not c.get('ports') and c.get('securityContext') == expected['containers'][0]['securityContext'],
            'check_container_changed')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    return {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': s['nodeName'],
            'phase': pod.get('status', {}).get('phase'), 'containers': [
                {'imageID': c.get('imageID'), 'restart_count': c.get('restartCount'),
                 'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason'),
                 'exit_code': c.get('state', {}).get('terminated', {}).get('exitCode')}
                for c in statuses]}


def verify_local_cache(e, sleep=time.sleep):
    checks = e.report.setdefault('cache_checks', [])
    for ordinal, host in enumerate(sorted(NODES)):
        spec = check_spec(host, e.report['operation'], ordinal)
        name = spec['metadata']['name']
        print(json.dumps({'phase': 'VERIFY_LOCAL_PRELOADED_IMAGE', 'node': host, 'job': name}), flush=True)
        job = e.create_check_once(spec)
        require(job.get('kind') == 'Job' and job.get('metadata', {}).get('name') == name
                and job['metadata'].get('namespace') == 'deer-flow' and job['metadata'].get('uid'), 'created_check_identity')
        row = {'node': host, 'node_uid': NODES[host], 'job': name, 'job_uid': job['metadata']['uid'], 'status': 'INCOMPLETE'}
        checks.append(row)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            current = e.get('check-observe-job', ['job', name])
            require(current['metadata']['uid'] == job['metadata']['uid'], 'check_job_replaced')
            pods = e.get('check-observe-pod', ['pods', '-l', 'batch.kubernetes.io/controller-uid=' + job['metadata']['uid']])['items']
            require(len(pods) <= 1, 'check_pod_count')
            observed = [check_pod(p, job, spec) for p in pods]
            row['pods'] = observed
            if observed:
                require(not any(c['waiting_reason'] in ('ErrImageNeverPull', 'ErrImagePull', 'ImagePullBackOff',
                            'InvalidImageName', 'CreateContainerConfigError', 'CreateContainerError')
                            for c in observed[0]['containers']), 'local_image_start_failed')
            conditions = current.get('status', {}).get('conditions', [])
            require(not any(c.get('type') == 'Failed' and c.get('status') == 'True' for c in conditions)
                    and not any(p['phase'] == 'Failed' for p in observed), 'local_image_check_failed')
            if any(c.get('type') == 'Complete' and c.get('status') == 'True' for c in conditions):
                require(len(observed) == 1 and observed[0]['phase'] == 'Succeeded'
                        and len(observed[0]['containers']) == 1, 'check_completion_missing')
                c = observed[0]['containers'][0]
                digest = (c.get('imageID') or '').rsplit('@', 1)[-1]
                if digest.startswith('containerd://'):
                    digest = digest[len('containerd://'):]
                require(c['exit_code'] == 0 and c['restart_count'] == 0 and digest in (MANIFEST, CONFIG), 'check_image_identity')
                args = ['logs', observed[0]['pod'], '-c', 'check', '--limit-bytes=16384']
                e.approved_check_logs = args
                try:
                    value = e.H.parse(e.call('check-fixed-result', args)[0])
                finally:
                    e.approved_check_logs = None
                require(value == {'status': 'LOCAL_PRELOADED_IMAGE_EXECUTED', 'sources_verified': True,
                                  'files_verified': len(SOURCE_PINS), 'provider_requests': 0}, 'check_source_result')
                node_gate(e.get('check-recheck-nodes', ['nodes'] + sorted(NODES))['items'])
                row.update(status='PASS', observation=value)
                break
            sleep(3)
        else:
            raise Refused('local_image_check_deadline')
    require(len(checks) == 2 and {c['node'] for c in checks} == set(NODES)
            and all(c['status'] == 'PASS' for c in checks), 'two_executed_image_checks_required')


def frontend_snapshot(e, front, deployment):
    inv = e.get('frontend-inventory', ['replicasets,pods', '-l', 'app=frontend'])['items']
    service = e.get('frontend-service', ['service', 'frontend'])
    slices = e.get('frontend-endpoints', ['endpointslices', '-l', 'kubernetes.io/service-name=frontend'])['items']
    return front.ready(deployment, inv, service, slices), service






def services(e):
    names = ('frontend', 'gateway', 'ppt-master-runner', 'provisioner', 'nginx', 'nginx-nodeport', 'gateway-run-dispatcher')
    rows = e.get('service-preservation', ['services'] + list(names))['items']
    require(type(rows) is list and 0 < len(rows) <= 256, 'service_inventory_invalid')
    result = {}
    for d in rows:
        m = d['metadata']
        name = m['name']
        require(d.get('kind') == 'Service' and m.get('namespace') == 'deer-flow'
                and type(m.get('uid')) is str and m['uid']
                and name not in result and type(d.get('spec')) is dict, 'service_identity_invalid')
        result[name] = {'uid': m['uid'], 'spec_sha256': pin(canonical(d['spec']))['sha256']}
    require(set(result) == set(names), 'required_serving_services_missing')
    return result

def runtime_code():
    # Read immutable candidate files and local HTTP only. No external/provider traffic.
    return """const fs=require('fs'),crypto=require('crypto'),http=require('http');
const pins=JSON.parse(process.argv[1]), hash=b=>crypto.createHash('sha256').update(b).digest('hex');
try { for(const [p,v] of Object.entries(pins)) {
 if(fs.realpathSync(p)!==p||v.bytes>2097152)throw Error('source');
 const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try { const s=fs.fstatSync(fd); if(!s.isFile()||s.size!==v.bytes)throw Error('source');
 const b=fs.readFileSync(fd),t=fs.fstatSync(fd);
 if(s.dev!==t.dev||s.ino!==t.ino||s.size!==t.size||s.mtimeMs!==t.mtimeMs||s.ctimeMs!==t.ctimeMs||hash(b)!==v.sha256)throw Error('source');
 } finally {fs.closeSync(fd)}
} } catch(e) { console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1); }
function get(path,expected){return new Promise((resolve,reject)=>{
 const q=http.get({host:'127.0.0.1',port:3000,path,timeout:8000,agent:false},r=>{let n=0;const h=crypto.createHash('sha256');
 r.on('data',b=>{n+=b.length;h.update(b);if(n>(expected?expected.bytes:1048576))q.destroy(Error('limit'))});
 r.on('error',reject);r.on('end',()=>{const ok=expected?r.statusCode===200&&n===expected.bytes&&h.digest('hex')===expected.sha256:r.statusCode>=200&&r.statusCode<400&&n>0;
 if(!ok)reject(Error('http'));else resolve({http_status:r.statusCode,bytes:n})})});
 q.on('timeout',()=>q.destroy(Error('timeout')));q.on('error',reject)})}
const assets=Object.entries(pins).filter(([p])=>p.includes('/.next/static/chunks/'));
Promise.all([get('/',null),...assets.map(([p,v])=>get(p.replace('/app/frontend/.next/static/','/_next/static/'),v))]).then(rows=>{
 console.log(JSON.stringify({status:'PASS',http_status:rows[0].http_status,bytes:rows[0].bytes,sources_verified:true,static_assets_verified:assets.length,provider_requests:0}));process.exit(0)
}).catch(()=>{console.log(JSON.stringify({status:'INCOMPLETE',error_code:'local_http_failed'}));process.exit(1)});
setTimeout(()=>process.exit(1),12000).unref();"""


def deployment_ready(d):
    s = d.get('status', {})
    wanted = d['spec'].get('replicas', 1)
    return (s.get('observedGeneration') == d['metadata']['generation']
            and all(s.get(k) == wanted for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not s.get('unavailableReplicas', 0) and not s.get('terminatingReplicas', 0))


def pod_identities(pods):
    return sorted([{'uid': p['metadata']['uid'], 'pod': p['metadata']['name'],
                    'node': p['spec']['nodeName'],
                    'containers': sorted((c.get('name'), c.get('containerID'), c.get('restartCount'), c.get('imageID'))
                                         for c in p['status'].get('containerStatuses', []))}
                   for p in pods], key=lambda p: p['uid'])


GATEWAY = {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 408, 'spec_sha256': '05691890fb9a36e8075fe947d5214501f3ac10f86b35188bbb3a8d48d4ef630e'}
GATEWAY_IMAGE = 'deerflow.local/deer-flow-gateway@sha256:c1b667d7382bbc3de57ef03d73a3a73fa5aa9cd30b2368ca3ad5c48ccb378f7d'













NODE_GATE = '"""Conservative node coverage for the pinned compatibility release, Python 3.7+."""\n\n\nclass NodeGateRefused(Exception):\n    def __init__(self, code, observation=None):\n        super().__init__(code)\n        self.observation = observation or {}\n\n\ndef require(ok, code):\n    if not ok:\n        raise NodeGateRefused(code)\n\n\ndef requirement_valid(item, fields=False):\n    require(isinstance(item, dict), \'NODE_AFFINITY_SHAPE\')\n    key, op, values = item.get(\'key\'), item.get(\'operator\'), item.get(\'values\', [])\n    require(isinstance(key, str) and key and isinstance(values, list)\n            and all(isinstance(v, str) for v in values), \'NODE_AFFINITY_SHAPE\')\n    if fields:\n        require(key == \'metadata.name\' and op in (\'In\', \'NotIn\'), \'UNSUPPORTED_NODE_FIELD\')\n    if op in (\'In\', \'NotIn\'):\n        require(bool(values), \'NODE_AFFINITY_VALUES\')\n    elif op in (\'Exists\', \'DoesNotExist\'):\n        require(not values, \'NODE_AFFINITY_VALUES\')\n    elif op in (\'Gt\', \'Lt\'):\n        require(len(values) == 1, \'NODE_AFFINITY_VALUES\')\n        try:\n            int(values[0])\n        except ValueError:\n            raise NodeGateRefused(\'NODE_AFFINITY_VALUES\')\n    else:\n        raise NodeGateRefused(\'UNSUPPORTED_NODE_OPERATOR\')\n\n\ndef requirement_matches(item, labels):\n    key, op, values = item[\'key\'], item[\'operator\'], item.get(\'values\', [])\n    present = key in labels\n    if op == \'In\':\n        return present and labels[key] in values\n    if op == \'NotIn\':\n        return not present or labels[key] not in values\n    if op == \'Exists\':\n        return present\n    if op == \'DoesNotExist\':\n        return not present\n    if not present:\n        return False\n    try:\n        actual, expected = int(labels[key]), int(values[0])\n    except (TypeError, ValueError):\n        return False\n    return actual > expected if op == \'Gt\' else actual < expected\n\n\ndef scheduling(spec):\n    require(spec.get(\'schedulerName\', \'default-scheduler\') == \'default-scheduler\'\n            and not spec.get(\'nodeName\'), \'UNSUPPORTED_SCHEDULER_OR_NODE_BINDING\')\n    selector = spec.get(\'nodeSelector\') or {}\n    require(isinstance(selector, dict) and all(isinstance(k, str) and isinstance(v, str)\n                                              for k, v in selector.items()), \'NODE_SELECTOR_SHAPE\')\n    affinity = spec.get(\'affinity\') or {}\n    require(isinstance(affinity, dict), \'NODE_AFFINITY_SHAPE\')\n    node_affinity = affinity.get(\'nodeAffinity\') or {}\n    require(isinstance(node_affinity, dict), \'NODE_AFFINITY_SHAPE\')\n    required = node_affinity.get(\'requiredDuringSchedulingIgnoredDuringExecution\')\n    terms = None\n    if required is not None:\n        require(isinstance(required, dict) and isinstance(required.get(\'nodeSelectorTerms\'), list),\n                \'NODE_AFFINITY_SHAPE\')\n        terms = required[\'nodeSelectorTerms\']\n        for term in terms:\n            require(isinstance(term, dict) and set(term) <= {\'matchExpressions\', \'matchFields\'},\n                    \'NODE_AFFINITY_SHAPE\')\n            for key in (\'matchExpressions\', \'matchFields\'):\n                items = term.get(key, [])\n                require(isinstance(items, list), \'NODE_AFFINITY_SHAPE\')\n                for item in items:\n                    requirement_valid(item, fields=key == \'matchFields\')\n    tolerations = spec.get(\'tolerations\') or []\n    require(isinstance(tolerations, list), \'TOLERATION_SHAPE\')\n    for t in tolerations:\n        require(isinstance(t, dict) and t.get(\'operator\', \'Equal\') in (\'Equal\', \'Exists\')\n                and t.get(\'effect\', \'\') in (\'\', \'NoSchedule\', \'NoExecute\', \'PreferNoSchedule\')\n                and all(isinstance(t.get(k, \'\'), str) for k in (\'key\', \'value\')), \'TOLERATION_SHAPE\')\n        require(bool(t.get(\'key\')) or t.get(\'operator\') == \'Exists\', \'TOLERATION_SHAPE\')\n        require(t.get(\'operator\') != \'Exists\' or not t.get(\'value\'), \'TOLERATION_SHAPE\')\n    return selector, terms, tolerations\n\n\ndef tolerated(taint, tolerations):\n    for t in tolerations:\n        if t.get(\'effect\') and t[\'effect\'] != taint[\'effect\']:\n            continue\n        if t.get(\'key\') and t[\'key\'] != taint[\'key\']:\n            continue\n        if t.get(\'operator\', \'Equal\') == \'Exists\' or t.get(\'value\', \'\') == taint.get(\'value\', \'\'):\n            return True\n    return False\n\n\ndef exclusions(node, schedule):\n    selector, terms, tolerations = schedule\n    labels = node[\'metadata\'].get(\'labels\', {})\n    require(isinstance(labels, dict), \'NODE_LABEL_SHAPE\')\n    reasons = []\n    if any(labels.get(k) != v for k, v in selector.items()):\n        reasons.append(\'node_selector\')\n    if terms is not None:\n        fields = {\'metadata.name\': node[\'metadata\'][\'name\']}\n        matched = any(\n            bool(term.get(\'matchExpressions\') or term.get(\'matchFields\'))\n            and all(requirement_matches(r, labels) for r in term.get(\'matchExpressions\', []))\n            and all(requirement_matches(r, fields) for r in term.get(\'matchFields\', []))\n            for term in terms\n        )\n        if not matched:\n            reasons.append(\'required_node_affinity\')\n    taints = node.get(\'spec\', {}).get(\'taints\') or []\n    require(isinstance(taints, list), \'NODE_TAINT_SHAPE\')\n    for t in taints:\n        require(isinstance(t, dict) and isinstance(t.get(\'key\'), str)\n                and isinstance(t.get(\'value\', \'\'), str)\n                and t.get(\'effect\') in (\'NoSchedule\', \'NoExecute\', \'PreferNoSchedule\'), \'NODE_TAINT_SHAPE\')\n        # Admission can add tolerations absent from the Deployment for standard\n        # health/pressure taints. Do not use those as proof of exclusion. Unlike\n        # those taints, an explicit unschedulable NoSchedule taint excludes an\n        # ordinary Deployment without a matching toleration.\n        if t[\'key\'].startswith(\'node.kubernetes.io/\') and not (\n                t[\'key\'] == \'node.kubernetes.io/unschedulable\' and t[\'effect\'] == \'NoSchedule\'):\n            continue\n        # Transient cordon/health/capacity and soft preferences cannot prove that\n        # a future replacement Pod will stay within the preloaded node set.\n        if t[\'effect\'] in (\'NoSchedule\', \'NoExecute\') and not tolerated(t, tolerations):\n            reasons.append(\'untolerated_hard_taint\')\n            break\n    return reasons\n\n\ndef assess_nodes(nodes, deployments, expected):\n    require(isinstance(nodes, list) and set(deployments) == set(expected), \'NODE_INPUT_SHAPE\')\n    observed = {}\n    for n in nodes:\n        m = n.get(\'metadata\', {})\n        require(isinstance(m.get(\'name\'), str) and isinstance(m.get(\'uid\'), str)\n                and m[\'name\'] not in observed, \'NODE_IDENTITY_SHAPE\')\n        observed[m[\'name\']] = n\n    pinned = {host: uid for p in expected.values() for host, uid in p[\'nodes\'].items()}\n    result = {\'observed_node_count\': len(nodes), \'preloaded_node_count\': len(pinned),\n              \'missing_pinned_nodes\': sorted(set(pinned) - set(observed)),\n              \'additional_nodes\': sorted(set(observed) - set(pinned)),\n              \'uid_mismatches\': [], \'unhealthy_pinned_nodes\': [], \'role_matrix\': {}}\n    for host, uid in sorted(pinned.items()):\n        if host not in observed:\n            continue\n        node = observed[host]\n        if node[\'metadata\'][\'uid\'] != uid:\n            result[\'uid_mismatches\'].append(host)\n        conditions = {c[\'type\']: c[\'status\'] for c in node.get(\'status\', {}).get(\'conditions\', [])}\n        if conditions.get(\'Ready\') != \'True\' or conditions.get(\'DiskPressure\') != \'False\':\n            result[\'unhealthy_pinned_nodes\'].append(host)\n    for key, code in ((\'missing_pinned_nodes\', \'PRELOADED_NODE_MISSING\'),\n                      (\'uid_mismatches\', \'PRELOADED_NODE_UID_CHANGED\'),\n                      (\'unhealthy_pinned_nodes\', \'PRELOADED_NODE_NOT_HEALTHY\')):\n        if result[key]:\n            raise NodeGateRefused(code, result)\n    uncovered = False\n    for name, d in sorted(deployments.items()):\n        schedule = scheduling(d[\'spec\'][\'template\'][\'spec\'])\n        row = {\'potential_nodes\': [], \'uncovered_nodes\': [], \'excluded_nodes\': {}}\n        for host, node in sorted(observed.items()):\n            reasons = exclusions(node, schedule)\n            if reasons:\n                row[\'excluded_nodes\'][host] = reasons\n            else:\n                row[\'potential_nodes\'].append(host)\n                if host not in expected[name][\'nodes\']:\n                    row[\'uncovered_nodes\'].append(host)\n                    uncovered = True\n        result[\'role_matrix\'][name] = row\n    if uncovered:\n        raise NodeGateRefused(\'SCHEDULABLE_NODE_NOT_PRELOADED\', result)\n    result[\'status\'] = \'ROLE_NODE_COVERAGE_ACCEPTED\'\n    return result\n'










def read_prior(helper):
    require(PRIOR_RECEIPT.get('scope') == 'operator_observed_deployment_identity'
        and PRIOR_RECEIPT.get('gateway') == GATEWAY and PRIOR_RECEIPT.get('frontend') == FRONT,
        'observed_baseline_binding_changed')
    return PRIOR_RECEIPT


def require_backend_ready(deployment):
    require(GATEWAY is not None and binding(deployment) == GATEWAY and deployment['spec'].get('replicas') == 70
        and deployment_ready(deployment), 'gateway_not_stable_or_changed')
    containers = [c for c in deployment['spec']['template']['spec']['containers'] if c.get('name') == 'gateway']
    require(len(containers) == 1 and containers[0].get('image') == GATEWAY_IMAGE, 'gateway_image_changed')


def check_backend_contract(e, deployment):
    read_prior(e.H)
    require(not deployment.get('spec', {}).get('paused', False), 'gateway_paused_no_frontend_change')
    require_backend_ready(deployment)
    e.report['gateway_observed_binding'] = GATEWAY
    e.report['gateway_deployment_rechecked'] = True
    e.report['gateway_business_acceptance_claimed'] = False


def require_frontend_coverage(e,deployment):
    gate = types.ModuleType('_core_frontend_node_gate')
    exec(compile(NODE_GATE,'<core-frontend-node-gate>','exec'),gate.__dict__)
    nodes = e.get('frontend-schedule-coverage',['nodes'])['items']
    try:
        observed = gate.assess_nodes(nodes, {'frontend':deployment}, {'frontend':{'nodes':NODES}})
    except gate.NodeGateRefused as exc:
        e.report['frontend_node_coverage'] = exc.observation
        code = str(exc).lower()
        raise Refused(code if re.fullmatch('[a-z0-9_]{1,100}',code) else 'frontend_node_coverage_failed')
    e.report['frontend_node_coverage'] = observed


def execute(e, front, sleep=time.sleep):
    r = e.report
    r.update(nginx_configuration_semantics_verified=False, ppt_guard_configuration_semantics_verified=False, skillhub_native_auth_remediation_included=False, preload_evidence_scope='operator_summaries_and_fresh_dual_node_image_execution')
    validate_preloads(PRELOADS)
    before = {d['metadata']['name']: d for d in e.get('before-deployments', ['deployments'] + ROLES)['items']}
    require(set(before) == set(ROLES) and binding(before['frontend']) == FRONT and (not before['frontend']['spec'].get('paused', False)) and deployment_ready(before['frontend']), 'frontend_baseline_changed')
    require({k: binding(v) for k, v in before.items()} == PROTECTED_BASELINE, 'protected_baseline_changed')
    check_backend_contract(e, before['gateway'])
    original = before['frontend']
    front.prepare_patch(original, original)
    r['fresh_nodes_before'] = front.node_gate(e.get('frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    require_frontend_coverage(e, original)
    service_before = e.get('before-service', ['service', 'frontend'])
    service_bindings = services(e)
    r['before'] = {k: binding(v) for k, v in before.items()}
    e.write('BEFORE-DEPLOYMENTS.private.json', list(before.values()))
    e.H.write_new(e.parent, ONCE, canonical({'operation': r['operation'], 'directory': r['directory'], 'automatic_retry': False, 'prior_frontend_receipt': PRIOR_RECEIPT, 'failed_r2': FAILED_R2, 'preloads_sha256': pin(canonical(PRELOADS))['sha256'], 'rechecks_sha256': pin(canonical(RECHECKS))['sha256']}) + b'\n')
    r['once_claimed'] = True
    verify_local_cache(e, sleep)
    fresh = {d['metadata']['name']: d for d in e.get('fresh-deployments', ['deployments'] + ROLES)['items']}
    require({k: binding(v) for k, v in fresh.items()} == r['before'] and services(e) == service_bindings, 'state_changed_before_patch')
    check_backend_contract(e, fresh['gateway'])
    require_frontend_coverage(e, fresh['frontend'])
    patch = front.prepare_patch(fresh['frontend'], original)
    require(len(r.get('cache_checks', [])) == 2 and {c['node'] for c in r['cache_checks']} == set(NODES) and all((c['status'] == 'PASS' for c in r['cache_checks'])), 'executed_cache_checks_missing')
    patched = e.patch_once(patch)
    require(patched['metadata']['generation'] == FRONT['generation'] + 1, 'frontend_generation_changed')
    r['patch_acknowledged_by_readback'] = True
    while True:
        require(time.monotonic() - e.started < SECONDS, 'frontend_rollout_deadline')
        current = e.get('observe-frontend', ['deployment', 'frontend'])
        require(binding(current) == binding(patched), 'frontend_changed_during_rollout')
        pods, service = frontend_snapshot(e, front, current)
        require(service == service_before, 'frontend_service_changed')
        if pods is not None:
            break
        sleep(5)
    assets = sum(('/.next/static/chunks/' in p for p in SOURCE_PINS))
    require(assets > 0, 'static_asset_pins_missing')
    runtime = []
    for pod in pods:
        value = json.loads(e.call('frontend-source-http', ['exec', pod['metadata']['name'], '-c', 'frontend', '--', 'node', '-e', runtime_code(), json.dumps(SOURCE_PINS)])[0])
        require(value.get('status') == 'PASS' and value.get('sources_verified') is True and (value.get('provider_requests') == 0) and (value.get('static_assets_verified') == assets) and (type(value.get('http_status')) is int) and (200 <= value['http_status'] < 400) and (type(value.get('bytes')) is int) and (0 < value['bytes'] <= 1048576), 'frontend_runtime_failed')
        runtime.append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': pod['spec']['nodeName'], 'observation': value})
    r['runtime'] = runtime
    final = {d['metadata']['name']: d for d in e.get('after-deployments', ['deployments'] + ROLES)['items']}
    require(set(final) == set(before) and all((binding(final[k]) == r['before'][k] for k in ROLES if k != 'frontend')), 'unrelated_spec_changed')
    require(binding(final['frontend']) == binding(patched) and services(e) == service_bindings, 'service_or_frontend_changed')
    stable, service = frontend_snapshot(e, front, final['frontend'])
    require(service == service_before and stable is not None and (pod_identities(stable) == pod_identities(pods)), 'frontend_containers_or_endpoints_changed')
    check_backend_contract(e, final['gateway'])
    r['fresh_nodes_after'] = front.node_gate(e.get('final-frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    e.write('AFTER-DEPLOYMENTS.private.json', list(final.values()))
    r.update(status=ACCEPTED, stage_passed=True, after={k: binding(v) for k, v in final.items()}, unrelated_specs_preserved=True, services_preserved=True, accepted_pods=runtime, browser_acceptance_pending=True)


def run():
    r = {'schema': 'portal-frontend-sidechat-security-activation.v1', 'tool_revision': 'r1', 'status': 'INCOMPLETE', 'stage_passed': False,
         'operation': uuid.uuid4().hex, 'once_claimed': False, 'patch_requested': False, 'patch_request_count': 0,
         'patch_may_have_applied': False, 'automatic_retry': False, 'automatic_rollback': False,
         'schema_migration_requested': False, 'work_datalake_activated': False, 'database_access': False, 'other_workload_mutation': False, 'provider_requests': 0,
         'temporary_jobs_created': [], 'cache_checks': [], 'image_pull_policy': 'Never',
         'check_job_ttl_seconds': 3600,
         'browser_acceptance_pending': True, 'commands': [], 'prior_frontend_receipt': PRIOR_RECEIPT,
         'preload_receipts': PRELOADS, 'cache_manifest': MANIFEST, 'cache_config': CONFIG, 'source_pins': SOURCE_PINS}
    helper = parent = directory = None
    started = time.monotonic()
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode
                and socket.gethostname() == 'datax-01', 'isolated_root_datax01_required')
        require(sys.argv[1:] == ['--activate-frontend-sidechat-security-once'], 'explicit_activation_required')
        validate_preloads(PRELOADS)
        front, helper = load_modules()
        parent = helper.private_parent()
        require(not os.path.lexists('/var/lib/' + ONCE), 'once_marker_exists_review_required')
        disk = os.fstatvfs(parent)
        require(disk.f_bavail * disk.f_frsize >= 256 * 1024**2, 'private_log_capacity')
        read_prior(helper)
        folder = 'portal-frontend-sidechat-security-activation-20261106-r1-' + r['operation']
        os.mkdir(folder, 0o700, dir_fd=parent)
        os.fsync(parent)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        r['directory'] = '/var/lib/' + folder
        kubectl = shutil.which('kubectl', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        require(kubectl in ('/usr/bin/kubectl', '/usr/local/bin/kubectl'), 'trusted_kubectl_path')
        for path in (kubectl, os.path.dirname(kubectl)):
            st = os.lstat(path)
            require(st.st_uid == 0 and not st.st_mode & 0o022 and not stat.S_ISLNK(st.st_mode), 'trusted_kubectl_permissions')
        e = make_execution(front)(helper, directory, kubectl, r)
        e.parent = parent
        execute(e, front)
    except BaseException as error:
        code = str(error)
        r.update(status='INCOMPLETE', stage_passed=False, error_class=type(error).__name__,
                 error_code=code if isinstance(error, Refused) and re.fullmatch('[a-z0-9_]{1,120}', code) else 'details_withheld')
    finally:
        r['preflight_retry_available'] = not r['once_claimed'] and not r['patch_may_have_applied'] and r['patch_request_count'] == 0
        r['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if directory is not None:
            try:
                raw = canonical(r) + b'\n'
                helper.write_new(directory, 'REPORT.json', raw)
                r.update(report_path=r['directory'] + '/REPORT.json', report_bytes=len(raw), report_sha256=pin(raw)['sha256'])
            except BaseException:
                r.update(status='INCOMPLETE', stage_passed=False, error_code='report_persistence_failed')
        for fd in (directory, parent):
            if fd is not None:
                os.close(fd)
    output = {k: v for k, v in r.items() if k not in ('commands', 'backend_before', 'backend_after', 'preload_receipts', 'source_pins')}
    output['exitcode'] = 0 if r['stage_passed'] else 1
    print(json.dumps(output, sort_keys=True), flush=True)
    return output['exitcode']


RECHECKS = {'datax-02': {'automatic_retry': False, 'config_digest': 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821', 'configuration_modified': False, 'content_and_unpack': 'PASS', 'content_blobs_present': 13, 'cri_visibility': 'PASS', 'database_access': False, 'elapsed_seconds': 5.793, 'exitcode': 0, 'expected_node_uid': '3148d4c1-6a97-42ed-a78c-950dcea5cbec', 'hostname': 'datax-02', 'image_deleted': False, 'image_import_requested': False, 'image_retag_requested': False, 'kubernetes_node_uid_freshly_checked': False, 'last_step': 'stable_prior_receipt', 'layer_hash_basis': 'sealed_original_import_receipt_and_fresh_containerd_closure', 'layer_payloads_rehashed_now': False, 'manifest_digest': 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3', 'observed_at': '2026-11-06T00:45:44.089697+00:00', 'old_references_preserved': True, 'operation': '41d307e6ef074c2abadb1dd4f819f70f', 'original_marker_preserved': True, 'original_receipt_modified': False, 'prior_receipt': {'bytes': 115986, 'commands': 200, 'error_code': 'command_limit', 'operation': '281ac71347fa44a59ef78d56c690c8f3', 'path': '/var/lib/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json', 'sha256': 'b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120', 'status': 'INCOMPLETE', 'verified_stop': 'final_reference_readback'}, 'production_workload_mutation': False, 'protected_reference_count': 31, 'provider_requests': 0, 'queries': 80, 'raw_errors_exported': False, 'ready_for_rollout': False, 'report_bytes': 21915, 'report_directory_created': True, 'report_path': '/var/lib/portal-frontend-sidechat-security-recheck-20261106-r1-41d307e6ef074c2abadb1dd4f819f70f/REPORT.json', 'report_sha256': 'd3d50cb5cea8f7c233c166cd131e4c4f06ea9c744f6bd08f0c45cc694ca6cb99', 'schema': 'portal-frontend-sidechat-cache-recheck.v1', 'snapshot_chains_verified': 11, 'stable_readback': True, 'status': 'FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED', 'storage_capacity_rechecked': False, 'temporary_reference_preserved': True}}
PRELOAD_PINS = {'ecs-zdz-pro-0015': {'bytes': 118975, 'sha256': '4442dbd901bbb96e19dd22c8011aac8407ab318dcf9fc4f1168af765fb5eb51e', 'operation': 'f7ea0c04a2ce4d03a1de272c7578518f'}, 'datax-02': {'bytes': 115986, 'sha256': 'b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120', 'operation': '281ac71347fa44a59ef78d56c690c8f3'}}
RECHECK_PIN = {'bytes': 21915, 'sha256': 'd3d50cb5cea8f7c233c166cd131e4c4f06ea9c744f6bd08f0c45cc694ca6cb99', 'operation': '41d307e6ef074c2abadb1dd4f819f70f'}
RECHECK_PATH = '/var/lib/portal-frontend-sidechat-security-recheck-20261106-r1-41d307e6ef074c2abadb1dd4f819f70f/REPORT.json'
PROTECTED_BASELINE = {'frontend': {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 241, 'spec_sha256': 'f48dcd7f2a54bc8867fcfa77f84682c22d3e6993fd0091779d1e639f8fee313a'}, 'gateway': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 408, 'spec_sha256': '05691890fb9a36e8075fe947d5214501f3ac10f86b35188bbb3a8d48d4ef630e'}, 'ppt-master-runner': {'uid': '4115f178-0599-445e-89c3-9d9452ddc26a', 'generation': 58, 'spec_sha256': '8602f3a5cf40ff97c3f9ee36d792e0c76ae37be53469b8146be41d3409719934'}, 'provisioner': {'uid': '9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253', 'generation': 86, 'spec_sha256': '009352242b1f12bb361b0874aff9ecc6d72b9d4003b4c1e54988f786c6fe7dd2'}, 'nginx': {'uid': '10fb043a-eaee-433e-bce0-80688e798611', 'generation': 116, 'spec_sha256': '043c199d237cbab9f5169e10f909865fbf55c4cc3a11776c377a407605b1b03a'}, 'nginx-security-heai-20261012-r1': {'uid': 'ce96a382-140e-4baf-9a85-d623b7df6e3d', 'generation': 2, 'spec_sha256': 'f065f125b1f8417010f3108ae682de3739c4a267040e6d803f26ce78363f6141'}}

if __name__ == '__main__':
    sys.exit(run())
