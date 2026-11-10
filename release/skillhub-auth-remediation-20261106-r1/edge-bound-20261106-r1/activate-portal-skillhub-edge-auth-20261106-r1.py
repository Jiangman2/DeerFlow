"""Prepare the pinned SkillHub edge guard; unbound native receipt means NO ACTION.

Python 3.7+. This controller never changes a Deployment, creates a Pod, or uses
real credentials. A separately sealed native-server acceptance receipt is an
absolute prerequisite to publishing the API bearer edge bypass. The package
generator embeds the pure transformation module for the operator's one-file
tool. No production configuration text is packaged or printed.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

import types
policy = types.ModuleType("skillhub_edge_policy")
exec('"""Pure, insertion-only SkillHub Nginx auth planner; no production operations.\n\nOnly the currently observed ConfigMap and live file are accepted by the public\nplanner. The stale mounted template is never an input. This module cannot read\nfiles, run commands, contact Kubernetes, patch a ConfigMap or reload Nginx.\n\nThe returned candidate is NOT authorization to activate. A future caller must\nfirst establish deployed native SkillHub authentication on every backend, replay\nthe seven pinned substitutions with the existing container\'s sed, run nginx -t,\nCAS both current configuration identities, and perform runtime/browser checks.\nNever publish the bearer edge bypass before native backend authentication passes.\n\nLexer/parser and literal startup validation derive from the audited PPT entry\nresume tool; the existing retirement guard is preserved byte-for-byte. The startup parser retains the exact reviewed seven-command requirement. Python 3.7 compatible; no CLI.\n"""\nimport hashlib\nimport json\nimport re\n\nCURRENT_TEMPLATE_SHA = \'85a9aee439cee462aaf81788594e55526ece023cbb4d63ff18b92276032dcbb4\'\nCURRENT_LIVE_SHA = \'139221024e43ad49e706138ae740093c1eefdb46f7f807094d10a39e1ad4a755\'\nSTALE_MOUNT_SHA = \'070b256f563a5bb712183feffaec21f8f41154fe7ca2c6a7369a60214233e5dd\'\nMARKER = \'portal-skillhub-auth-edge-20261106-r1\'\nPPT_VARIABLE = \'$portal_ppt_agent_new_run_retired_20261104\'\nBEARER_VARIABLE = \'$skillhub_bearer_request\'\nBROWSER_AUTH = \'/_internal/skillhub/browser-auth\'\nAPI_AUTH = \'/_internal/skillhub/api-auth\'\nASSERTION = \'/_internal/skillhub/session-assertion\'\nBOOTSTRAP = \'/platform/skillhub/api/v1/auth/session/bootstrap\'\nSSE = \'/platform/skillhub/api/web/notifications/sse\'\nAPI_PREFIX = \'/platform/skillhub/api/\'\nPAGE_PREFIX = \'/platform/skillhub/\'\nOLD_ROUTE_HASHES = {\n    (\'location\', \'=\', \'/platform/skillhub\'): \'b1fdb13d442259a0c2af484976e7f35c1a034c19eb95f1b50ff79c0a05cae4ce\',\n    (\'location\', \'=\', ASSERTION): \'9d337f49eb8430e47b1e52f472e92cca9522cefbc3799e0c92d231b119a6fb99\',\n    (\'location\', \'=\', BOOTSTRAP): \'a8fa321a173cdac6344a9afa04887be75ac3d79738da63402d6a8632f8f14b58\',\n    (\'location\', \'=\', SSE): \'ef29bd0d875e56f11e010be20bfbcecfc4e62130b1f4cdbde1c8f48a47abb198\',\n    (\'location\', \'^~\', PAGE_PREFIX): \'78599fe87aab294207f692816035220ee95d0890f3ea1865750aa4a40b1975ee\',\n}\n\n\ndef require(ok, code):\n    if not ok:\n        raise ValueError(code)\n\n\ndef sha(raw):\n    return hashlib.sha256(raw).hexdigest()\n\n\ndef lex(text):\n    tokens, i = [], 0\n    while i < len(text):\n        if text[i].isspace():\n            i += 1\n            continue\n        if text[i] == "#":\n            end = text.find("\\n", i)\n            i = len(text) if end < 0 else end + 1\n            continue\n        start = i\n        if text[i] in "{};":\n            i += 1\n        elif text[i] in "\\"\'":\n            quote = text[i]\n            i += 1\n            while i < len(text) and text[i] != quote:\n                i += 2 if text[i] == "\\\\" else 1\n            require(i < len(text), "unclosed_nginx_quote")\n            i += 1\n        else:\n            while i < len(text) and not text[i].isspace() and text[i] not in "{};#":\n                if text.startswith("${", i):\n                    end = text.find("}", i)\n                    require(end >= 0, "unclosed_nginx_variable")\n                    i = end + 1\n                else:\n                    i += 2 if text[i] == "\\\\" else 1\n        tokens.append((text[start:i], start, i))\n    return tokens\n\ndef parse_blocks(text):\n    root = {"header": [], "children": [], "directives": []}\n    stack, pending = [root], []\n    for value, start, end in lex(text):\n        if value == "{":\n            require(bool(pending), "missing_block_header")\n            node = {"header": [x[0] for x in pending], "start": pending[0][1], "open": start, "children": [], "directives": []}\n            stack[-1]["children"].append(node)\n            stack.append(node)\n            pending = []\n        elif value == "}":\n            require(len(stack) > 1 and not pending, "unbalanced_nginx_block")\n            stack.pop()["close"] = start\n        elif value == ";":\n            stack[-1]["directives"].append([x[0] for x in pending])\n            pending = []\n        else:\n            pending.append((value, start, end))\n    require(len(stack) == 1 and not pending, "incomplete_nginx_config")\n    return root\n\n"""Parse the observed Nginx startup into literal, pure sed substitutions.\n\nNo files, subprocesses, network or shell execution. Python 3.7 compatible.\nThe public entry point always uses the production evidence identities below.\n"""\nimport hashlib\nimport json\nimport re\nimport shlex\n\nSTARTUP_SHA = \'c10f308cc45d31d3861799e706e5adef53e52f9c78d2f171bf47f13f29424cc3\'\nSTARTUP_SHA256 = STARTUP_SHA\nEXPRESSION_IDENTITIES = (\n    (104, \'c8075a28f07dd1bb01130655f71f5b70dd0f7baa652d00664c389e98afda9a01\'),\n    (82, \'2bd9315ff4063890fa8866e9f978e8229713d253dca7e5f3c756259947d480bb\'),\n    (85, \'634bbc8cef07b7703ef17524d3ce1e10b8a96393ef82d4b7252259153a8646a1\'),\n    (94, \'0d8927f8a540c08f427c08c3f1634ed8374bc905372c52cd359a4b62f46f4d82\'),\n    (72, \'7bb56de719c6fd4c466c7607ecf1c10840249eadbfa6a063b68dd8e411578cc2\'),\n    (74, \'edb9873122fe92cd1bcc94a4d5f69b0ca3e25eddfbb43d89fb97f25be9571a5d\'),\n    (80, \'afc30173babdc20ef307b6217fe6de5e4d79f7d90eeba6f49d4fe514523044e9\'),\n)\nTEMPLATE = \'/etc/nginx/nginx.conf.template\'\nLIVE = \'/tmp/nginx.conf\'\n\n\ndef _require(ok, code):\n    if not ok:\n        raise ValueError(code)\n\n\ndef startup_digest(container):\n    """Use exactly the canonical JSON scheme used by the readonly probe."""\n    value = {\'command\': container.get(\'command\'), \'args\': container.get(\'args\')}\n    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(\',\', \':\')).encode()).hexdigest()\n\n\ndef _literal_commands(script):\n    """Accept only independent simple commands, with literal complete words.\n\n    A quoted word cannot concatenate with another word/quote. Double quotes\n    support POSIX backslash handling, but all dollar/backtick syntax is refused\n    there (including escaped forms). Unquoted words are a safe ASCII subset.\n    Semicolons/newlines are separators; shell operators and continuations are\n    never interpreted. Comments are intentionally outside this grammar.\n    """\n    _require(isinstance(script, str) and len(script.encode()) <= 8192, \'startup_text_bound\')\n    _require(not any(ord(c) < 32 and c not in \'\\n\\t\' for c in script), \'startup_control_character\')\n    commands, words, i = [], [], 0\n    while i < len(script):\n        c = script[i]\n        if c in \' \\t\':\n            i += 1\n            continue\n        if c in \'\\n;\':\n            _require(c != \';\' or bool(words), \'startup_empty_shell_command\')\n            if words:\n                commands.append(words)\n                words = []\n            i += 1\n            continue\n        if c in "\'\\"":\n            quote = c\n            i += 1\n            value = []\n            while i < len(script) and script[i] != quote:\n                c = script[i]\n                _require(c not in \'\\n\\r\\0`\', \'startup_quoted_word_unsupported\')\n                if quote == \'"\':\n                    _require(c != \'$\', \'startup_shell_expansion_forbidden\')\n                    if c == \'\\\\\':\n                        _require(i + 1 < len(script), \'startup_unclosed_quote\')\n                        following = script[i + 1]\n                        _require(following not in \'\\n\\r$`\', \'startup_shell_expansion_forbidden\')\n                        # POSIX double quotes remove backslash only before\n                        # dollar, backtick, double quote, backslash or newline.\n                        # Dollar/backtick/newline are already refused above.\n                        if following in \'\\\\"\':\n                            value.append(following)\n                        else:\n                            value.extend((\'\\\\\', following))\n                        i += 2\n                        continue\n                value.append(c)\n                i += 1\n            _require(i < len(script) and script[i] == quote, \'startup_unclosed_quote\')\n            i += 1\n            _require(i == len(script) or script[i] in \' \\t\\n;\', \'startup_word_concatenation_forbidden\')\n            words.append(\'\'.join(value))\n        else:\n            start = i\n            while i < len(script) and script[i] not in \' \\t\\n;\':\n                _require(re.fullmatch(r\'[A-Za-z0-9_./:+,=-]\', script[i]) is not None,\n                         \'startup_nonliteral_shell_word\')\n                i += 1\n            words.append(script[start:i])\n        _require(len(words) <= 8 and len(commands) <= 9, \'startup_command_bound\')\n    if words:\n        commands.append(words)\n    _require(len(commands) == 9, \'startup_command_count\')\n    return commands\n\n\ndef validate_substitution(expression):\n    """Prove a single sed s-command; never permit addresses/extra commands.\n\n    Patterns and replacements remain sed\'s own syntax. Escaped delimiter\n    handling is byte-for-byte preserved. Only g and positive occurrence-number\n    flags are permitted; e/w/r, output files, newlines and trailing programs are\n    rejected. Invalid regular expressions will still fail in the bounded replay.\n    """\n    _require(isinstance(expression, str) and 4 <= len(expression.encode()) <= 512,\n             \'sed_expression_bound\')\n    _require(not any(ord(c) < 32 or ord(c) == 127 for c in expression), \'sed_control_character\')\n    _require(expression[0] == \'s\' and expression[1] in \'/|#@:%!~,_=+\', \'sed_substitution_required\')\n    delimiter = expression[1]\n    i = 2\n    fields = []\n    for unused in range(2):\n        start = i\n        while i < len(expression):\n            if expression[i] == \'\\\\\':\n                _require(i + 1 < len(expression), \'sed_unfinished_escape\')\n                i += 2\n            elif expression[i] == delimiter:\n                fields.append(expression[start:i])\n                i += 1\n                break\n            else:\n                i += 1\n        else:\n            raise ValueError(\'sed_unterminated_substitution\')\n    _require(bool(fields[0]), \'sed_previous_pattern_forbidden\')\n    flags = expression[i:]\n    _require(re.fullmatch(r\'(?:g|[1-9][0-9]{0,5}g?|g[1-9][0-9]{0,5})?\', flags) is not None,\n             \'sed_unsafe_or_unknown_flags\')\n    return expression\n\n\ndef _parse_startup(container, expected_startup_sha256, expected_expressions):\n    """Internal test seam. Production must call parse_startup(), not this."""\n    _require(isinstance(container, dict), \'startup_container_required\')\n    _require(container.get(\'command\') in ([\'sh\', \'-c\'], [\'/bin/sh\', \'-c\']), \'startup_command_requires_review\')\n    args = container.get(\'args\')\n    _require(isinstance(args, list) and len(args) == 1 and isinstance(args[0], str), \'startup_args_required\')\n    _require(startup_digest(container) == expected_startup_sha256, \'startup_identity_changed\')\n    _require(len(expected_expressions) == 7, \'seven_expression_identities_required\')\n    commands = _literal_commands(args[0])\n    _require(commands[0] == [\'cp\', TEMPLATE, LIVE], \'startup_copy_requires_review\')\n    _require(commands[-1] == [\'exec\', \'nginx\', \'-c\', LIVE, \'-g\', \'daemon off;\'],\n             \'startup_nginx_exec_requires_review\')\n    for command in commands[1:-1]:\n        _require(len(command) == 4 and command[:2] == [\'sed\', \'-i\'] and command[-1] == LIVE,\n                 \'startup_sed_command_requires_review\')\n\n    # The production expression hashes came from this exact shlex projection.\n    # Do not silently substitute the output of a different shell-word parser.\n    lexer = shlex.shlex(args[0], posix=True, punctuation_chars=\';&|()<>\')\n    lexer.whitespace_split = True\n    projected = list(lexer)\n    _require(len(projected) <= 200, \'startup_projection_bound\')\n    projected = [word for word in projected if word != \';\']\n    literal = [word for command in commands for word in command]\n    _require(projected == literal, \'startup_projection_literal_mismatch\')\n\n    expressions = []\n    for command, identity in zip(commands[1:-1], expected_expressions):\n        expression = command[2]\n        raw = expression.encode()\n        _require(len(raw) == identity[0] and hashlib.sha256(raw).hexdigest() == identity[1],\n                 \'startup_sed_identity_changed\')\n        expressions.append(validate_substitution(expression))\n    return tuple(expressions)\n\n\ndef parse_startup(container):\n    """Return exactly seven reviewed literal sed expressions, without execution."""\n    return _parse_startup(container, STARTUP_SHA, EXPRESSION_IDENTITIES)\n\n\n\ndef walk(node):\n    yield node\n    for child in node[\'children\']:\n        for descendant in walk(child):\n            yield descendant\n\n\ndef block(text, node):\n    return text[node[\'open\']:node[\'close\'] + 1]\n\n\ndef one(nodes, code):\n    require(len(nodes) == 1, code)\n    return nodes[0]\n\n\ndef auth_location(path, api=False):\n    # Same directives as the reviewed k8s/local-pvc nginx delta. Static,\n    # path-qualified Gateway URL survives the seven startup substitutions.\n    bypass = \'\\n            if (\' + BEARER_VARIABLE + \') { return 204; }\' if api else \'\'\n    return \'\\n        location = \' + path + \' {\\n            internal;\' + bypass + """\n            proxy_pass http://gateway:8001/api/v1/auth/me;\n            proxy_pass_request_body off;\n            proxy_set_header Content-Length "";\n            proxy_set_header Cookie $http_cookie;\n            proxy_set_header X-DeerFlow-Internal-Token "";\n            proxy_set_header X-DeerFlow-Owner-User-Id "";\n            proxy_set_header Forwarded "";\n            proxy_set_header X-Forwarded-Port "";\n            proxy_hide_header X-Powered-By;\n            proxy_hide_header X-AspNet-Version;\n            proxy_hide_header X-AspNetMvc-Version;\n            proxy_set_header X-Forwarded-Host $http_host;\n            proxy_set_header Host $http_host;\n            proxy_set_header X-Forwarded-Proto $forwarded_proto;\n        }\n"""\n\n\ndef _transform(raw, expected_route_hashes=None):\n    """Internal synthetic-test seam; callers must use plan_production_pair."""\n    require(type(raw) is bytes and len(raw) <= 131072 and b\'\\0\' not in raw, \'config_bound\')\n    text = raw.decode(\'utf-8\')\n    require(MARKER not in text and BEARER_VARIABLE not in text\n            and all(x not in text for x in (BROWSER_AUTH, API_AUTH, \'@skillhub_login\')),\n            \'already_guarded_or_conflicting_auth_names\')\n    root = parse_blocks(text)\n    nodes = list(walk(root))\n    require(not any(d[:1] == [\'include\'] for n in nodes for d in n[\'directives\']), \'includes_require_review\')\n    http = one([n for n in root[\'children\'] if n[\'header\'] == [\'http\']], \'one_http_required\')\n    server = one([n for n in http[\'children\'] if n[\'header\'] == [\'server\']\n                  and any(d[:2] == [\'listen\', \'2026\'] for d in n[\'directives\'])], \'one_entry_server_required\')\n    all_routes = [n for n in nodes if n[\'header\'][:1] == [\'location\']\n                  and any(\'skillhub\' in word.lower() for word in n[\'header\'])]\n    require(len(all_routes) == 5 and len({tuple(n[\'header\']) for n in all_routes}) == 5\n            and {tuple(n[\'header\']) for n in all_routes} == set(OLD_ROUTE_HASHES)\n            and all(n in server[\'children\'] for n in all_routes), \'existing_skillhub_routes_require_review\')\n    routes = {tuple(n[\'header\']): n for n in all_routes}\n    if expected_route_hashes is not None:\n        require(set(expected_route_hashes) == set(routes), \'route_inventory_changed\')\n        for header, digest in expected_route_hashes.items():\n            require(sha(block(text, routes[header]).encode()) == digest, \'existing_route_hash_changed\')\n    assertion = routes[(\'location\', \'=\', ASSERTION)]\n    bootstrap = routes[(\'location\', \'=\', BOOTSTRAP)]\n    sse = routes[(\'location\', \'=\', SSE)]\n    page = routes[(\'location\', \'^~\', PAGE_PREFIX)]\n    require([\'internal\'] in assertion[\'directives\'], \'assertion_must_remain_internal\')\n    require([d for d in bootstrap[\'directives\'] if d[:1] == [\'auth_request\']]\n            == [[\'auth_request\', ASSERTION]], \'bootstrap_auth_changed\')\n    for node in (page, sse):\n        require(not node[\'children\'] and not any(d[:1] in ([\'auth_request\'], [\'return\'], [\'error_page\'])\n                for d in node[\'directives\']), \'existing_page_or_sse_auth_requires_review\')\n        require([d for d in node[\'directives\'] if d[:1] == [\'proxy_pass\']]\n                == [[\'proxy_pass\', \'http://skillhub-web:80\']], \'skillhub_upstream_changed\')\n        require([\'proxy_set_header\', \'X-Forwarded-Proto\', \'$forwarded_proto\'] in node[\'directives\'],\n                \'forwarded_proto_binding_changed\')\n        for directive in (\n            [\'proxy_set_header\', \'Forwarded\', \'""\'],\n            [\'proxy_set_header\', \'X-Forwarded-Port\', \'""\'],\n            [\'proxy_set_header\', \'X-Forwarded-Host\', \'$http_host\'],\n            [\'proxy_set_header\', \'X-SkillHub-Identity\', \'""\'],\n            [\'proxy_set_header\', \'X-SkillHub-Signature\', \'""\'],\n            [\'proxy_hide_header\', \'X-Powered-By\'],\n            [\'proxy_hide_header\', \'X-AspNet-Version\'],\n            [\'proxy_hide_header\', \'X-AspNetMvc-Version\'],\n        ):\n            require(directive in node[\'directives\'], \'existing_header_defense_missing\')\n    require(PPT_VARIABLE in text and \'PPT_AGENT_RETIRED\' in text, \'existing_ppt_guard_required\')\n\n    # Insert only: all original config bytes, routes, headers and PPT guards\n    # remain in their original order. The API prefix clones the existing page\n    # body so its header defenses and upstream behavior are preserved exactly.\n    mapping = \'\\n    # BEGIN \' + MARKER + """\n    map $http_authorization $skillhub_bearer_request {\n        default 0;\n        "~*^Bearer[ ]+.+$" 1;\n    }\n    # END """ + MARKER + \'\\n\'\n    internal = \'\\n        # BEGIN \' + MARKER + auth_location(BROWSER_AUTH) + auth_location(API_AUTH, True)\n    internal += """\n        location @skillhub_login {\n            return 302 /login;\n        }\n        # END """ + MARKER + \'\\n        \'\n    api = (\'\\n        # BEGIN \' + MARKER + \'\\n        location ^~ \' + API_PREFIX + \' {\\n\'\n           + \'            auth_request \' + API_AUTH + \';\'\n           + text[page[\'open\'] + 1:page[\'close\']] + \'}\\n        # END \' + MARKER + \'\\n        \')\n    insertions = [\n        (sse[\'open\'] + 1, \'\\n            auth_request \' + API_AUTH + \';\'),\n        (page[\'open\'] + 1, \'\\n            auth_request \' + BROWSER_AUTH + \';\\n            error_page 401 = @skillhub_login;\'),\n        (assertion[\'start\'], internal),\n        (page[\'start\'], api),\n        # Append AFTER every existing http directive, including map_hash_*.\n        (http[\'close\'], mapping),\n    ]\n    require(len({offset for offset, _ in insertions}) == len(insertions), \'insertion_overlap\')\n    result = text\n    for offset, value in sorted(insertions, reverse=True):\n        result = result[:offset] + value + result[offset:]\n    # Reverse every insertion at its exact computed offset. This is stronger\n    # than searching/replacing marker strings and proves all old bytes survive.\n    shift, adjusted = 0, []\n    for offset, value in sorted(insertions):\n        adjusted.append((offset + shift, value))\n        shift += len(value)\n    restored = result\n    for offset, value in reversed(adjusted):\n        require(restored[offset:offset + len(value)] == value, \'insertion_roundtrip_failed\')\n        restored = restored[:offset] + restored[offset + len(value):]\n    require(restored == text, \'unrelated_bytes_changed\')\n    require(len(result.encode()) <= 131072, \'candidate_config_bound\')\n    parse_blocks(result)\n    return result.encode(), {\n        \'insertions\': len(insertions), \'existing_bytes_preserved\': True,\n        \'bootstrap_unchanged\': True, \'ppt_guard_preserved\': True,\n        \'new_locations\': 4, \'new_maps\': 1, \'existing_auth_locations_changed\': 2,\n        \'raw_configuration_exported\': False,\n    }\n\n\ndef plan_production_pair(current_template, live, container):\n    """Bind CURRENT ConfigMap/live bytes and the exact observed startup.\n\n    Return private candidate bytes and literal replay inputs. Never print or\n    publish these raw values. There is deliberately no activation-ready result.\n    Native backend rollout/HTTP acceptance and replay/nginx -t remain external\n    prerequisites; receipt provenance must be checked by the future controller.\n    """\n    require(type(current_template) is bytes and sha(current_template) == CURRENT_TEMPLATE_SHA,\n            \'current_configmap_hash_changed_or_stale_mount\')\n    require(type(live) is bytes and sha(live) == CURRENT_LIVE_SHA, \'current_live_hash_changed\')\n    expressions = parse_startup(container)\n    require(len(expressions) == 7, \'seven_substitutions_required\')\n    candidate_template, template_summary = _transform(current_template, OLD_ROUTE_HASHES)\n    candidate_live, live_summary = _transform(live, OLD_ROUTE_HASHES)\n    return {\n        \'schema\': \'portal-skillhub-nginx-auth-plan.v1\',\n        \'current_template\': current_template, \'current_live\': live,\n        \'candidate_template\': candidate_template, \'candidate_live\': candidate_live,\n        \'startup_sed_expressions\': expressions,\n        \'summary\': {\n            \'schema\': \'portal-skillhub-nginx-auth-plan-summary.v1\',\n            \'before_template_sha256\': sha(current_template), \'before_live_sha256\': sha(live),\n            \'candidate_template_sha256\': sha(candidate_template), \'candidate_live_sha256\': sha(candidate_live),\n            \'startup_sha256\': STARTUP_SHA, \'pure_substitutions\': 7,\n            \'template\': template_summary, \'live\': live_summary,\n            \'stale_mounted_template_used\': False, \'ready_for_activation\': False,\n            \'native_server_auth_acceptance_required\': True,\n            \'startup_replay_verified\': False, \'nginx_syntax_verified\': False,\n            \'production_runtime_accepted\': False,\n        },\n    }\n\n\ndef verify_startup_replay(plan, original_replayed, candidate_replayed):\n    """Check supplied bounded sed replay outputs; never execute startup/shell.\n\n    This certifies only byte reproduction. It cannot authorize deployment or\n    establish native backend authentication, Nginx syntax, or HTTP acceptance.\n    """\n    require(type(plan) is dict and plan.get(\'schema\') == \'portal-skillhub-nginx-auth-plan.v1\', \'plan_schema\')\n    expressions = plan.get(\'startup_sed_expressions\')\n    require(isinstance(expressions, tuple) and tuple((len(x.encode()), sha(x.encode())) for x in expressions)\n            == EXPRESSION_IDENTITIES, \'plan_startup_expressions_changed\')\n    for expression in expressions:\n        validate_substitution(expression)\n    template, live = plan[\'current_template\'], plan[\'current_live\']\n    require(sha(template) == CURRENT_TEMPLATE_SHA and sha(live) == CURRENT_LIVE_SHA, \'plan_before_identity\')\n    require(_transform(template, OLD_ROUTE_HASHES)[0] == plan[\'candidate_template\']\n            and _transform(live, OLD_ROUTE_HASHES)[0] == plan[\'candidate_live\'], \'candidate_plan_changed\')\n    require(original_replayed == live, \'original_startup_does_not_reproduce_live\')\n    require(candidate_replayed == plan[\'candidate_live\'], \'startup_changes_skillhub_or_ppt_guard\')\n    return {\'startup_replay_verified\': True, \'startup_shell_executed\': False,\n            \'ready_for_activation\': False, \'native_server_auth_acceptance_required\': True,\n            \'nginx_syntax_verified\': False, \'production_runtime_accepted\': False}\n', policy.__dict__)

NATIVE_RECEIPT_BINDING = {'bytes': 7130, 'sha256': '29e159302c129098c28786ab4dd8267724d31ebe28e37f02293186b31d033c38', 'operation': '25f363cb9f254e00b9f9dd196b2ade97'}
NATIVE_RECEIPT = '/var/lib/portal-skillhub-registry-auth-activation-20261106-r2/REPORT.json'
DIRECTORY = '/var/lib/portal-skillhub-edge-auth-activation-20261106-r1'
NS = 'deer-flow'
ENTRY = 'nginx-security-heai-20261012-r1'
ENTRY_BINDING = {'uid': 'ce96a382-140e-4baf-9a85-d623b7df6e3d', 'generation': 2,
                 'spec_sha256': 'f065f125b1f8417010f3108ae682de3739c4a267040e6d803f26ce78363f6141'}
POD = 'nginx-security-heai-20261012-r1-5d66699595-cpq85'
POD_UID = 'a795d8c9-c487-4245-80a6-d7869f603a44'
CM = 'deer-flow-nginx-baseline-20261027201316'
CM_UID = '0c717077-deb9-428e-ade2-f01fa7d8dd47'
CM_DATA_SHA = 'e1a768366017fc266397377619b27ecd5a3013e3e074a71ab5d9734dd4a49654'
SERVER = 'skillhub-server'
SERVER_UID = '009babb5-b084-4397-a5ee-56a4b4b91032'
SERVER_IMAGE = 'deerflow.local/skillhub-server@sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68'
SERVER_JAR = '342d0e02914d790a1c01a845cce4047af8b634fd34ecef0b01a66d5425fe7e3f'
SERVER_CONFIG = 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7'
SERVER_SERVICE = ('9c0ec531-1b1d-4c4d-8616-e016cf3b3725', '42fc392e599d754e93978aba66dcf7c990be0151e68937562ce3bf658eff9ad1')
SERVICE_IDENTITIES = {
    'nginx': ('968e2c68-6635-4e60-9eba-9b13ae10647c', '4466eebc5928640ecee0982ed1b90131857897e1a7a59a8e7deee6381c1a8b67'),
    'nginx-nodeport': ('5c200684-780b-4559-897c-39d3a4664afa', 'd5c784d31129ffd4c77e67c2d613fac4717435b129d866e6ef2c8708673ffab1'),
}
PROTECTED = ('frontend', 'gateway', 'provisioner', 'ppt-master-runner', 'nginx', ENTRY, 'skillhub-web')
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
SUCCESS = 'SKILLHUB_EDGE_AUTH_ACCEPTED_BROWSER_PENDING'
INVALID_BEARER = 'Authorization: Bearer invalid-synthetic-registry-auth-verification'
NATIVE_CHECKS = [
    {'path': '/api/v1/health', 'fabricated_bearer': False, 'status': 200},
    {'path': '/api/web/labels', 'fabricated_bearer': False, 'status': 401},
    {'path': '/api/web/skills?page=0&size=1', 'fabricated_bearer': False, 'status': 401},
    {'path': '/api/web/labels', 'fabricated_bearer': True, 'status': 401},
    {'path': '/api/web/skills?page=0&size=1', 'fabricated_bearer': True, 'status': 401},
]
EDGE_CHECKS = [
    {'path': '/health', 'status': 200},
    {'path': '/platform/skillhub/', 'status': 302, 'redirect': '/login'},
    {'path': '/platform/skillhub/api/web/labels', 'status': 401},
    {'path': '/platform/skillhub/api/web/skills?page=0&size=1', 'status': 401},
    {'path': '/platform/skillhub/api/web/notifications/sse', 'status': 401},
    {'path': '/platform/skillhub/api/v1/auth/session/bootstrap', 'status': 401},
    {'path': '/platform/skillhub/api/web/labels', 'status': 401, 'fabricated_bearer': True},
    {'path': '/platform/skillhub/api/web/skills?page=0&size=1', 'status': 401, 'fabricated_bearer': True},
    {'path': '/platform/skillhub/api/web/notifications/sse', 'status': 401, 'fabricated_bearer': True},
]


class Refused(Exception):
    pass


def check(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def binding(value):
    return {'uid': value['metadata']['uid'], 'generation': value['metadata'].get('generation'),
            'spec_sha256': sha(canonical(value['spec']))}


def converged(value):
    m, s, t = value['metadata'], value['spec'], value.get('status', {})
    n = s.get('replicas')
    return (type(n) is int and n > 0 and not m.get('deletionTimestamp') and not s.get('paused')
            and t.get('observedGeneration') == m.get('generation')
            and all(t.get(k, 0) == n for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and t.get('terminatingReplicas', 0) == 0)


def ready(pod):
    return (not pod['metadata'].get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running'
            and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in pod['status'].get('conditions', [])))


def pod_identity(pod):
    statuses = pod.get('status', {}).get('containerStatuses', [])
    check(ready(pod) and len(statuses) == 1 and statuses[0].get('ready')
          and statuses[0].get('state', {}).get('running') and statuses[0].get('containerID'), 'POD_RUNTIME_REQUIRED')
    c = statuses[0]
    return {'uid': pod['metadata']['uid'], 'spec_sha256': sha(canonical(pod['spec'])),
            'container_id_sha256': sha(c['containerID'].encode()), 'image_id': c.get('imageID'),
            'restart_count': c.get('restartCount'), 'running_since': c['state']['running'].get('startedAt')}


def validate_receipt(receipt, pin):
    check(receipt.get('schema') == 'portal-skillhub-registry-auth-activation.v1'
          and receipt.get('status') == 'SKILLHUB_REGISTRY_AUTH_RUNTIME_ACCEPTED_EDGE_PENDING'
          and receipt.get('operation') == pin['operation'], 'NATIVE_ACCEPTANCE_REQUIRED')
    for flag in ('image_only_patch_verified', 'stable_readback', 'service_endpoints_verified',
                 'unrelated_deployments_preserved', 'temporary_pod_absent'):
        check(receipt.get(flag) is True, 'NATIVE_ACCEPTANCE_INCOMPLETE_' + flag)
    check(receipt.get('patch_request_count') == 1 and receipt.get('installed_arm64_jar_check') == 'PASS'
          and receipt.get('native_http_checks') == NATIVE_CHECKS, 'NATIVE_HTTP_ACCEPTANCE_REQUIRED')
    after = receipt.get('after', {})
    check(set(after) == {'uid', 'generation', 'spec_sha256'} and after['uid'] == SERVER_UID
          and after['generation'] == 15 and re.fullmatch('[0-9a-f]{64}', after['spec_sha256']), 'NATIVE_SERVER_BINDING')
    protected = receipt.get('protected_before', {})
    check(set(protected) == set(PROTECTED) and protected.get(ENTRY) == ENTRY_BINDING, 'NATIVE_PROTECTED_BINDINGS')
    for row in protected.values():
        check(set(row) == {'uid', 'generation', 'spec_sha256'} and isinstance(row['uid'], str)
              and type(row['generation']) is int and re.fullmatch('[0-9a-f]{64}', row['spec_sha256']), 'NATIVE_PROTECTED_ROW')
    runtime = receipt.get('runtime', {})
    check(runtime.get('jar_sha256') == SERVER_JAR and runtime.get('image') == SERVER_IMAGE
          and runtime.get('node') == 'datax-01' and re.fullmatch(r'skillhub-server-[a-z0-9]+-[a-z0-9]+', runtime.get('pod', ''))
          and re.fullmatch(r'[0-9a-f-]{36}', runtime.get('uid', '')), 'NATIVE_RUNTIME_BINDING')
    return receipt


def validate_pin(pin):
    check(isinstance(pin, dict) and set(pin) == {'bytes', 'sha256', 'operation'}, 'NATIVE_RECEIPT_NOT_BOUND_NO_CHANGE')
    check(type(pin['bytes']) is int and 0 < pin['bytes'] <= 262144
          and isinstance(pin['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pin['sha256'])
          and isinstance(pin['operation'], str) and re.fullmatch('[0-9a-f]{32}', pin['operation']), 'NATIVE_RECEIPT_BINDING_INVALID')


def private_read(path, limit):
    """Read under no-follow root-owned directory FDs, detecting replacement."""
    check(path.startswith('/') and all(x not in ('', '.', '..') for x in path.split('/')[1:]), 'PRIVATE_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.split('/')[1:]
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            s = os.fstat(fd)
            check(s.st_uid == 0 and s.st_mode & 0o022 == 0, 'PRIVATE_PARENT_REQUIRED')
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            check(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                  and before.st_mode & 0o077 == 0 and 0 < before.st_size <= limit, 'PRIVATE_FILE_REQUIRED')
            chunks, count = [], 0
            while count <= limit:
                piece = os.read(file_fd, min(65536, limit + 1 - count))
                if not piece:
                    break
                chunks.append(piece)
                count += len(piece)
            raw = b''.join(chunks)
            after = os.fstat(file_fd)
            linked = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            fields = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns, x.st_mode, x.st_nlink, x.st_uid)
            check(fields(before) == fields(after) == fields(linked) and len(raw) == before.st_size, 'PRIVATE_FILE_CHANGED')
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def read_native_receipt():
    validate_pin(NATIVE_RECEIPT_BINDING)
    raw = private_read(NATIVE_RECEIPT, 262144)
    check(len(raw) == NATIVE_RECEIPT_BINDING['bytes'] and sha(raw) == NATIVE_RECEIPT_BINDING['sha256'], 'NATIVE_RECEIPT_HASH_CHANGED')
    return validate_receipt(json.loads(raw), NATIVE_RECEIPT_BINDING)


FILE_GUARD = r'''
printf 'EDGE_STEP:LIVE_IDENTITY\n' >&2
test -d /tmp && test ! -L /tmp
test "$(stat -c '%u:%a' /tmp)" = '0:1777'
test -f /tmp/nginx.conf && test ! -L /tmp/nginx.conf
edge_uid=$(id -u)
test "$edge_uid" = 0
test "$(stat -c '%u:%h' /tmp/nginx.conf)" = '0:1'
edge_mode=$(stat -c '%a' /tmp/nginx.conf)
test "$((0$edge_mode & 022))" -eq 0
test "$(wc -c < /tmp/nginx.conf)" -le 131072
'''
READ_LIVE = FILE_GUARD + r'''
printf 'LIVE\n'
base64 /tmp/nginx.conf
printf '\nMOUNTED_SHA\n'
test -f /etc/nginx/nginx.conf.template && test ! -L /etc/nginx/nginx.conf.template
test "$(wc -c < /etc/nginx/nginx.conf.template)" -le 131072
sha256sum /etc/nginx/nginx.conf.template | cut -d ' ' -f 1
'''
READ_PROCESSES = r'''
for edge_cmd in /proc/[0-9]*/cmdline; do
  test -r "$edge_cmd" || continue
  edge_line=$(tr '\000' ' ' < "$edge_cmd" 2>/dev/null) || continue
  edge_kind=''
  case "$edge_line" in
    'nginx: master process '*)
      case " $edge_line " in *' -c /tmp/nginx.conf '*) edge_kind=MASTER;; *) exit 1;; esac;;
    'nginx: worker process'*) edge_kind=WORKER;;
  esac
  test -n "$edge_kind" || continue
  edge_pid=${edge_cmd#/proc/}; edge_pid=${edge_pid%/cmdline}
  edge_start=$(awk '{print $22}' "/proc/$edge_pid/stat")
  printf '%s %s:%s\n' "$edge_kind" "$edge_pid" "$edge_start"
done
'''
STAGE = FILE_GUARD + r'''
umask 077
printf 'EDGE_STEP:LIVE_HASH\n' >&2
test "$(sha256sum /tmp/nginx.conf | cut -d ' ' -f 1)" = "$2"
printf 'EDGE_STEP:STAGE_DIRECTORY\n' >&2
mkdir -m 700 "$1"
cp /tmp/nginx.conf "$1/before.conf"
chmod 600 "$1/before.conf"
printf 'EDGE_STEP:CANDIDATE_WRITE\n' >&2
cat > "$1/candidate.conf"
test "$(stat -c '%u:%a:%h' "$1/candidate.conf")" = '0:600:1'
test "$(sha256sum "$1/candidate.conf" | cut -d ' ' -f 1)" = "$3"
printf 'EDGE_STEP:NGINX_SYNTAX\n' >&2
nginx -t -c "$1/candidate.conf"
sync -f "$1/before.conf"
sync -f "$1/candidate.conf"
sync -f "$1"
printf 'CANDIDATE_STAGED_AND_NGINX_VALIDATED\n'
'''
INSTALL = FILE_GUARD + r'''
test -d "$1" && test ! -L "$1"
test "$(stat -c '%u:%a' "$1")" = '0:700'
test -f "$1/candidate.conf" && test ! -L "$1/candidate.conf"
test "$(stat -c '%u:%a:%h' "$1/candidate.conf")" = '0:600:1'
test "$(sha256sum "$1/candidate.conf" | cut -d ' ' -f 1)" = "$3"
printf 'EDGE_STEP:NGINX_SYNTAX\n' >&2
nginx -t -c "$1/candidate.conf"
printf 'EDGE_STEP:LIVE_HASH\n' >&2
test "$(sha256sum /tmp/nginx.conf | cut -d ' ' -f 1)" = "$2"
chmod "$edge_mode" "$1/candidate.conf"
sync -f "$1/candidate.conf"
printf 'EDGE_STEP:ATOMIC_INSTALL\n' >&2
mv -T "$1/candidate.conf" /tmp/nginx.conf
sync -f /tmp
printf 'EDGE_STEP:GRACEFUL_RELOAD\n' >&2
nginx -s reload -c /tmp/nginx.conf
printf 'LIVE_INSTALLED_RELOAD_SIGNAL_SENT\n'
'''
PPT_PROBE = r'''
edge_code=$(curl --noproxy '*' --proto '=http' --connect-timeout 2 --max-time 5 --max-filesize 4096 --silent --show-error -X POST -H 'Host: cnoocclaw.cnooc.com.cn' -H 'Content-Type: application/json' --data-binary '{' -w '\n%{http_code}' "http://127.0.0.1:2026/api/ppt-master-runtime/$1")
case "$edge_code" in
  *'"code":"PPT_AGENT_RETIRED"'*'409') printf 'PPT_ADMISSION_BLOCKED_409\n';;
  *) printf 'PPT_ADMISSION_NOT_CONFIRMED\n'; exit 1;;
esac
'''


def split_live(raw):
    check(raw.startswith(b'LIVE\n') and raw.count(b'\nMOUNTED_SHA\n') == 1, 'LIVE_READ_SHAPE')
    encoded, mounted = raw[5:].split(b'\nMOUNTED_SHA\n')
    live = base64.b64decode(b''.join(encoded.split()), validate=True)
    check(0 < len(live) <= 131072 and b'\0' not in live and mounted.strip().decode() == policy.STALE_MOUNT_SHA, 'LIVE_OR_MOUNT_CHANGED')
    return live


def process_identity(raw):
    rows = raw.decode('ascii').splitlines()
    check(0 < len(rows) <= 1024 and all(re.fullmatch(r'(MASTER|WORKER) [1-9][0-9]*:[0-9]+', r) for r in rows), 'NGINX_PROCESS_SHAPE')
    masters = [r[7:] for r in rows if r.startswith('MASTER ')]
    workers = [r[7:] for r in rows if r.startswith('WORKER ')]
    check(len(masters) == 1 and len(workers) > 0 and len(rows) == len(set(rows)), 'NGINX_PROCESS_COUNT')
    return {'master': masters[0], 'workers': sorted(workers)}


def endpoint_refs(value, name):
    refs = []
    items = value.get('items', [])
    check(bool(items), 'ENDPOINTS_REQUIRED')
    for item in items:
        check(item['metadata'].get('labels', {}).get('kubernetes.io/service-name') == name, 'ENDPOINT_SERVICE')
        for ep in item.get('endpoints', []):
            conditions, target = ep.get('conditions', {}), ep.get('targetRef', {})
            check(conditions.get('ready') is True and not conditions.get('terminating')
                  and target.get('kind') == 'Pod' and target.get('namespace', NS) == NS
                  and target.get('uid') and target.get('name'), 'ENDPOINT_NOT_READY')
            refs.append((target['name'], target['uid']))
    check(bool(refs) and len(refs) == len(set(refs)), 'ENDPOINT_DUPLICATE_OR_EMPTY')
    return sorted(refs)


class Controller:
    def __init__(self):
        self.started, self.count, self.fd, self.parent_fd, self.tool = time.monotonic(), 0, None, None, None
        self.operation = uuid.uuid4().hex
        self.report = dict(schema='portal-skillhub-edge-auth-activation.v1', tool_revision='r1', status='INCOMPLETE',
            operation=self.operation, automatic_retry=False, automatic_rollback=False, once_claimed=False,
            configuration_modified=False, configmap_patch_requests=0, configmap_patch_may_have_applied=False,
            runtime_install_may_have_applied=False, reload_may_have_been_requested=False,
            deployment_modified=False, pods_created=0, pods_deleted=0, sandbox_deleted=False,
            explicit_database_queries=0, database_configuration_modified=False,
            application_auth_health_reads_may_access_existing_database=True,
            provider_requests=0, real_credentials_used=False, raw_errors_exported=False,
            raw_configuration_exported=False, browser_business_acceptance_pending=True,
            old_worker_connections_may_remain=True, nginx_test_may_open_configured_logs=True,
            native_receipt_bound=NATIVE_RECEIPT_BINDING is not None)

    def call(self, args, label, data=None, limit=4194304):
        self.count += 1
        self.report['last_step'] = label
        remaining = 420 - (time.monotonic() - self.started)
        check(remaining > 0 and self.count <= 160 and (data is None or len(data) <= 1048576), 'COMMAND_BUDGET')
        argv = [self.tool, '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', NS] + args
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='/', env={'PATH': SAFE_PATH, 'LC_ALL': 'C'})
        buffers, overflow = [bytearray(), bytearray()], threading.Event()
        def drain(stream, index):
            try:
                while True:
                    chunk = stream.read(16384)
                    if not chunk:
                        break
                    if len(buffers[index]) + len(chunk) > (limit if index == 0 else 65536):
                        overflow.set()
                        proc.kill()
                        break
                    buffers[index].extend(chunk)
            finally:
                stream.close()
        def feed():
            try:
                proc.stdin.write(data)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        workers = [threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True)]
        if data is not None:
            workers.append(threading.Thread(target=feed, daemon=True))
        for worker in workers:
            worker.start()
        timed_out = False
        try:
            proc.wait(timeout=min(30, remaining))
        except BaseException:
            timed_out = True
            proc.kill()
            proc.wait(timeout=3)
        for worker in workers:
            worker.join(timeout=3)
        diagnostic = bytes(buffers[1])
        if proc.returncode or timed_out or overflow.is_set():
            steps = re.findall(rb'^EDGE_STEP:([A-Z_]+)$', diagnostic, re.M)
            self.report['failure_step'] = steps[-1].decode() if steps else None
            self.report['failure_categories'] = [name for name, token in (
                ('permission_denied', b'permission denied'), ('duplicate_directive', b'duplicate'),
                ('upstream_dns', b'host not found in upstream'), ('no_space', b'no space left'),
                ('read_only_filesystem', b'read-only file system')) if token in diagnostic.lower()]
        check(not timed_out, 'COMMAND_TIMEOUT_OR_INTERRUPTED_' + label)
        check(not overflow.is_set() and not any(w.is_alive() for w in workers), 'COMMAND_OUTPUT_BOUND_' + label)
        check(proc.returncode == 0, 'COMMAND_FAILED_' + label)
        return bytes(buffers[0])

    def get(self, kind, name=None, selector=None):
        check(kind in {'deployment', 'deployments', 'pod', 'replicaset', 'service', 'endpointslices', 'configmap'}, 'READ_RESOURCE')
        args = ['get', kind] + ([name] if name else []) + (['-l', selector] if selector else []) + ['-o', 'json']
        return json.loads(self.call(args, 'get_' + kind + '_' + (name or 'selected')))

    def execute(self, script, label, params=(), data=None):
        check(script in (READ_LIVE, READ_PROCESSES, STAGE, INSTALL, PPT_PROBE), 'FIXED_SCRIPT_REQUIRED')
        if script in (STAGE, INSTALL):
            check(len(params) == 3 and params[0] == '/tmp/portal-skillhub-edge-' + self.operation
                  and all(re.fullmatch('[0-9a-f]{64}', x) for x in params[1:]), 'STAGE_ARGUMENTS')
        elif script == PPT_PROBE:
            check(params in [('runs/stream',), ('threads/retirement-verification/runs/stream',)], 'PPT_PROBE_PATH')
        else:
            check(not params, 'UNEXPECTED_SCRIPT_PARAMS')
        return self.call(['exec'] + (['-i'] if data is not None else []) + [POD, '-c', 'nginx', '--',
            'sh', '-eu', '-c', script, 'skillhub-edge'] + list(params), label, data, 262144)

    def replay(self, container, raw, label):
        for i, expression in enumerate(policy.parse_startup(container)):
            policy.validate_substitution(expression)
            raw = self.call(['exec', '-i', POD, '-c', 'nginx', '--', 'sed', '-e', expression], label + '_' + str(i), raw, 131072)
            check(0 < len(raw) <= 131072 and b'\0' not in raw, 'STARTUP_OUTPUT_BOUND')
        return raw

    def owner(self, pod, deployment):
        refs = [x for x in pod['metadata'].get('ownerReferences', []) if x.get('controller')]
        check(len(refs) == 1 and refs[0].get('kind') == 'ReplicaSet', 'POD_OWNER_REQUIRED')
        rs = self.get('replicaset', refs[0]['name'])
        check(rs['metadata']['uid'] == refs[0]['uid'] and not rs['metadata'].get('deletionTimestamp'), 'REPLICASET_CHANGED')
        owners = [x for x in rs['metadata'].get('ownerReferences', []) if x.get('controller')]
        check(len(owners) == 1 and owners[0].get('kind') == 'Deployment'
              and owners[0].get('uid') == deployment['metadata']['uid'] and owners[0].get('name') == deployment['metadata']['name'], 'DEPLOYMENT_OWNER_CHANGED')

    def snapshot(self, native, expected_cm=None):
        names = (SERVER,) + PROTECTED
        values = json.loads(self.call(['get', 'deployments'] + list(names) + ['-o', 'json'], 'deployment_snapshot'))['items']
        deployments = {x['metadata']['name']: x for x in values}
        check(len(values) == len(names) and set(deployments) == set(names), 'DEPLOYMENT_INVENTORY_CHANGED')
        expected = dict(native['protected_before'], **{SERVER: native['after']})
        for name in names:
            check(binding(deployments[name]) == expected[name], 'DEPLOYMENT_CHANGED_' + name)
            if name in (SERVER, ENTRY):
                check(converged(deployments[name]), 'DEPENDENCY_NOT_CONVERGED_' + name)
        server = deployments[SERVER]
        cs = server['spec']['template']['spec']['containers']
        check(len(cs) == 1 and cs[0].get('name') == 'server' and cs[0].get('image') == SERVER_IMAGE
              and [x for x in cs[0].get('env', []) if x.get('name') == 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED']
              == [{'name': 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED', 'value': 'true'}], 'SERVER_AUTH_OR_IMAGE_CHANGED')
        pod = self.get('pod', POD)
        check(pod['metadata']['uid'] == POD_UID, 'ENTRY_POD_CHANGED')
        pod_identity(pod)
        self.owner(pod, deployments[ENTRY])
        container = pod['spec']['containers'][0]
        intended = deployments[ENTRY]['spec']['template']['spec']['containers']
        check(len(intended) == 1 and container.get('name') == 'nginx'
              and container.get('command') == intended[0].get('command') and container.get('args') == intended[0].get('args'), 'ENTRY_STARTUP_CHANGED')
        policy.parse_startup(container)
        mounts = [m for m in container.get('volumeMounts', []) if m.get('mountPath') == policy.TEMPLATE]
        check(len(mounts) == 1 and mounts[0].get('subPath') == 'nginx.conf' and mounts[0].get('readOnly') is True
              and any(v['name'] == mounts[0]['name'] and v.get('configMap', {}).get('name') == CM for v in pod['spec'].get('volumes', [])), 'TEMPLATE_MOUNT_CHANGED')
        services = {}
        for name, identity in dict(SERVICE_IDENTITIES, **{SERVER: SERVER_SERVICE}).items():
            service = self.get('service', name)
            check((service['metadata']['uid'], sha(canonical(service['spec']))) == identity, 'SERVICE_CHANGED_' + name)
            refs = endpoint_refs(self.get('endpointslices', selector='kubernetes.io/service-name=' + name), name)
            target = (native['runtime']['pod'], native['runtime']['uid']) if name == SERVER else (POD, POD_UID)
            check(refs == [target], 'SERVICE_BACKEND_CHANGED_' + name)
            services[name] = refs
        serverpod = self.get('pod', native['runtime']['pod'])
        check(serverpod['metadata']['uid'] == native['runtime']['uid'] and serverpod['spec'].get('nodeName') == 'datax-01', 'SERVER_POD_CHANGED')
        server_identity = pod_identity(serverpod)
        check(serverpod['spec']['containers'][0].get('image') == SERVER_IMAGE
              and server_identity['image_id'].split('@')[-1].replace('containerd://', '') in {SERVER_IMAGE.split('@')[1], SERVER_CONFIG}, 'SERVER_RUNTIME_IMAGE_CHANGED')
        self.owner(serverpod, server)
        cm = self.get('configmap', CM)
        check(cm['metadata']['uid'] == CM_UID and not cm.get('immutable') and not cm.get('binaryData'), 'CONFIGMAP_CHANGED')
        check(cm.get('data') == expected_cm if expected_cm is not None else sha(canonical(cm.get('data'))) == CM_DATA_SHA, 'CONFIGMAP_DATA_CHANGED')
        return dict(deployments=deployments, pod=pod, serverpod=serverpod, cm=cm, container=container, services=services)

    def check_same_pods(self, snapshot):
        for pod in (snapshot['pod'], snapshot['serverpod']):
            check(pod_identity(self.get('pod', pod['metadata']['name'])) == pod_identity(pod), 'CONTAINER_CHANGED')

    def http(self, pod, path, native=False, bearer=False):
        # Fixed local targets only; never follow a redirect or attach a session.
        allowed = {x['path'] for x in (NATIVE_CHECKS if native else EDGE_CHECKS)}
        check(path in allowed and (pod != POD) == native, 'HTTP_PROBE_TARGET')
        args = ['exec', pod, '-c', 'server' if native else 'nginx', '--', '/usr/bin/curl' if native else 'curl',
                '--noproxy', '*', '--proto', '=http', '--connect-timeout', '2', '--max-time', '5',
                '--max-filesize', '65536', '--silent', '--show-error', '--output', '/dev/null',
                '--write-out', '%{http_code}\n%{redirect_url}', '--request', 'GET']
        if not native:
            args += ['--header', 'Host: cnoocclaw.cnooc.com.cn']
        if bearer:
            args += ['--header', INVALID_BEARER]
        args += [('http://127.0.0.1:8080' if native else 'http://127.0.0.1:2026') + path]
        raw = self.call(args, ('native_' if native else 'edge_') + 'http_probe', limit=4096).decode('ascii')
        check(re.fullmatch(r'[0-9]{3}\n[^\r\n]*', raw) is not None, 'HTTP_PROBE_SHAPE')
        code, redirect = raw.split('\n')
        return int(code), redirect

    def native_http(self, native, label):
        pod = native['runtime']['pod']
        jar = self.call(['exec', pod, '-c', 'server', '--', 'sha256sum', '/app/app.jar'], 'native_jar_hash', limit=4096)
        check(jar.decode().strip() == SERVER_JAR + '  /app/app.jar', 'NATIVE_JAR_CHANGED')
        for row in NATIVE_CHECKS:
            code, redirect = self.http(pod, row['path'], True, row['fabricated_bearer'])
            check(code == row['status'] and not redirect, 'NATIVE_HTTP_AUTH_NO_LONGER_ACCEPTED')
        self.report[label] = NATIVE_CHECKS

    def write_new(self, name, raw):
        check(self.fd is not None and re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None, 'PRIVATE_WRITE_PATH')
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self.fd)

    def save(self):
        if self.fd is None:
            return
        self.report.update(commands=self.count, elapsed_seconds=round(time.monotonic() - self.started, 3))
        pending = 'REPORT-' + uuid.uuid4().hex + '.pending'
        self.write_new(pending, canonical(self.report))
        os.replace(pending, 'REPORT.json', src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)

    def claim(self, initial, plan):
        self.parent_fd = os.open('/var/lib', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        s = os.fstat(self.parent_fd)
        check(s.st_uid == 0 and s.st_mode & 0o022 == 0, 'PRIVATE_PARENT_REQUIRED')
        name = DIRECTORY.rsplit('/', 1)[1]
        os.mkdir(name, 0o700, dir_fd=self.parent_fd)
        os.fsync(self.parent_fd)
        self.report['once_claimed'] = True
        self.fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent_fd)
        self.report.update(report_path=DIRECTORY + '/REPORT.json', private_backups_created=False)
        self.save()
        for name, raw in (
            ('configmap.before.private.json', canonical(initial['cm'])),
            ('runtime.before.conf', plan['current_live']), ('template.before.conf', plan['current_template']),
            ('runtime.candidate.conf', plan['candidate_live']), ('template.candidate.conf', plan['candidate_template'])):
            self.write_new(name, raw)
        self.report['private_backups_created'] = True
        self.save()

    def patch(self, cm, candidate):
        operations = [
            {'op': 'test', 'path': '/metadata/uid', 'value': CM_UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': cm['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/data', 'value': cm['data']},
            {'op': 'replace', 'path': '/data/nginx.conf', 'value': candidate.decode('utf-8')},
        ]
        check(self.report['configmap_patch_requests'] == 0, 'SINGLE_PATCH_ONLY')
        self.report.update(configmap_patch_requests=1, configmap_patch_may_have_applied=True)
        self.save()
        result = self.call(['patch', 'configmap', CM, '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], 'patch_entry_configmap', canonical(operations))
        return json.loads(result)

    def run(self):
        try:
            self.execute_all()
        except BaseException as exc:
            self.report.update(error_class=type(exc).__name__,
                error_code=str(exc) if isinstance(exc, Refused) else 'INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'LOCAL_FAILURE')
        self.report.update(commands=self.count, elapsed_seconds=round(time.monotonic() - self.started, 3),
                           observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        try:
            self.save()
            if self.fd is not None:
                raw = private_read(DIRECTORY + '/REPORT.json', 262144)
                self.report.update(report_bytes=len(raw), report_sha256=sha(raw))
        except Exception:
            self.report['report_write_or_readback_failed'] = True
            self.report['status'] = 'INCOMPLETE'
        finally:
            if self.fd is not None:
                os.close(self.fd)
            if self.parent_fd is not None:
                os.close(self.parent_fd)
        return self.report

    def execute_all(self):
        # Preparation artifacts cannot perform even a cluster read until sealed.
        validate_pin(NATIVE_RECEIPT_BINDING)
        check(sys.platform.startswith('linux') and os.geteuid() == 0
              and socket.gethostname().split('.')[0] == 'datax-01' and os.uname().machine in ('aarch64', 'arm64')
              and sys.flags.isolated and sys.flags.dont_write_bytecode, 'ROOT_ISOLATED_DATAX01_REQUIRED')
        native = read_native_receipt()
        check(not os.path.lexists(DIRECTORY), 'ONCE_EXISTS_REVIEW_REQUIRED')
        self.tool = shutil.which('kubectl', path=SAFE_PATH)
        check(bool(self.tool), 'KUBECTL_REQUIRED')
        self.report['native_acceptance_receipt'] = dict(NATIVE_RECEIPT_BINDING, path=NATIVE_RECEIPT)
        initial = self.snapshot(native)
        live = split_live(self.execute(READ_LIVE, 'read_live'))
        plan = policy.plan_production_pair(initial['cm']['data']['nginx.conf'].encode(), live, initial['container'])
        replayed = self.replay(initial['container'], plan['current_template'], 'replay_current')
        replayed_candidate = self.replay(initial['container'], plan['candidate_template'], 'replay_candidate')
        policy.verify_startup_replay(plan, replayed, replayed_candidate)
        self.check_same_pods(initial)
        self.native_http(native, 'native_http_before')
        check(self.http(POD, '/health') == (200, ''), 'ENTRY_HEALTH_BEFORE')
        processes = process_identity(self.execute(READ_PROCESSES, 'processes_before'))
        self.report['plan'] = plan['summary']
        self.report['startup_replay_verified'] = True
        self.claim(initial, plan)
        args = ('/tmp/portal-skillhub-edge-' + self.operation, sha(live), sha(plan['candidate_live']))
        self.report['container_staging_may_exist'] = True
        self.save()
        check(self.execute(STAGE, 'stage_and_nginx_test', args, plan['candidate_live']).strip()
              == b'CANDIDATE_STAGED_AND_NGINX_VALIDATED', 'STAGE_NOT_CONFIRMED')
        self.report['candidate_nginx_syntax_verified'] = True
        self.save()
        check(read_native_receipt() == native, 'NATIVE_RECEIPT_CHANGED')
        fresh = self.snapshot(native)
        check(pod_identity(fresh['pod']) == pod_identity(initial['pod'])
              and pod_identity(fresh['serverpod']) == pod_identity(initial['serverpod']), 'PODS_CHANGED_BEFORE_COMMIT')
        check(fresh['cm']['metadata']['resourceVersion'] == initial['cm']['metadata']['resourceVersion'], 'CONFIGMAP_VERSION_CHANGED')
        check(split_live(self.execute(READ_LIVE, 'live_before_commit')) == live, 'LIVE_CHANGED_BEFORE_COMMIT')
        check(process_identity(self.execute(READ_PROCESSES, 'processes_before_commit')) == processes, 'NGINX_PROCESS_CHANGED_BEFORE_COMMIT')
        expected_data = dict(fresh['cm']['data'], **{'nginx.conf': plan['candidate_template'].decode()})
        changed = self.patch(fresh['cm'], plan['candidate_template'])
        check(changed['metadata']['uid'] == CM_UID and changed.get('data') == expected_data, 'PATCH_NOT_CONFIRMED')
        self.report.update(configuration_modified=True, configmap_patch_acknowledged=True)
        self.save()
        self.check_same_pods(initial)
        self.report.update(runtime_install_may_have_applied=True, reload_may_have_been_requested=True)
        self.save()
        check(self.execute(INSTALL, 'atomic_install_and_reload', args).strip() == b'LIVE_INSTALLED_RELOAD_SIGNAL_SENT', 'RELOAD_NOT_ACKNOWLEDGED')
        deadline = time.monotonic() + 30
        while True:
            current = process_identity(self.execute(READ_PROCESSES, 'observe_graceful_workers'))
            check(current['master'] == processes['master'], 'NGINX_MASTER_RESTARTED')
            if set(current['workers']) - set(processes['workers']):
                break
            check(time.monotonic() < deadline, 'RELOAD_WORKER_TIMEOUT')
            time.sleep(1)
        checks = []
        for row in EDGE_CHECKS:
            status, redirect = self.http(POD, row['path'], bearer=row.get('fabricated_bearer', False))
            # curl resolves relative redirects using its loopback request URL;
            # Nginx normally emits an absolute URL based on the explicit Host.
            allowed_redirects = {'http://cnoocclaw.cnooc.com.cn:2026/login', 'http://cnoocclaw.cnooc.com.cn/login', 'http://127.0.0.1:2026/login'} if row.get('redirect') else {''}
            check(status == row['status'] and redirect in allowed_redirects, 'EDGE_HTTP_ACCEPTANCE_FAILED')
            checks.append(dict(row, observed_status=status, redirect_to_login=bool(redirect)))
        for path in ('runs/stream', 'threads/retirement-verification/runs/stream'):
            check(self.execute(PPT_PROBE, 'verify_preserved_ppt_admission', (path,)).strip() == b'PPT_ADMISSION_BLOCKED_409', 'PPT_RETIREMENT_REGRESSION')
        self.native_http(native, 'native_http_after')
        final = self.snapshot(native, expected_data)
        self.check_same_pods(initial)
        check(pod_identity(final['pod']) == pod_identity(initial['pod']) and pod_identity(final['serverpod']) == pod_identity(initial['serverpod']), 'PODS_CHANGED_AFTER_RELOAD')
        check(split_live(self.execute(READ_LIVE, 'verify_installed_live')) == plan['candidate_live'], 'INSTALLED_LIVE_CHANGED')
        check(read_native_receipt() == native, 'NATIVE_RECEIPT_CHANGED_AFTER')
        self.report.update(status=SUCCESS, edge_http_checks=checks, native_auth_rechecked=True,
            graceful_reload_verified=True, nginx_master_preserved=True, new_worker_observed=True,
            service_backends_preserved=True, protected_deployments_preserved=True,
            future_configmap_guard_persisted=True, stale_mount_unchanged=True, stale_mount_used_as_candidate=False,
            ppt_new_run_admission='BLOCKED_409', stable_readback=True,
            authenticated_browser_requests=0, valid_native_bearer_requests=0,
            scope='skillhub_edge_auth_only_no_deployment_rollout')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-skillhub-edge-auth-once', action='store_true', required=True)
    parser.parse_args()
    result = Controller().run()
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['status'] == SUCCESS else 1


if __name__ == '__main__':
    raise SystemExit(main())
