"""Conservative node coverage for the pinned compatibility release, Python 3.7+."""


class NodeGateRefused(Exception):
    def __init__(self, code, observation=None):
        super().__init__(code)
        self.observation = observation or {}


def require(ok, code):
    if not ok:
        raise NodeGateRefused(code)


def requirement_valid(item, fields=False):
    require(isinstance(item, dict), 'NODE_AFFINITY_SHAPE')
    key, op, values = item.get('key'), item.get('operator'), item.get('values', [])
    require(isinstance(key, str) and key and isinstance(values, list)
            and all(isinstance(v, str) for v in values), 'NODE_AFFINITY_SHAPE')
    if fields:
        require(key == 'metadata.name' and op in ('In', 'NotIn'), 'UNSUPPORTED_NODE_FIELD')
    if op in ('In', 'NotIn'):
        require(bool(values), 'NODE_AFFINITY_VALUES')
    elif op in ('Exists', 'DoesNotExist'):
        require(not values, 'NODE_AFFINITY_VALUES')
    elif op in ('Gt', 'Lt'):
        require(len(values) == 1, 'NODE_AFFINITY_VALUES')
        try:
            int(values[0])
        except ValueError:
            raise NodeGateRefused('NODE_AFFINITY_VALUES')
    else:
        raise NodeGateRefused('UNSUPPORTED_NODE_OPERATOR')


def requirement_matches(item, labels):
    key, op, values = item['key'], item['operator'], item.get('values', [])
    present = key in labels
    if op == 'In':
        return present and labels[key] in values
    if op == 'NotIn':
        return not present or labels[key] not in values
    if op == 'Exists':
        return present
    if op == 'DoesNotExist':
        return not present
    if not present:
        return False
    try:
        actual, expected = int(labels[key]), int(values[0])
    except (TypeError, ValueError):
        return False
    return actual > expected if op == 'Gt' else actual < expected


def scheduling(spec):
    require(spec.get('schedulerName', 'default-scheduler') == 'default-scheduler'
            and not spec.get('nodeName'), 'UNSUPPORTED_SCHEDULER_OR_NODE_BINDING')
    selector = spec.get('nodeSelector') or {}
    require(isinstance(selector, dict) and all(isinstance(k, str) and isinstance(v, str)
                                              for k, v in selector.items()), 'NODE_SELECTOR_SHAPE')
    affinity = spec.get('affinity') or {}
    require(isinstance(affinity, dict), 'NODE_AFFINITY_SHAPE')
    node_affinity = affinity.get('nodeAffinity') or {}
    require(isinstance(node_affinity, dict), 'NODE_AFFINITY_SHAPE')
    required = node_affinity.get('requiredDuringSchedulingIgnoredDuringExecution')
    terms = None
    if required is not None:
        require(isinstance(required, dict) and isinstance(required.get('nodeSelectorTerms'), list),
                'NODE_AFFINITY_SHAPE')
        terms = required['nodeSelectorTerms']
        for term in terms:
            require(isinstance(term, dict) and set(term) <= {'matchExpressions', 'matchFields'},
                    'NODE_AFFINITY_SHAPE')
            for key in ('matchExpressions', 'matchFields'):
                items = term.get(key, [])
                require(isinstance(items, list), 'NODE_AFFINITY_SHAPE')
                for item in items:
                    requirement_valid(item, fields=key == 'matchFields')
    tolerations = spec.get('tolerations') or []
    require(isinstance(tolerations, list), 'TOLERATION_SHAPE')
    for t in tolerations:
        require(isinstance(t, dict) and t.get('operator', 'Equal') in ('Equal', 'Exists')
                and t.get('effect', '') in ('', 'NoSchedule', 'NoExecute', 'PreferNoSchedule')
                and all(isinstance(t.get(k, ''), str) for k in ('key', 'value')), 'TOLERATION_SHAPE')
        require(bool(t.get('key')) or t.get('operator') == 'Exists', 'TOLERATION_SHAPE')
        require(t.get('operator') != 'Exists' or not t.get('value'), 'TOLERATION_SHAPE')
    return selector, terms, tolerations


def tolerated(taint, tolerations):
    for t in tolerations:
        if t.get('effect') and t['effect'] != taint['effect']:
            continue
        if t.get('key') and t['key'] != taint['key']:
            continue
        if t.get('operator', 'Equal') == 'Exists' or t.get('value', '') == taint.get('value', ''):
            return True
    return False


def exclusions(node, schedule):
    selector, terms, tolerations = schedule
    labels = node['metadata'].get('labels', {})
    require(isinstance(labels, dict), 'NODE_LABEL_SHAPE')
    reasons = []
    if any(labels.get(k) != v for k, v in selector.items()):
        reasons.append('node_selector')
    if terms is not None:
        fields = {'metadata.name': node['metadata']['name']}
        matched = any(
            bool(term.get('matchExpressions') or term.get('matchFields'))
            and all(requirement_matches(r, labels) for r in term.get('matchExpressions', []))
            and all(requirement_matches(r, fields) for r in term.get('matchFields', []))
            for term in terms
        )
        if not matched:
            reasons.append('required_node_affinity')
    taints = node.get('spec', {}).get('taints') or []
    require(isinstance(taints, list), 'NODE_TAINT_SHAPE')
    for t in taints:
        require(isinstance(t, dict) and isinstance(t.get('key'), str)
                and isinstance(t.get('value', ''), str)
                and t.get('effect') in ('NoSchedule', 'NoExecute', 'PreferNoSchedule'), 'NODE_TAINT_SHAPE')
        # Admission can add tolerations absent from the Deployment for standard
        # health/pressure taints. Do not use those as proof of exclusion. Unlike
        # those taints, an explicit unschedulable NoSchedule taint excludes an
        # ordinary Deployment without a matching toleration.
        if t['key'].startswith('node.kubernetes.io/') and not (
                t['key'] == 'node.kubernetes.io/unschedulable' and t['effect'] == 'NoSchedule'):
            continue
        # Transient cordon/health/capacity and soft preferences cannot prove that
        # a future replacement Pod will stay within the preloaded node set.
        if t['effect'] in ('NoSchedule', 'NoExecute') and not tolerated(t, tolerations):
            reasons.append('untolerated_hard_taint')
            break
    return reasons


def assess_nodes(nodes, deployments, expected):
    require(isinstance(nodes, list) and set(deployments) == set(expected), 'NODE_INPUT_SHAPE')
    observed = {}
    for n in nodes:
        m = n.get('metadata', {})
        require(isinstance(m.get('name'), str) and isinstance(m.get('uid'), str)
                and m['name'] not in observed, 'NODE_IDENTITY_SHAPE')
        observed[m['name']] = n
    pinned = {host: uid for p in expected.values() for host, uid in p['nodes'].items()}
    result = {'observed_node_count': len(nodes), 'preloaded_node_count': len(pinned),
              'missing_pinned_nodes': sorted(set(pinned) - set(observed)),
              'additional_nodes': sorted(set(observed) - set(pinned)),
              'uid_mismatches': [], 'unhealthy_pinned_nodes': [], 'role_matrix': {}}
    for host, uid in sorted(pinned.items()):
        if host not in observed:
            continue
        node = observed[host]
        if node['metadata']['uid'] != uid:
            result['uid_mismatches'].append(host)
        conditions = {c['type']: c['status'] for c in node.get('status', {}).get('conditions', [])}
        if conditions.get('Ready') != 'True' or conditions.get('DiskPressure') != 'False':
            result['unhealthy_pinned_nodes'].append(host)
    for key, code in (('missing_pinned_nodes', 'PRELOADED_NODE_MISSING'),
                      ('uid_mismatches', 'PRELOADED_NODE_UID_CHANGED'),
                      ('unhealthy_pinned_nodes', 'PRELOADED_NODE_NOT_HEALTHY')):
        if result[key]:
            raise NodeGateRefused(code, result)
    uncovered = False
    for name, d in sorted(deployments.items()):
        schedule = scheduling(d['spec']['template']['spec'])
        row = {'potential_nodes': [], 'uncovered_nodes': [], 'excluded_nodes': {}}
        for host, node in sorted(observed.items()):
            reasons = exclusions(node, schedule)
            if reasons:
                row['excluded_nodes'][host] = reasons
            else:
                row['potential_nodes'].append(host)
                if host not in expected[name]['nodes']:
                    row['uncovered_nodes'].append(host)
                    uncovered = True
        result['role_matrix'][name] = row
    if uncovered:
        raise NodeGateRefused('SCHEDULABLE_NODE_NOT_PRELOADED', result)
    result['status'] = 'ROLE_NODE_COVERAGE_ACCEPTED'
    return result
