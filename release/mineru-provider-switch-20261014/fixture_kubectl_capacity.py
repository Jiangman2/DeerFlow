#!/usr/bin/env python3
"""Offline delivery-test fixture. NOT shipped or used on production."""
import json
import sys

IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20260921-enterprise-arm64-rc15-conversation-runtime4'
IMAGE_ID = 'sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9'
names = ('deer-flow-mineru-adapter', 'deer-flow-mineru-dispatcher', 'weknora-app', 'weknora-docreader')
args = sys.argv[1:]
assert args[:3] == ['--request-timeout=20s', '-n', 'deer-flow']
args = args[3:]

def ctr(name):
    return {'name': 'adapter' if name == names[0] else 'dispatcher', 'image': IMAGE,
            'resources': {'requests': {'cpu': '500m', 'memory': '1Gi'}},
            'env': [{'name': 'MINERU_API_TOKEN', 'value': 'FIXTURE-PRIVATE-VALUE'},
                    {'name': 'WEKNORA_ASYNQ_CORE_CONCURRENCY', 'value': '4'}]}

def pod(name):
    container = ctr(name)
    return {'metadata': {'name': name + '-pod', 'uid': name + '-pod-id',
                         'ownerReferences': [{'controller': True, 'uid': name + '-rs-id'}]},
            'spec': {'nodeName': 'fixture-node', 'containers': [container]},
            'status': {'phase': 'Running', 'containerStatuses': [
                {'name': container['name'], 'ready': True, 'imageID': IMAGE_ID}]}}

if args[0] == 'exec':
    assert args[1] == '-i' and args[2] in (names[0] + '-pod', names[1] + '-pod')
    assert '127.0.0.1' in sys.stdin.read()
    out = {'source_sha256': 'fixture', 'http_status': 200, 'provider_requests': 0,
           'parser': {'active': 0, 'waiting': 0},
           'dispatcher': {'ready': True, 'busy': 0, 'discovered': 1, 'live': 1}}
else:
    assert args[0] == 'get'
    resource = args[1]
    if resource == 'deployment':
        name = args[2]
        assert name in names
        out = {'metadata': {'name': name, 'uid': name + '-id', 'generation': 1},
               'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}},
                        'template': {'spec': {'containers': [ctr(name)]}}},
               'status': {'readyReplicas': 1}}
    elif resource == 'nodes':
        out = {'items': [{'metadata': {'name': 'fixture-node'}, 'spec': {},
            'status': {'allocatable': {'cpu': '8', 'memory': '16Gi', 'pods': '110'},
                       'nodeInfo': {'architecture': 'arm64'},
                       'conditions': [{'type': k, 'status': v} for k, v in (
                           ('Ready', 'True'), ('MemoryPressure', 'False'),
                           ('DiskPressure', 'False'), ('PIDPressure', 'False'))]}}]}
    elif resource == 'pods' and '--all-namespaces' in args:
        assert args[args.index('--field-selector') + 1] == 'spec.nodeName=fixture-node'
        out = {'items': [pod(n) for n in names]}
    else:
        name = args[args.index('-l') + 1].removeprefix('app=')
        assert name in names
        if resource == 'replicasets':
            out = {'items': [{'metadata': {'uid': name + '-rs-id',
                'ownerReferences': [{'controller': True, 'uid': name + '-id'}]}}]}
        else:
            assert resource == 'pods'
            out = {'items': [pod(name)]}
print(json.dumps(out))
