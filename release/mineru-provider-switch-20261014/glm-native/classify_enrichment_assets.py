"""Classify pinned runtime/source byte differences without changing either input."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import resource
import signal
import socket
import sys
import tempfile
import traceback

import audit_enrichment_runtime as a

PRIOR = Path('/var/lib/weknora-enrichment-runtime-audit-e6_1488t/RESULT.json')
PRIOR_SHA = '1536733f52e61636a90444255ec35c691f16dcbddde1ef0ad399d1f998b5303e'
SOURCE_SHA = '43b8484b6ef5a18ed76a6a13ab3faf8a13eca8a76589d454ae0687293971e1b0'
PHASE = 'PREFLIGHT'


def phase(value):
    global PHASE
    PHASE = value
    a.emit(dict(runtime_asset_classification='RUNNING', phase=value, production_writes=0))


def digest(raw): return hashlib.sha256(raw).hexdigest()


def newline_hashes(raw):
    # These are exact byte transformations, NOT SQL/YAML semantic equivalence.
    # Preserve BOM, all whitespace, final newline, and standalone CR characters.
    # No source text/config value is retained in the receipt or terminal output.
    try: raw.decode('utf-8')
    except UnicodeDecodeError: return dict(text='NON_UTF8', variants={})
    if b'\0' in raw: return dict(text='NUL_PRESENT', variants={})
    lf = raw.replace(b'\r\n', b'\n')
    return dict(text='UTF8', crlf_pairs=raw.count(b'\r\n'),
        bare_lf=raw.count(b'\n')-raw.count(b'\r\n'), standalone_cr=lf.count(b'\r'),
        variants={'CRLF_TO_LF': digest(lf), 'CANONICAL_CRLF': digest(lf.replace(b'\n', b'\r\n'))})


def classify(expected, actual):
    rows = []
    for name, source_sha in sorted(expected.items()):
        item = actual.get(name)
        blocked_parent = any(str(p) in actual and actual[str(p)]['kind'] != 'directory'
                             for p in PurePosixPath(name).parents if str(p) != '.')
        kind, transform = 'UNCLASSIFIED_BYTE_DIFFERENCE', None
        if item is None: kind = 'MISSING'
        elif item['kind'] != 'file' or blocked_parent: kind = 'NONREGULAR_PATH'
        elif item['sha256'] == source_sha: kind = 'EXACT'
        else:
            variants = item.get('content_classification', {}).get('variants', {})
            for label in ('CRLF_TO_LF', 'CANONICAL_CRLF'):
                if variants.get(label) == source_sha:
                    kind, transform = 'LINE_ENDINGS_ONLY', label
                    break
        row = dict(path=name, classification=kind, candidate_to_source_transform=transform,
                   source_sha256=source_sha, candidate_sha256=item.get('sha256') if item else None)
        rows.append(row)
    expected_counts = {}
    for label, prefix in (('postgres_migrations','app/migrations/versioned/'),
                          ('config_assets','app/config/'), ('entrypoint','app/scripts/')):
        selected = [r for r in rows if r['path'].startswith(prefix)]
        expected_counts[label] = dict(expected=len(selected),
            exact=sum(r['classification']=='EXACT' for r in selected),
            line_endings_only=sum(r['classification']=='LINE_ENDINGS_ONLY' for r in selected),
            unresolved=sum(r['classification'] not in ('EXACT','LINE_ENDINGS_ONLY') for r in selected))
    extra = sorted(k for k,v in actual.items() if a.wanted(k) and v['kind']!='directory' and k not in expected)
    # An operator config is not a compiled-source asset and must NOT be reused
    # by the future isolated fixture. Other extras are always unresolved.
    operator = 'app/config/config.yaml'
    ordinary_operator = (operator in extra and actual[operator]['kind']=='file'
        and all(actual.get(str(p), {}).get('kind','directory')=='directory'
                for p in PurePosixPath(operator).parents if str(p)!='.'))
    operator_extras = [operator] if ordinary_operator else []
    unexpected = [k for k in extra if k not in operator_extras]
    unresolved = [r['path'] for r in rows if r['classification'] not in ('EXACT','LINE_ENDINGS_ONLY')]
    explained = not unresolved and not unexpected
    return dict(groups=expected_counts, files=rows, unresolved=unresolved, unexpected_extra=unexpected,
        operator_config_not_source_asset=operator_extras, operator_config_reuse_allowed=False,
        byte_differences_explained=explained, semantic_equivalence_proven=False,
        fresh_database_migration_acceptance='NOT_TESTED',
        next_action='PREPARE_ISOLATED_FULL_APP_WITH_CLEAN_CONFIG' if explained else 'REVIEW_UNCLASSIFIED_FILES_NO_REBUILD_REQUESTED')


def reconcile(prior, expected, actual, source_sha):
    a.need(prior['enrichment_runtime_audit']=='COMPLETE' and prior['production_writes']==0
        and prior['candidate_archive_sha256']==a.ARCHIVE_SHA
        and prior['source_manifest_sha256']==source_sha==SOURCE_SHA, 'prior_audit_binding')
    a.need(a.comparison(expected, actual)==prior['runtime_assets'], 'prior_asset_view_changed')


def compact(result):
    out = {k:v for k,v in result.items() if k!='classification'}
    item = result['classification']
    out['classification'] = {k:v for k,v in item.items() if k not in ('files','unresolved','unexpected_extra')}
    for key in ('unresolved','unexpected_extra'):
        out['classification'][key+'_count'] = len(item[key])
        out['classification'][key+'_first20'] = item[key][:20]
    out['classification']['full_hash_comparison'] = 'RETAINED_IN_PRIVATE_RESULT_JSON'
    return out


def record(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode('utf-8')
    fd = os.open(str(path), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    return digest(raw)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    a.need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01'
           and platform.machine() in ('arm64','aarch64'), 'root_arm64_datax01_required')
    os.umask(0o077); os.nice(10); resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    def stop(signum, frame): raise a.AuditFailure('classification_interrupted')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGALRM): signal.signal(sig,stop)
    signal.alarm(900); state = None
    try:
        phase('BIND_PRIOR_AUDIT_AND_EXACT_COMPILED_SOURCE')
        prior = json.loads(a.read(PRIOR, PRIOR_SHA))
        expected, source_sha = a.source_assets()
        a.need(source_sha == SOURCE_SHA, 'source_binding_changed')
        a.directory(Path('/var/lib'))
        state = Path(tempfile.mkdtemp(prefix='weknora-runtime-byte-compare-',dir='/var/lib'))
        phase('COMPARE_RAW_AND_LINE_ENDING_ONLY_HASHES')
        actual = a.inspect_archive(a.CANDIDATE/'weknora.candidate.oci.tar', a.ARCHIVE_SHA,
            a.ARCHIVE_BYTES, a.MANIFEST, a.CONFIG, content_classifier=newline_hashes)
        reconcile(prior, expected, actual, source_sha)
        result = dict(a.common(), runtime_asset_classification='COMPLETE', classification=classify(expected,actual),
            prior_report_sha256=PRIOR_SHA, source_manifest_sha256=source_sha, candidate_archive_sha256=a.ARCHIVE_SHA,
            original_evidence_modified=False, archive_modified=False, subprocesses_started=0,
            cluster_queries=0, configuration_values_printed=False, fixture_start_approved=False)
        report = state/'RESULT.json'; sha = record(report,result)
        a.emit(compact(result)); a.emit(dict(report=str(report), report_sha256=sha))
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        result = dict(a.common(),runtime_asset_classification='INCOMPLETE',phase=PHASE,error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc,a.AuditFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            raw_error_printed=False, code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                for x in traceback.extract_tb(exc.__traceback__)[-8:]])
        if state is not None:
            report=state/'FAILED.json'; sha=record(report,result)
            result.update(report=str(report),report_sha256=sha)
        a.emit(result); return 1
    finally: signal.alarm(0)


if __name__=='__main__': sys.exit(main())
