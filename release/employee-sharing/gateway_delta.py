"""Compose reviewed employee-only symbols on audited Gateway source.

Reviewed modules are NOT whole-file copies of the dirty checkout.
This is a partial candidate composer, not a runnable release/rollout package.
Every unselected production definition remains byte-for-byte unchanged.
"""

import ast
import hashlib

RECIPES = {
    "app/gateway/app.py": {
        "symbols": (),
        "imports": ("from app.gateway.routers import employee_changes, employee_resources",),
        "insertions": (
            {
                "symbol": "lifespan",
                "anchor": '        logger.info("LangGraph runtime initialised")\n',
                "addition": (
                    "        if not isolated_ppt_runner:\n"
                    "            from app.gateway.employee_knowledge import EmployeeKnowledgeBroker\n"
                    "            from deerflow.config.paths import get_paths\n"
                    "            from deerflow.runtime.employee_resources import register_employee_resource_services\n"
                    "\n"
                    "            employee_broker = EmployeeKnowledgeBroker(get_paths())\n"
                    "            register_employee_resource_services(search=employee_broker.search, authorize=employee_broker.authorize)\n"
                ),
            },
            {
                "symbol": "create_app",
                "anchor": "    app.include_router(agents.router)\n",
                "addition": "    app.include_router(employee_resources.router)\n    app.include_router(employee_changes.router)\n",
            },
        ),
    },
    "app/gateway/routers/agents.py": {
        "symbols": (
            "AgentResponse", "AgentCreateRequest", "AgentUpdateRequest", "AgentPublishRequest",
            "AgentPublicationVersion", "AgentPublicationHistory", "migrate_digital_employee_categories",
            "_agent_config_to_response", "_definition_response", "_shared_agent_dir", "_shared_agent_response",
            "list_team_agents_endpoint", "get_agent", "create_agent_endpoint", "publish_agent",
            "unpublish_team_agent", "list_agent_publications", "update_agent", "delete_agent",
            "_encode_custom_avatar", "get_agent_avatar", "upload_agent_avatar", "delete_agent_avatar",
            "_delete_agent_with_memory_cancel",
        ),
        "imports": (
            "import json", "from fastapi import Query",
            "from app.gateway.agent_knowledge import validate_creator_knowledge_scope",
            "from deerflow.config.agent_knowledge_config import AgentKnowledgeScope",
            "from deerflow.config.agents_config import AgentSource, shared_agent_source_dir",
            "from deerflow.persistence.agents.base import parse_agent_config",
            "from deerflow.persistence.agents.drafts import AgentDefinition, DraftConflict",
            "from deerflow.persistence.agents.publications import PublicationConflict, PublicationForbidden, PublicationSnapshot, TeamPublicationStore",
            "from deerflow.runtime.user_context import get_current_user",
            "from deerflow.skills.agent_selection import validate_agent_skills",
        ),
    },
    "app/gateway/services.py": {
        "symbols": (
            "_CONTEXT_CONFIGURABLE_KEYS", "_SERVER_OWNED_AUTHZ_CONTEXT_KEYS",
            "merge_run_context_overrides", "inject_authenticated_user_context",
            "inject_weknora_user_context", "build_run_config", "start_run",
        ),
        "imports": (
            "from app.gateway.agent_knowledge import apply_agent_knowledge_scope",
            "from app.gateway.agent_publications import admit_agent_publication",
            "from deerflow.runtime.user_context import get_effective_user_id",
        ),
    },
    "app/gateway/routers/threads.py": {
        "symbols": ("branch_thread",),
        "imports": ("from app.gateway.services import resolve_trusted_internal_owner_for_attribution",),
    },
    "packages/harness/deerflow/config/agents_config.py": {
        "symbols": ("AgentConfig", "resolve_agent_dir", "AgentSource", "validate_agent_source", "shared_agent_source_dir", "load_agent_config", "load_agent_soul", "list_team_agents"),
        "imports": ("from deerflow.config.agent_knowledge_config import AgentKnowledgeScope",),
    },
    "packages/harness/deerflow/persistence/agents/base.py": {
        "symbols": ("AgentStore",),
        "imports": (),
    },
    "packages/harness/deerflow/persistence/agents/file.py": {
        "symbols": ("FileAgentStore",),
        "imports": (),
    },
    "packages/harness/deerflow/persistence/agents/sql.py": {
        "symbols": ("SqlAgentStore",),
        "imports": (),
    },
}

# Runtime modules contain unrelated checkout updates. Whole selected symbols
# are used only where the audited diff is employee-only; mixed factories use
# exact block adapters which retain the deployed protocols.
RECIPES.update({
    "packages/harness/deerflow/runtime/context_compaction.py": {
        "symbols": (), "imports": ("import asyncio",),
        "insertions": ({
            "symbol": "compact_thread_context",
            "anchor": "    resolved_app_config = app_config or get_app_config()\n",
            "addition": (
                "    # Manual compaction does not construct the lead Agent, so it must resolve\n"
                "    # the same server-owned binding before registering any memory-flush hook.\n"
                "    # Never rely on a client-selected agent name or change the global config.\n"
                "    from deerflow.runtime.employee_resources import read_employee_publication, scope_employee_config\n"
                "\n"
                "    publication = await asyncio.to_thread(read_employee_publication, thread_id, user_id)\n"
                "    resolved_app_config = scope_employee_config(resolved_app_config, publication)\n"
            ),
        },),
    },
    "packages/harness/deerflow/agents/lead_agent/agent.py": {
        "symbols": (), "imports": ("from deerflow.config.agents_config import validate_agent_source",),
        "adapter": "lead", "adapted_symbols": ("build_middlewares", "_load_enabled_skills_for_tool_policy", "_make_lead_agent"),
    },
    "packages/harness/deerflow/agents/lead_agent/prompt.py": {
        "symbols": ("get_enabled_skills_for_config", "_skill_mutability_label", "get_skills_prompt_section", "get_agent_soul", "_build_self_update_section", "apply_prompt_template"), "imports": (),
    },
    "packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py": {
        "symbols": (), "imports": (), "adapter": "activation", "adapted_symbols": ("SkillActivationMiddleware",),
    },
    "packages/harness/deerflow/agents/middlewares/weknora_scope_middleware.py": {
        "symbols": ("WeKnoraScopeMiddleware",), "imports": (),
    },
    "packages/harness/deerflow/subagents/executor.py": {
        "symbols": (), "imports": (), "adapter": "subagent", "adapted_symbols": ("SubagentExecutor",),
    },
    "packages/harness/deerflow/tools/builtins/task_tool.py": {
        "symbols": (), "imports": (), "adapter": "task", "adapted_symbols": ("task_tool",),
    },
    "packages/harness/deerflow/tools/builtins/setup_agent_tool.py": {
        "symbols": ("setup_agent",),
        "imports": ("from deerflow.config.agents_config import is_personal_agent", "from deerflow.skills.agent_selection import validate_agent_skills"),
    },
    "packages/harness/deerflow/tools/builtins/update_agent_tool.py": {
        "symbols": ("update_agent",),
        "imports": ("from deerflow.persistence.agents.base import parse_agent_config", "from deerflow.persistence.agents.proposals import EmployeeProposalStore", "from deerflow.skills.agent_selection import validate_agent_skills"),
    },
    "packages/harness/deerflow/skills/types.py": {"symbols": ("SkillCategory", "Skill"), "imports": ()},
    "packages/harness/deerflow/skills/storage/__init__.py": {"symbols": ("_user_scoped_storages", "get_or_new_user_skill_storage"), "imports": ()},
    "packages/harness/deerflow/community/aio_sandbox/backend.py": {"symbols": ("SandboxBackend",), "imports": ()},
    "packages/harness/deerflow/community/aio_sandbox/remote_backend.py": {"symbols": ("RemoteSandboxBackend",), "imports": ()},
    "packages/harness/deerflow/sandbox/sandbox_provider.py": {"symbols": ("SandboxProvider",), "imports": ()},
    "packages/harness/deerflow/sandbox/local/local_sandbox_provider.py": {"symbols": ("LocalSandboxProvider",), "imports": ()},
    "packages/harness/deerflow/community/aio_sandbox/local_backend.py": {
        "symbols": (), "imports": (), "adapter": "local_backend", "adapted_symbols": ("LocalContainerBackend",),
    },
    "packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py": {
        "symbols": (), "imports": (), "adapter": "aio_provider", "adapted_symbols": ("AioSandboxProvider",),
    },
    "packages/harness/deerflow/sandbox/tools.py": {
        "symbols": ("_is_disabled_skill_path", "_drop_disabled_skill_paths", "_resolve_skills_path", "_runtime_thread_id", "_check_employee_execution", "_validate_employee_sandbox", "ensure_sandbox_initialized", "ensure_sandbox_initialized_async", "ls_tool", "glob_tool", "grep_tool", "read_current_file_content"),
        "imports": (), "adapter": "sandbox_tools", "adapted_symbols": ("read_file_tool",),
    },
})


def _nodes(text):
    result = {}
    for node in ast.parse(text).body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name is not None:
            if name in result:
                raise ValueError("Ambiguous production symbol: " + name)
            result[name] = node
    return result


def _span(node):
    decorators = getattr(node, "decorator_list", [])
    start = min([node.lineno] + [item.lineno for item in decorators]) - 1
    return start, node.end_lineno


def symbols(text):
    lines = text.splitlines(keepends=True)
    return {name: "".join(lines[slice(*_span(node))]) for name, node in _nodes(text).items()}


def select_symbols(source, candidate, selected, imports):
    original_nodes = _nodes(source)
    candidate_nodes = _nodes(candidate)
    candidates = symbols(candidate)
    if len(set(selected)) != len(selected) or any(name not in candidates for name in selected):
        raise ValueError("Employee candidate symbol missing or duplicated")
    lines = source.splitlines(keepends=True)
    replacements, additions = [], {}
    for name in selected:
        if name in original_nodes:
            start, end = _span(original_nodes[name])
            replacements.append((start, end, candidates[name]))
        else:
            # Preserve definition-time dependencies (aliases, decorators,
            # defaults). Appending new definitions at EOF breaks modules
            # without postponed annotation evaluation, despite valid ASTs.
            following = [key for key, node in candidate_nodes.items() if node.lineno > candidate_nodes[name].lineno and key in original_nodes]
            anchor = _span(original_nodes[following[0]])[0] if following else len(lines)
            additions.setdefault(anchor, []).append((candidate_nodes[name].lineno, candidates[name]))
    for anchor, members in additions.items():
        content = "\n" + "\n".join(item.rstrip("\r\n") + "\n" for _, item in sorted(members)) + "\n"
        replacements.append((anchor, anchor, content))
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement.rstrip("\r\n") + "\n"]
    result = "".join(lines)
    tree = ast.parse(result)
    import_line = 0
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            import_line = node.end_lineno
        elif isinstance(node, ast.ImportFrom) and node.module == "__future__":
            import_line = node.end_lineno
        else:
            break
    lines = result.splitlines(keepends=True)
    lines[import_line:import_line] = ["\n" + "\n".join(imports) + "\n"] if imports else []
    result = "".join(lines)
    ast.parse(result)
    old, new = symbols(source), symbols(result)
    for name, content in old.items():
        if name not in selected and new.get(name) != content:
            raise ValueError("Unrelated production symbol changed: " + name)
    return result


def insert_reviewed(source, candidate, insertions):
    """Add only exact audited blocks inside their named production function."""
    for insertion in insertions:
        name, anchor, addition = (insertion[key] for key in ("symbol", "anchor", "addition"))
        candidate_symbol = symbols(candidate).get(name, "").replace("\r\n", "\n")
        if candidate_symbol.count(anchor + addition) != 1:
            raise ValueError("Reviewed insertion no longer matches candidate: " + name)
        nodes = _nodes(source)
        if name not in nodes:
            raise ValueError("Production insertion anchor function missing: " + name)
        lines = source.splitlines(keepends=True)
        start, end = _span(nodes[name])
        original = "".join(lines[start:end])
        newline = "\r\n" if "\r\n" in original else "\n"
        anchor, addition = anchor.replace("\n", newline), addition.replace("\n", newline)
        if original.count(anchor) != 1 or anchor + addition in original:
            raise ValueError("Production insertion anchor missing, repeated or already applied: " + name)
        lines[start:end] = [original.replace(anchor, anchor + addition, 1)]
        source = "".join(lines)
        ast.parse(source)
    return source


def compose(relative, source, candidate):
    recipe = RECIPES[relative]
    candidate_text = candidate.decode("utf-8")
    result = select_symbols(source.decode("utf-8"), candidate_text, recipe["symbols"], recipe["imports"])
    result = insert_reviewed(result, candidate_text, recipe.get("insertions", ()))
    if recipe.get("adapter"):
        from gateway_runtime_delta import ADAPTERS
        from gateway_sandbox_delta import ADAPTERS as SANDBOX_ADAPTERS

        result = {**ADAPTERS, **SANDBOX_ADAPTERS}[recipe["adapter"]](result, candidate_text)
    old, new = symbols(source.decode("utf-8")), symbols(result)
    touched = set(recipe["symbols"]) | set(recipe.get("adapted_symbols", ())) | {item["symbol"] for item in recipe.get("insertions", ())}
    if any(new.get(name) != content for name, content in old.items() if name not in touched):
        raise ValueError("Unrelated production symbol changed by runtime adapter")
    result = result.encode("utf-8")
    return result, {"path": relative, "source_sha256": hashlib.sha256(source).hexdigest(), "target_sha256": hashlib.sha256(result).hexdigest(),
                    "candidate_sha256": hashlib.sha256(candidate).hexdigest(), "selected_symbols": list(recipe["symbols"]),
                    "runtime_adapter": recipe.get("adapter"), "adapted_symbols": list(recipe.get("adapted_symbols", ())),
                    "anchored_insertions": [item["symbol"] for item in recipe.get("insertions", ())], "unrelated_symbols": "PRESERVED"}
