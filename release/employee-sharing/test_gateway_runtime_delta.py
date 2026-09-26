"""Employee runtime edits must not bring unrelated checkout upgrades along."""

import ast
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def composer(monkeypatch):
    directory = Path(__file__).parent
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("employee_runtime_composer_tests", directory / "gateway_delta.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def composed(composer, path):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-supplement") / "gateway"
    if not baseline.is_dir():
        pytest.skip("requires supplied production source audit")
    source = (baseline / path).read_bytes()
    candidate = (root / "backend" / path).read_bytes()
    result, receipt = composer.compose(path, source, candidate)
    ast.parse(result)
    return source.decode(), result.decode(), receipt


def test_lead_keeps_production_tool_policy_while_binding_employee(composer):
    path = "packages/harness/deerflow/agents/lead_agent/agent.py"
    source, result, receipt = composed(composer, path)
    assert "EmployeePublicationBindings" in result
    assert "scope_employee_config(resolved_app_config, publication)" in result
    assert "scope_employee_tools(filtered, publication, knowledge_scope=knowledge_scope)" in result
    assert "SkillToolPolicyMiddleware" not in result
    assert "slash_source_owner_token" not in result
    assert "filter_tools_by_skill_allowed_tools(raw_tools + extra_tools" in result
    assert composer.symbols(result)["_restore_task_tool_for_subagent_mode"] == composer.symbols(source)["_restore_task_tool_for_subagent_mode"]
    assert receipt["runtime_adapter"] == "lead"


def test_manual_compaction_only_scopes_server_owned_employee_memory(composer):
    path = "packages/harness/deerflow/runtime/context_compaction.py"
    source, result, receipt = composed(composer, path)
    assert receipt["anchored_insertions"] == ["compact_thread_context"]
    assert "asyncio.to_thread(read_employee_publication, thread_id, user_id)" in result
    assert "scope_employee_config(resolved_app_config, publication)" in result
    assert result.index("scope_employee_config(resolved_app_config, publication)") < result.index("middleware = _create_compaction_middleware")
    assert composer.symbols(source)["_checkpoint_namespace"] == composer.symbols(result)["_checkpoint_namespace"]


def test_subagent_keeps_stream_protocol_and_loads_published_skills(composer):
    path = "packages/harness/deerflow/subagents/executor.py"
    source, result, _ = composed(composer, path)
    assert "scope_employee_tools(tools, self.employee_publication)" in result
    assert "EmployeePublicationMiddleware(thread_id=self.thread_id" in result
    assert "get_thread_published_skill_storage" in result
    assert "close_agent_stream" not in result
    assert "normalize_authz_attributes" not in result
    old_class = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "SubagentExecutor")
    new_class = next(n for n in ast.parse(result).body if isinstance(n, ast.ClassDef) and n.name == "SubagentExecutor")
    old = {n.name: ast.dump(n) for n in old_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    new = {n.name: ast.dump(n) for n in new_class.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert old["_aexecute"] == new["_aexecute"]


def test_activation_retains_baseline_secret_contract(composer):
    _, result, _ = composed(composer, "packages/harness/deerflow/agents/middlewares/skill_activation_middleware.py")
    assert "self._thread_id = thread_id" in result
    assert "get_thread_published_skill_storage" in result
    assert "_SLASH_SECRET_SOURCE_KEY" in result
    assert "write_slash_skill_source_path" not in result
    assert "slash_source_owner_token" not in result


def test_task_adds_only_employee_knowledge_filter(composer):
    source, result, _ = composed(composer, "packages/harness/deerflow/tools/builtins/task_tool.py")
    assert 'metadata.get("employee_knowledge_disabled") is True' in result
    gate = "tools = filter_tools_by_datalake_scope(tools, enabled=datalake_enabled)"
    assert result.count(gate) == source.count(gate)
    assert "tools = get_available_tools(**available_tools_kwargs)" in result
    assert "run_assembly" not in result
    assert "authz_attributes" not in result


def test_runtime_adapters_reject_unknown_or_duplicated_anchor(composer):
    from gateway_runtime_delta import replace_once

    source = "def chosen():\n    old()\n\ndef other():\n    old()\n"
    result = replace_once(source, "chosen", "    old()\n", "    employee()\n")
    assert result.endswith("def other():\n    old()\n")
    for changed in (source.replace("    old()", "    changed()"), source.replace("    old()", "    old()\n    old()")):
        with pytest.raises(ValueError, match="anchor"):
            replace_once(changed, "chosen", "    old()\n", "    employee()\n")


def test_aio_employee_projection_keeps_production_locks_and_teardown(composer):
    from gateway_runtime_delta import definition

    source, result, _ = composed(composer, "packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py")
    assert "get_thread_published_skill_storage" in result
    assert "self.validate_employee_skills(sandbox_id, thread_id" in result
    assert "asyncio.to_thread(self.validate_employee_skills" in result
    assert "_acquire_serializer" not in result
    assert "redis" not in result.lower()
    for method in ("_acquire_internal", "_acquire_internal_async", "_destroy_warm_entry", "release", "shutdown"):
        assert definition(result, "AioSandboxProvider." + method) == definition(source, "AioSandboxProvider." + method)


def test_container_backend_only_adds_mount_validation(composer):
    from gateway_runtime_delta import definition

    source, result, _ = composed(composer, "packages/harness/deerflow/community/aio_sandbox/local_backend.py")
    assert "def validate_employee_skills(" in result
    assert definition(source, "LocalContainerBackend._stop_container") == definition(result, "LocalContainerBackend._stop_container")
    assert "_STOP_TIMEOUT_SECONDS" not in result


def test_read_file_retains_production_output_format(composer):
    from gateway_runtime_delta import definition

    source, result, _ = composed(composer, "packages/harness/deerflow/sandbox/tools.py")
    before = definition(source, "read_file_tool")
    after = definition(result, "read_file_tool")
    assert after == before.replace("_is_disabled_skill_path(path, user_id=resolve_runtime_user_id(runtime))", "_is_disabled_skill_path(path, user_id=resolve_runtime_user_id(runtime), thread_id=_runtime_thread_id(runtime))")
    assert "normalize_mask_tail" not in result
