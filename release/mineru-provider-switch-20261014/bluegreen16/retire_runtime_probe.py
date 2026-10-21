"""Read-only live candidate checks: busy new production traffic is allowed.

Reuse the exact staged config/source/CA/DNS checks, replacing only the old
pre-release assumption that the candidate must be idle. No provider connection.
"""
import hashlib
import json
import sys

STAGE_CODE = '__EMBED_FROZEN_STAGE_PROBE__'
STAGE_SHA = 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503'


class LiveFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise LiveFailure(label)


def validate_live(row, role, expected):
    require(row.get('status') == 'ok' and row.get('role') == role and expected == 6, 'live_role_or_capacity')
    if role == 'worker':
        p = row.get('parser', {})
        require(all(type(p.get(k)) is int and p[k] >= 0 for k in ('active', 'waiting'))
                and p['active'] <= 1, 'live_worker_serialization_or_counter')
        return {k: p[k] for k in ('active', 'waiting')}
    require(role == 'dispatcher', 'unexpected_live_role')
    d = row.get('dispatcher', {})
    keys = ('discovered', 'live', 'minimum', 'busy', 'waiting', 'quarantined')
    require(all(type(d.get(k)) is int and d[k] >= 0 for k in keys), 'live_dispatcher_counter')
    require(d['discovered'] == d['live'] == d['minimum'] == expected
            and d.get('ready') is True and d.get('capacity_target_met') is True
            and d['busy'] <= expected and d['quarantined'] == 0, 'live_dispatcher_capacity')
    return {k: d[k] for k in keys}


def no_provider_connection(cafile):
    return dict(tls='NOT_RETESTED_CONFIG_CA_CHECK_ONLY', tls_connections=0,
                provider_http_requests=0, credentials_sent=False)


def main():
    require(hashlib.sha256(STAGE_CODE.encode('utf-8')).hexdigest() == STAGE_SHA, 'frozen_stage_probe_hash')
    ns = {'__name__': 'frozen_staged_checks'}
    exec(compile(STAGE_CODE, '<frozen-staged-checks>', 'exec'), ns)
    ns['validate_health'] = validate_live
    ns['tls_probe'] = no_provider_connection
    ns['main']()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        text = str(exc)
        allowed = isinstance(exc, LiveFailure) or type(exc).__name__ == 'CheckFailure'
        print(json.dumps(dict(stage_runtime='FAIL', error_type=type(exc).__name__,
            failed_check=text if allowed else 'TRANSPORT_OR_RUNTIME_ERROR', raw_error_printed=False,
            provider_http_requests=0, documents_submitted=0, credentials_printed=False)), flush=True)
        sys.exit(1)
