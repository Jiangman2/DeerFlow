"""Inspect the fixed OA callback transport from two Gateway nodes; no writes or POST."""
import argparse
import json
import os
import socket
import sys
import types

COMMON = ''


def inspect_cluster():
    common = types.ModuleType('_oa_callback_diagnostic')
    exec(compile(COMMON, '<oa-callback-diagnostic>', 'exec'), common.__dict__)
    def read(args, timeout=30, limit=8 * 1024**2):
        return common.query(args, timeout=70 if args[0] == 'exec' else timeout, limit=limit)
    result = common.inspect_cluster(read=read)
    result.update(schema='portal-oa-callback-transport-diagnosis.v2',
                  diagnostic_revision='r2_native_ca_file_loading',
                  status='CALLBACK_TRANSPORT_DIAGNOSED_NOT_ENABLED',
                  maximum_loopback_get_requests=0, maximum_callback_head_requests=4,
                  business_callback_requests=0, automatic_retry=False, files_modified=False,
                  secret_api_reads=0, production_database_access=False,
                  provider_requests=0, job_submissions=0)
    result['callback_head_requests'] = sum(row['observation']['callback_diagnosis']['head_requests'] for row in result['samples'])
    assert 0 <= result['callback_head_requests'] <= 4
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose-readonly', action='store_true', required=True)
    parser.parse_args()
    try:
        if not (os.name == 'posix' and os.geteuid() == 0 and socket.gethostname() == 'datax-01'
                and sys.flags.isolated and sys.flags.dont_write_bytecode and COMMON):
            raise ValueError('DATAX01_ISOLATED_ROOT_REQUIRED')
        result = inspect_cluster()
        result['exitcode'] = 0
    except Exception as exc:
        message = str(exc)
        result = {'schema': 'portal-oa-callback-transport-diagnosis.v2', 'status': 'INCOMPLETE',
                  'error_class': type(exc).__name__, 'raw_error_exported': False,
                  'error_code': message if isinstance(exc, ValueError) and message.replace('_', '').replace('-', '').isalnum() else 'DETAILS_WITHHELD',
                  'workload_modified': False, 'files_modified': False,
                  'credentials_exported': False, 'business_callback_requests': 0, 'exitcode': 1}
    print(json.dumps(result, sort_keys=True), flush=True)
    return result['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
