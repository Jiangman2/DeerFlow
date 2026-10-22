"""Pure native DB fixture rendering and receipt validation; no cluster client."""
import base64
import copy
import hashlib
import json
import re
import zlib

ASSET_Z64 = '__ASSET_Z64__'
ASSET_SHA = '__ASSET_SHA__'
PG_CACHE = __PG_CACHE__
CONTAINER_IDENTITIES = __CONTAINER_IDENTITIES__
OWNER_LABEL = 'portal-ppt-network-owner'
PG_PATH = '/usr/lib/postgresql/16/bin:/usr/local/bin:/usr/bin:/bin'
PG_FILES = {
    'entrypoint': '/usr/local/bin/docker-entrypoint.sh',
    'postgres': '/usr/lib/postgresql/16/bin/postgres',
    'pg_isready': '/usr/lib/postgresql/16/bin/pg_isready',
    'initdb': '/usr/lib/postgresql/16/bin/initdb',
}


def require(value, code):
    if not value:
        raise ValueError(code)


def assets():
    raw = zlib.decompress(base64.b64decode(ASSET_Z64, validate=True))
    require(len(raw) < 131072 and hashlib.sha256(raw).hexdigest() == ASSET_SHA, 'db_asset_bundle_changed')
    value = json.loads(raw)
    for row in value.values():
        content = base64.b64decode(row['base64'], validate=True)
        require(len(content) == row['bytes'] and hashlib.sha256(content).hexdigest() == row['sha256'], 'db_asset_changed')
    return value


def asset_bytes(name):
    return base64.b64decode(assets()[name]['base64'], validate=True)


def identity(owner, namespace):
    require(isinstance(owner, str) and re.fullmatch('[a-f0-9]{32}', owner), 'db_owner_shape')
    require(namespace == 'portal-ppt-' + owner, 'db_namespace_binding')


def pg_binding(value):
    require(value == PG_CACHE, 'db_pg_cache_changed')


def base_pod(owner, namespace, name, container, deadline=60):
    identity(owner, namespace)
    return {
        'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': name, 'namespace': namespace, 'labels': {OWNER_LABEL: owner}},
        'spec': {
            'restartPolicy': 'Never', 'activeDeadlineSeconds': deadline, 'terminationGracePeriodSeconds': 5,
            'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
            'dnsPolicy': 'None', 'dnsConfig': {'nameservers': ['127.0.0.1']},
            'schedulerName': 'default-scheduler', 'preemptionPolicy': 'Never',
            'nodeSelector': {'kubernetes.io/hostname': 'datax-02', 'kubernetes.io/arch': 'arm64', 'kubernetes.io/os': 'linux'},
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchFields': [{'key': 'metadata.name', 'operator': 'In', 'values': ['datax-02']}]}]}}},
            'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [container],
        },
    }


def render_pg_probe(owner, namespace, pg_cache):
    identity(owner, namespace); pg_binding(pg_cache)
    # These image-local reads have no DB or network client and emit only fixed paths/numeric IDs.
    script = '''set -eu
uid=$(id -u postgres)
gid=$(id -g postgres)
case "$uid:$gid" in *[!0-9:]*|:|0:*|*:0) exit 31;; esac
for p in /usr/local/bin/docker-entrypoint.sh /usr/lib/postgresql/16/bin/postgres /usr/lib/postgresql/16/bin/pg_isready /usr/lib/postgresql/16/bin/initdb; do test -f "$p" && test -x "$p" || exit 32; done
version=$(/usr/lib/postgresql/16/bin/postgres --version)
case "$version" in 'postgres (PostgreSQL) '*) version=${version#'postgres (PostgreSQL) '};; *) exit 33;; esac
version=${version%% *}
case "$version" in 16.*) ;; *) exit 34;; esac
case "$version" in *[!0-9.]*|16.) exit 35;; esac
printf '{"schema":"portal-native-pg-profile.v1","owner":"OWNER","uid":%s,"gid":%s,"server_binary_version":"%s","entrypoint":"/usr/local/bin/docker-entrypoint.sh","postgres":"/usr/lib/postgresql/16/bin/postgres","pg_isready":"/usr/lib/postgresql/16/bin/pg_isready","initdb":"/usr/lib/postgresql/16/bin/initdb","database_started":false}\\n' "$uid" "$gid" "$version"
'''.replace('OWNER', owner)
    container = {
        'name': 'pg-profile', 'image': PG_CACHE['reference'], 'imagePullPolicy': 'Never',
        'command': ['/usr/bin/env'], 'args': ['-i', 'PATH=' + PG_PATH, 'HOME=/tmp', '/bin/sh', '-eu', '-c', script],
        'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}},
        'securityContext': {'runAsUser': 65532, 'runAsGroup': 65532, 'runAsNonRoot': True,
                            'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                            'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}},
    }
    return base_pod(owner, namespace, 'pg-profile', container)


def validate_pg_probe(receipt, pg_cache, owner=None):
    pg_binding(pg_cache)
    required = {'schema', 'owner', 'uid', 'gid', 'server_binary_version', 'database_started'} | set(PG_FILES)
    require(isinstance(receipt, dict) and set(receipt) == required, 'db_pg_profile_shape')
    require(receipt['schema'] == 'portal-native-pg-profile.v1' and receipt['database_started'] is False, 'db_pg_profile_scope')
    require(re.fullmatch('[a-f0-9]{32}', receipt['owner']) and (owner is None or receipt['owner'] == owner), 'db_pg_profile_owner')
    require(all(type(receipt[k]) is int and 0 < receipt[k] < 2147483648 for k in ('uid', 'gid')), 'db_pg_profile_user')
    require(re.fullmatch(r'16\.[0-9]+(?:\.[0-9]+)?', receipt['server_binary_version']), 'db_pg_profile_version')
    require(all(receipt[k] == v for k, v in PG_FILES.items()), 'db_pg_profile_path')
    return dict(receipt)


def fixture_inline(ordinal):
    require(ordinal in ('1', '2', '3', '4', '5', 'final'), 'db_step_invalid')
    fixed = {name: row for name, row in assets().items() if name != 'POD-TEMPLATE.json'}
    wire = json.dumps(fixed, sort_keys=True, separators=(',', ':')).encode()
    encoded = base64.b64encode(zlib.compress(wire, 9)).decode()
    return '''import base64,hashlib,json,os,stat,sys,zlib
raw=zlib.decompress(base64.b64decode(BUNDLE))
if hashlib.sha256(raw).hexdigest()!=BUNDLE_SHA: raise RuntimeError('fixture_bundle_changed')
files=json.loads(raw)
for name,record in files.items():
 data=base64.b64decode(record['base64']); path='/qualification/'+name
 if len(data)!=record['bytes'] or hashlib.sha256(data).hexdigest()!=record['sha256']: raise RuntimeError('fixture_bytes_changed')
 if ORDINAL=='1':
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
  try:
   with os.fdopen(fd,'wb',closefd=False) as stream: stream.write(data); stream.flush()
   os.fsync(fd)
  finally: os.close(fd)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  info=os.fstat(fd)
  if not stat.S_ISREG(info.st_mode) or info.st_size!=len(data): raise RuntimeError('fixture_file_shape')
  with os.fdopen(fd,'rb',closefd=False) as stream: found=stream.read(len(data)+1)
  if found!=data: raise RuntimeError('fixture_readback_changed')
 finally: os.close(fd)
sys.argv=['/qualification/probe_supervisor.py',ORDINAL]
exec(compile(base64.b64decode(files['probe_supervisor.py']['base64']),'/qualification/probe_supervisor.py','exec'),{'__name__':'__main__'})
'''.replace('BUNDLE_SHA', repr(hashlib.sha256(wire).hexdigest())).replace('BUNDLE', repr(encoded)).replace('ORDINAL', repr(ordinal))


def render_db(owner, namespace, profile):
    identity(owner, namespace); validate_pg_probe(profile, PG_CACHE, owner)
    pod = json.loads(asset_bytes('POD-TEMPLATE.json'))['pod_template']
    pod['metadata'] = {'name': 'db-bootstrap', 'namespace': namespace, 'labels': {OWNER_LABEL: owner}}
    pg = pod['spec']['initContainers'][0]
    pg['command'] = ['/usr/bin/env']
    pg['args'] = ['-i', 'PATH=' + PG_PATH, 'HOME=/tmp', 'LANG=C.UTF-8'] + [item['name'] + '=' + item['value'] for item in pg.pop('env')]
    pg['args'] += [profile['entrypoint'], 'postgres', '-c', 'listen_addresses=127.0.0.1', '-c', 'unix_socket_directories=/var/run/postgresql']
    pg['securityContext']['runAsUser'] = profile['uid']; pg['securityContext']['runAsGroup'] = profile['gid']
    pg['startupProbe']['exec']['command'][0] = profile['pg_isready']
    for ordinal, container in enumerate(pod['spec']['initContainers'][1:], 1):
        index = container['args'].index('-c')
        container['args'] = container['args'][:index+1] + [fixture_inline(str(ordinal))]
    main = pod['spec']['containers'][0]; index = main['args'].index('-c')
    main['args'] = main['args'][:index+1] + [fixture_inline('final')]
    require('__UNBOUND_' not in json.dumps(pod), 'db_unbound_template_value')
    return pod


def validate_db_summary(receipt):
    # Reuse the exact already-qualified receipt checks, without running business probes.
    namespace = {'__name__': 'portal_db_receipt_validation'}
    exec(compile(asset_bytes('probe_supervisor.py'), 'probe_supervisor.py', 'exec'), namespace)
    contract = json.loads(asset_bytes('RECEIPT-CONTRACT.json'))
    require(isinstance(receipt, dict) and receipt.get('schema') == 'portal-ppt-native-db-container-summary.v1'
            and receipt.get('status') == 'FIVE_PROBE_RECEIPTS_PASS_EXTERNAL_NATIVE_IDENTITY_REVIEW_REQUIRED', 'db_summary_status')
    require(receipt.get('probe_sha256') == namespace['PROBE_SHA'] and receipt.get('contract_sha256') == namespace['CONTRACT_SHA'], 'db_summary_fixture_pin')
    require(receipt.get('snapshot_sha256') == contract['expected_snapshot_sha256'], 'db_summary_snapshot')
    require(receipt.get('native_arm64_passed') is False and receipt.get('ready_for_rollout') is False
            and receipt.get('production_accessed') is False and receipt.get('model_requests') == 0
            and receipt.get('operator_configuration_loading') == 'NOT_TESTED', 'db_summary_scope')
    require(isinstance(receipt.get('steps'), list) and len(receipt['steps']) == 5, 'db_summary_steps')
    for packet, step in zip(receipt['steps'], contract['steps']):
        require(packet.get('schema') == 'portal-ppt-native-db-step.v1' and packet.get('status') == 'PASS'
                and packet.get('ordinal') == step['ordinal'] and packet.get('label') == step['label']
                and packet.get('probe_sha256') == namespace['PROBE_SHA'] and packet.get('contract_sha256') == namespace['CONTRACT_SHA'], 'db_summary_step_identity')
        process = packet.get('process', {})
        require(process.get('returncode') == 0 and process.get('failure_code') is None
                and process.get('child_exit_confirmed') is True and process.get('whole_output_captured') is True
                and type(process.get('bytes')) is int and 0 <= process['bytes'] <= namespace['LIMIT'], 'db_summary_child_failure')
        namespace['validate_probe'](packet['probe_receipt'], step)
    return copy.deepcopy(receipt)
