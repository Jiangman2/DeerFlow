"""Prepare one scheduler-concurrency edit; no file, network or rollout actions."""
import re
import yaml


class Refused(ValueError):
    pass


def need(value, code):
    if not value:
        raise Refused(code)


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        need(isinstance(key, (str, int, float, bool, type(None))), 'YAML_MAPPING_KEY_INVALID')
        need(key not in result, 'YAML_DUPLICATE_KEY')
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def parse(text):
    try:
        result = yaml.load(text, Loader=UniqueLoader)
    except Refused:
        raise
    except Exception:
        raise Refused('CONFIGURATION_YAML_INVALID') from None
    need(isinstance(result, dict), 'CONFIGURATION_MAPPING_REQUIRED')
    return result


def differences(before, after, path=(), depth=0, budget=None):
    budget = [10000] if budget is None else budget
    budget[0] -= 1
    need(depth < 32 and budget[0] >= 0, 'CONFIGURATION_STRUCTURE_BOUND')
    if type(before) is not type(after):
        return [path]
    if isinstance(before, dict):
        result = []
        for key in set(before) | set(after):
            result += [path + (key,)] if key not in before or key not in after else differences(before[key], after[key], path + (key,), depth + 1, budget)
        return result
    if isinstance(before, list):
        if len(before) != len(after):
            return [path]
        return [change for index, (old, new) in enumerate(zip(before, after))
                for change in differences(old, new, path + (index,), depth + 1, budget)]
    return [] if before == after else [path]


def replace_concurrency(raw, expected_current):
    need(isinstance(raw, bytes) and 0 < len(raw) <= 1048576, 'CONFIGURATION_BYTES_BOUND')
    need(type(expected_current) is int and 1 <= expected_current <= 32, 'CURRENT_CONCURRENCY_INVALID')
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise Refused('CONFIGURATION_UTF8_REQUIRED') from None
    before = parse(text)
    section = before.get('scheduler')
    need(isinstance(section, dict), 'SCHEDULER_MAPPING_REQUIRED')
    current = section.get('max_concurrent_runs', 3)
    need(type(current) is int and current == expected_current, 'CURRENT_CONCURRENCY_MISMATCH')
    need(current <= 16, 'CONCURRENCY_DECREASE_NOT_AUTHORIZED')
    if current == 16:
        return raw
    headers = list(re.finditer(r'^scheduler:(?:[ \t]+&[A-Za-z0-9_-]+)?[ \t]*(?:#[^\r\n]*)?\r?$', text, re.M))
    need(len(headers) == 1, 'BLOCK_SCHEDULER_MAPPING_REQUIRED')
    header = headers[0]
    start = header.end()
    next_section = re.search(r'^[^\s#]', text[start:], re.M)
    end = start + next_section.start() if next_section else len(text)
    block = text[start:end]
    limits = list(re.finditer(r'^( +)max_concurrent_runs:[ \t]*([0-9]+)([ \t]*(?:#[^\r\n]*)?\r?)$', block, re.M))
    if 'max_concurrent_runs' in section:
        need(len(limits) == 1 and int(limits[0][2]) == current, 'BLOCK_CONCURRENCY_VALUE_REQUIRED')
        found = limits[0]
        begin, finish = start + found.start(2), start + found.end(2)
        candidate = text[:begin] + '16' + text[finish:]
    else:
        need(not limits, 'UNEXPECTED_CONCURRENCY_VALUE')
        newline = '\r\n' if '\r\n' in text else '\n'
        # Header.end includes a CR, but not its LF. Insert after the full line.
        insertion = text.find('\n', header.start())
        need(insertion >= 0, 'SCHEDULER_HEADER_LINE_END_REQUIRED')
        indentation = re.search(r'^( +)[A-Za-z_]', block, re.M)
        indent = indentation[1] if indentation else '  '
        candidate = text[:insertion+1] + indent + 'max_concurrent_runs: 16' + newline + text[insertion+1:]
    after = parse(candidate)
    need(differences(before, after) == [('scheduler', 'max_concurrent_runs')], 'CONFIGURATION_CHANGE_SCOPE_EXCEEDED')
    need(after['scheduler']['max_concurrent_runs'] == 16, 'TARGET_CONCURRENCY_INVALID')
    return candidate.encode('utf-8')
