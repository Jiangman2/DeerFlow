"""Narrow runtime adapters for the audited pre-tool-policy production baseline.

Do not replace whole lead/subagent factories with the dirty checkout. Each
adapter changes named blocks only and rejects unknown/repeated anchors.
"""

import ast


def _span(text, qualified):
    body = ast.parse(text).body
    for name in qualified.split("."):
        nodes = [n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(nodes) != 1:
            raise ValueError("Runtime anchor definition missing or ambiguous: " + qualified)
        node = nodes[0]
        body = node.body
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    return start, node.end_lineno


def definition(text, qualified):
    start, end = _span(text, qualified)
    return "".join(text.splitlines(keepends=True)[start:end])


def block(text, qualified, start, end):
    """Read an explicitly reviewed block, inclusive start/exclusive end."""
    section = definition(text, qualified).replace("\r\n", "\n")
    if section.count(start) != 1 or section.count(end) != 1:
        raise ValueError("Candidate runtime block anchor changed: " + qualified)
    left, right = section.index(start), section.index(end)
    if right <= left:
        raise ValueError("Candidate runtime anchor order changed: " + qualified)
    return section[left:right]


def replace_once(text, qualified, before, after):
    start, end = _span(text, qualified)
    lines = text.splitlines(keepends=True)
    section = "".join(lines[start:end])
    newline = "\r\n" if "\r\n" in section else "\n"
    before, after = before.replace("\r\n", "\n").replace("\n", newline), after.replace("\r\n", "\n").replace("\n", newline)
    if not before or section.count(before) != 1:
        raise ValueError("Production runtime anchor missing or repeated: " + qualified)
    lines[start:end] = [section.replace(before, after, 1)]
    result = "".join(lines)
    ast.parse(result)
    return result


def lead(source, candidate):
    # Retain the production static allowed-tools/deferred-tool protocol.
    scope = "build_middlewares"
    source = replace_once(source, scope, "    user_id: str | None = None,\n", "    user_id: str | None = None,\n    publication=None,\n")
    anchor = "    middlewares = build_lead_runtime_middlewares(app_config=resolved_app_config, lazy_init=True)\n"
    addition = block(candidate, scope, "    if publication is not None:\n", "    # Always inject current date")
    source = replace_once(source, scope, anchor, anchor + addition)
    addition = block(candidate, scope, "    from deerflow.runtime.employee_resources import has_delegated_knowledge\n", "    # Deterministically load")
    source = replace_once(source, scope, "    middlewares.append(WeKnoraScopeMiddleware())\n\n", addition)
    anchor = "SkillActivationMiddleware(available_skills=available_skills, app_config=resolved_app_config, user_id=user_id)"
    source = replace_once(source, scope, anchor, anchor[:-1] + ', thread_id=_get_runtime_config(config).get("thread_id"))')

    scope = "_load_enabled_skills_for_tool_policy"
    source = replace_once(source, scope, "user_id: str | None = None)", "user_id: str | None = None, thread_id: str | None = None)")
    source = replace_once(source, scope, "get_enabled_skills_for_config(app_config, user_id=user_id)", 'get_enabled_skills_for_config(app_config, user_id=user_id, **({"thread_id": thread_id} if thread_id else {}))')

    scope = "_make_lead_agent"
    before = '    agent_name = validate_agent_name(cfg.get("agent_name"))\n\n    agent_config = load_agent_config(agent_name) if not is_bootstrap else None\n'
    after = block(candidate, scope, '    agent_name = validate_agent_name(cfg.get("agent_name"))\n', "    available_skills = _available_skill_names")
    source = replace_once(source, scope, before, after)
    anchor = '            "available_skills": sorted(available_skills) if available_skills is not None else None,\n'
    source = replace_once(source, scope, anchor, anchor + '            "employee_knowledge_disabled": getattr(knowledge_scope, "mode", None) == "disabled",\n')
    anchor = "skills_for_tool_policy = _load_enabled_skills_for_tool_policy(available_skills, app_config=resolved_app_config, user_id=resolved_user_id)"
    source = replace_once(source, scope, anchor, anchor[:-1] + ", thread_id=thread_id)")
    source = replace_once(source, scope,
                          "can_update_agent = bool(agent_name and not is_webhook_channel and is_personal_agent(agent_name, user_id=resolved_user_id))",
                          'can_update_agent = bool(agent_name and agent_source not in ("team", "legacy") and not is_webhook_channel and is_personal_agent(agent_name, user_id=resolved_user_id))')
    # Two filter sites exist (bootstrap and ordinary). Only ordinary execution
    # has a publication; anchor the whole ordinary block to prevent ambiguity.
    anchor = '    filtered = filter_tools_by_datalake_scope(filtered, enabled=datalake_enabled)\n    if non_interactive:\n'
    source = replace_once(source, scope, anchor,
                          '    filtered = filter_tools_by_datalake_scope(filtered, enabled=datalake_enabled)\n'
                          '    from deerflow.runtime.employee_resources import scope_employee_tools\n\n'
                          '    filtered = scope_employee_tools(filtered, publication, knowledge_scope=knowledge_scope)\n'
                          '    if non_interactive:\n')
    anchor = "            user_id=resolved_user_id,\n        ),\n        system_prompt="
    source = replace_once(source, scope, anchor, "            user_id=resolved_user_id,\n            publication=publication,\n        ),\n        system_prompt=")
    anchor = "                agent_name=agent_name,\n"
    source = replace_once(source, scope, anchor, anchor + "                agent_source=agent_source,\n                agent_soul=publication.soul if publication is not None else None,\n                thread_id=thread_id,\n")
    return source


def activation(source, candidate):
    scope = "SkillActivationMiddleware.__init__"
    source = replace_once(source, scope, "        user_id: str | None = None,\n", "        user_id: str | None = None,\n        thread_id: str | None = None,\n")
    anchor = "        self._user_id = user_id\n"
    source = replace_once(source, scope, anchor, anchor + "        self._thread_id = thread_id\n")
    scope = "SkillActivationMiddleware._storage"
    addition = block(candidate, scope, "        if self._thread_id and self._user_id:\n", "        if self._user_id is not None:\n")
    anchor = "        if self._user_id is not None:\n"
    return replace_once(source, scope, anchor, addition + anchor)


def subagent(source, candidate):
    scope = "SubagentExecutor.__init__"
    after = block(candidate, scope, "        from deerflow.runtime.employee_resources import read_employee_publication, scope_employee_tools\n", "            config.tools,\n")
    source = replace_once(source, scope, "        self._base_tools = _filter_tools(\n            tools,\n", after)
    # Locate graph-builder method by its unique anchor; the owning method's
    # name is explicit in this baseline and must remain unchanged.
    scope = "SubagentExecutor._create_agent"
    addition = block(candidate, scope, "        if self.employee_publication is not None:\n", "        # Collect every guard middleware")
    anchor = "        middlewares = build_subagent_runtime_middlewares(**middleware_kwargs)\n"
    source = replace_once(source, scope, anchor, anchor + addition)
    scope = "SubagentExecutor._load_skills"
    original = definition(source, scope)
    return replace_once(source, scope, original, definition(candidate, scope))


def task(source, candidate):
    scope = "task_tool"
    anchor = "    tools = filter_tools_by_datalake_scope(tools, enabled=datalake_enabled)\n\n    # Create executor\n"
    addition = block(candidate, scope, '    if metadata.get("employee_knowledge_disabled") is True:\n', "    # Create executor\n")
    return replace_once(source, scope, anchor, addition + "    # Create executor\n")


ADAPTERS = {"lead": lead, "activation": activation, "subagent": subagent, "task": task}
