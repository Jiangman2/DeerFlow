"""Real subagent discovery/activation against a thread-bound published snapshot."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware
from deerflow.agents.middlewares.skill_tool_policy_middleware import SkillToolPolicyMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths
from deerflow.skills.agent_publication import PublishedAgentSkillStorage, stage_agent_skills
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.subagents.config import SubagentConfig


@pytest.fixture
def executor_module(monkeypatch):
    name = "_v21_lazy_skill_executor"
    path = Path(__file__).parents[1] / "packages/harness/deerflow/subagents/executor.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    from deerflow.trace_context import request_trace_context

    try:
        with request_trace_context():
            yield module
    finally:
        module._shutdown_isolated_subagent_loop()
        module.atexit.unregister(module._shutdown_isolated_subagent_loop)


@pytest.fixture
def publication(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path / "state")
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.skills.agent_publication.get_paths", lambda: paths)
    config = AppConfig.model_validate(
        {
            "models": [{"name": "test", "use": "langchain_openai:ChatOpenAI", "model": "test"}],
            "sandbox": {"use": "test"},
            "skills": {"path": str(tmp_path / "skills"), "deferred_discovery": True},
            "guardrails": {"enabled": False},
        }
    )
    owner = UserScopedSkillStorage("alice", app_config=config)
    viewer = UserScopedSkillStorage("bob", app_config=config)
    for store, name, description, body in (
        (owner, "analysis", "Frozen owner description", "OWNER_BODY_SENTINEL"),
        (owner, "other", "Other published skill", "OTHER_BODY_SENTINEL"),
        (viewer, "analysis", "VIEWER_DESCRIPTION_SENTINEL", "VIEWER_BODY_SENTINEL"),
    ):
        directory = store.get_user_custom_root() / name
        directory.mkdir(parents=True)
        metadata = yaml.safe_dump({"name": name, "description": description, "allowed-tools": ["bash"]})
        (directory / "SKILL.md").write_text("---\n" + metadata + "---\n" + body, encoding="utf-8")
    monkeypatch.setattr("deerflow.skills.agent_publication.get_or_new_user_skill_storage", lambda user_id, **kwargs: owner if user_id == "alice" else viewer)
    target = paths.team_agent_dir("employee")
    digest = stage_agent_skills(target, ["analysis", "other"], user_id="alice", app_config=config)
    (target / "config.yaml").write_text(yaml.safe_dump({"skills": ["analysis", "other"], "skills_revision": digest}), encoding="utf-8")
    storage = PublishedAgentSkillStorage("bob", "business-thread", publication=target, revision=digest, app_config=config)
    reads = []

    def resolve(thread_id, *, user_id, app_config=None):
        assert (thread_id, user_id, app_config) == ("business-thread", "bob", config)
        reads.append((thread_id, user_id))
        return storage

    monkeypatch.setattr("deerflow.skills.agent_publication.get_thread_published_skill_storage", resolve)
    # No admitted run or database is created by this unit fixture. The actual
    # published storage below still verifies its immutable manifest and digest.
    monkeypatch.setattr("deerflow.runtime.employee_resources.read_employee_publication", lambda *args: None)
    return SimpleNamespace(config=config, storage=storage, reads=reads, target=target)


def make_executor(module, publication, *, skills=("analysis",), **config):
    return module.SubagentExecutor(
        SubagentConfig(name="tester", description="Test", system_prompt="SYSTEM", model="test", skills=None if skills is None else list(skills), **config),
        tools=[SimpleNamespace(name=name) for name in ("bash", "read_file", "web_search")],
        app_config=publication.config,
        user_id="bob",
        thread_id="business-thread",
    )


def build_chain(executor, module, monkeypatch):
    captured = {}
    monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(module, "create_agent", lambda **kwargs: captured.update(kwargs))
    executor._create_agent()
    chain = captured["middleware"]
    activation = next(m for m in chain if isinstance(m, SkillActivationMiddleware))
    policy = next(m for m in chain if isinstance(m, SkillToolPolicyMiddleware))
    assert chain.index(activation) < chain.index(policy)
    return activation, policy


def test_authorization_keeps_publication_and_filters_child_mcp(executor_module, publication, monkeypatch):
    from langchain_core.tools import StructuredTool

    from deerflow.config.authorization_config import AuthorizationConfig
    from deerflow.guardrails.middleware import GuardrailMiddleware
    from deerflow.tools.mcp_metadata import tag_mcp_tool

    publication.config.authorization = AuthorizationConfig.model_validate(
        {"enabled": True, "provider": {"use": "deerflow.authz.rbac:RbacAuthorizationProvider", "config": {"roles": {"user": {"tools": {"allow": ["read_file", "mcp_safe"]}}}}}}
    )
    publication.config.tool_search.enabled = True
    executor = make_executor(executor_module, publication)
    for name in ("mcp_safe", "mcp_denied"):
        executor._base_tools.append(tag_mcp_tool(StructuredTool.from_function(lambda: "test", name=name, description=name)))
    state, tools, setup = asyncio.run(executor._build_initial_state("use published employee"))
    assert {t.name for t in tools} == {"read_file", "mcp_safe", "tool_search"}
    assert setup.deferred_names == {"mcp_safe"}
    assert executor._available_skill_names == {"analysis"}
    assert "BODY_SENTINEL" not in str(state)
    assert publication.reads
    monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(executor_module, "create_agent", lambda **kwargs: kwargs)
    built = executor._create_agent(tools, deferred_setup=setup)
    guard = next(m for m in built["middleware"] if isinstance(m, GuardrailMiddleware))
    assert guard.provider._provider is executor._authorization_provider
    activation = next(m for m in built["middleware"] if isinstance(m, SkillActivationMiddleware))
    assert activation._available_skills == {"analysis"}


@pytest.mark.anyio
async def test_child_provider_is_constructed_on_execution_loop(executor_module, publication, monkeypatch):
    from deerflow.authz.provider import AuthzDecision
    from deerflow.config.authorization_config import AuthorizationConfig

    loop = asyncio.get_running_loop()
    seen = []

    class Provider:
        name = "loop-bound"

        def __init__(self):
            seen.append(asyncio.get_running_loop())

        def authorize(self, request):
            return AuthzDecision(allow=True)

        async def aauthorize(self, request):
            return self.authorize(request)

        def filter_resources(self, principal, resource_type, candidates):
            assert asyncio.get_running_loop() is loop
            assert principal.user_id == "bob"
            return candidates

    publication.config.authorization = AuthorizationConfig.model_validate({"enabled": True, "provider": {"use": "test:Provider"}})
    monkeypatch.setattr("deerflow.authz.runtime.resolve_variable", lambda *args, **kwargs: Provider)
    executor = make_executor(executor_module, publication)
    await executor._build_initial_state("task")
    assert seen == [loop]


@pytest.mark.parametrize("deferred", [True, False])
def test_metadata_is_lazy_and_publication_bound(executor_module, publication, deferred):
    publication.config.skills.deferred_discovery = deferred
    executor = make_executor(executor_module, publication)
    state, tools, _ = asyncio.run(executor._build_initial_state("research"))
    system = [m for m in state["messages"] if isinstance(m, SystemMessage)]
    assert len(system) == 1
    assert "BODY_SENTINEL" not in system[0].content
    assert "VIEWER_DESCRIPTION_SENTINEL" not in system[0].content
    assert executor._available_skill_names == {"analysis"}
    assert {t.name for t in tools} == {"bash", "read_file", "web_search"} | ({"describe_skill"} if deferred else set())
    if deferred:
        describe = next(t for t in tools if t.name == "describe_skill")
        response = describe.invoke({"name": "describe_skill", "type": "tool_call", "id": "desc", "args": {"name": "select:analysis,other"}})
        text = response.update["messages"][0].content
        assert "Frozen owner description" in text
        assert "Other published skill" not in text
        assert "BODY_SENTINEL" not in text
    else:
        assert "Frozen owner description" in system[0].content
        assert "Other published skill" not in system[0].content
    assert publication.reads


@pytest.mark.parametrize("skills,expected", [(None, {"analysis", "other"}), ((), set()), (("missing",), set())])
def test_catalog_controls_activation_not_passive_tools(executor_module, publication, monkeypatch, skills, expected):
    executor = make_executor(executor_module, publication, skills=skills, disallowed_tools=["web_search"])
    _, tools, _ = asyncio.run(executor._build_initial_state("task"))
    assert executor._available_skill_names == expected
    assert "web_search" not in {t.name for t in tools}
    activation, policy = build_chain(executor, executor_module, monkeypatch)
    for middleware in (activation, policy):
        assert middleware._available_skills == expected
        assert middleware._user_id == "bob"
        assert middleware._thread_id == "business-thread"
    if not expected:
        assert activation._resolve_activation("/analysis run").activation is None


def test_real_slash_activation_applies_policy_without_viewer_fallback(executor_module, publication, monkeypatch):
    executor = make_executor(executor_module, publication)
    _, tools, _ = asyncio.run(executor._build_initial_state("task"))
    activation, policy = build_chain(executor, executor_module, monkeypatch)
    context = {}
    request = ModelRequest(model=object(), tools=tools, messages=[HumanMessage(content="/analysis run")], state={}, runtime=SimpleNamespace(context=context))
    prepared = activation.wrap_model_call(request, lambda req: req)
    assert "OWNER_BODY_SENTINEL" in str(prepared.messages)
    assert "VIEWER_BODY_SENTINEL" not in str(prepared.messages)
    filtered = policy.wrap_model_call(prepared, lambda req: req)
    assert "web_search" not in {t.name for t in filtered.tools}
    assert "bash" in {t.name for t in filtered.tools}
    denied = SimpleNamespace(tool_call={"name": "web_search", "id": "blocked", "args": {}}, state={}, runtime=request.runtime)
    assert policy.wrap_tool_call(denied, lambda _: pytest.fail("denied tool executed")).status == "error"
    assert activation._resolve_activation("/other run").activation is None
    # A second child must not trust an activation token from the first child.
    activation2, policy2 = build_chain(executor, executor_module, monkeypatch)
    assert activation._slash_source_owner_token == policy._slash_source_owner_token
    assert activation2._slash_source_owner_token == policy2._slash_source_owner_token
    assert activation2._slash_source_owner_token != activation._slash_source_owner_token


def test_autonomous_read_context_enforces_execution_policy(executor_module, publication, monkeypatch):
    executor = make_executor(executor_module, publication)
    _, tools, _ = asyncio.run(executor._build_initial_state("task"))
    _, policy = build_chain(executor, executor_module, monkeypatch)
    state = {"skill_context": [{"path": "/mnt/skills/custom/analysis/SKILL.md"}]}
    runtime = SimpleNamespace(context={})
    request = ModelRequest(model=object(), tools=tools, state=state, messages=[], runtime=runtime)
    filtered = policy.wrap_model_call(request, lambda req: req)
    assert "web_search" not in {t.name for t in filtered.tools}
    allowed = SimpleNamespace(tool_call={"name": "bash", "id": "ok", "args": {}}, state=state, runtime=runtime)
    assert policy.wrap_tool_call(allowed, lambda _: "executed") == "executed"


@pytest.mark.parametrize("allowed_mcp", [True, False])
def test_mcp_discovery_preserves_config_and_active_skill_boundary(executor_module, publication, monkeypatch, allowed_mcp):
    from langchain_core.tools import tool

    from deerflow.tools.mcp_metadata import tag_mcp_tool

    @tool
    def mcp_calc(value: int) -> str:
        """An MCP calculator."""
        return str(value)

    tag_mcp_tool(mcp_calc)
    publication.config.tool_search.enabled = True
    executor = executor_module.SubagentExecutor(
        SubagentConfig(name="tester", description="Test", system_prompt="SYSTEM", model="test", skills=["analysis"], disallowed_tools=[] if allowed_mcp else ["mcp_calc"]),
        tools=[mcp_calc],
        app_config=publication.config,
        user_id="bob",
        thread_id="business-thread",
        sandbox_state={"sandbox_id": "parent-sandbox"},
        thread_data={"workspace_path": "/mnt/user-data/workspace"},
    )
    state, tools, deferred = asyncio.run(executor._build_initial_state("task"))
    assert deferred.deferred_names == (frozenset({"mcp_calc"}) if allowed_mcp else frozenset())
    assert state["sandbox"] == executor.sandbox_state
    assert state["thread_data"] == executor.thread_data
    assert [t.name for t in executor._base_tools] == (["mcp_calc"] if allowed_mcp else [])
    _, repeated_tools, _ = asyncio.run(executor._build_initial_state("task"))
    assert [t.name for t in repeated_tools].count("describe_skill") == 1
    activation, policy = build_chain(executor, executor_module, monkeypatch)
    request = ModelRequest(model=object(), tools=tools, messages=[HumanMessage(content="/analysis run")], state={}, runtime=SimpleNamespace(context={}))
    prepared = activation.wrap_model_call(request, lambda req: req)
    assert "mcp_calc" not in {t.name for t in policy.wrap_model_call(prepared, lambda req: req).tools}
    denied = SimpleNamespace(tool_call={"name": "mcp_calc", "id": "promoted", "args": {"value": 1}}, state={}, runtime=request.runtime)
    assert policy.wrap_tool_call(denied, lambda _: pytest.fail("promotion widened skill policy")).status == "error"


@pytest.mark.parametrize("phase", ["discovery", "activation", "tool_policy"])
def test_revoked_publication_fails_closed(executor_module, publication, monkeypatch, phase):
    executor = make_executor(executor_module, publication)
    if phase != "discovery":
        asyncio.run(executor._build_initial_state("task"))
        activation, policy = build_chain(executor, executor_module, monkeypatch)
    (publication.target / "config.yaml").unlink()
    if phase == "discovery":
        with pytest.raises(ValueError, match="publication"):
            asyncio.run(executor._build_initial_state("task"))
    elif phase == "activation":
        with pytest.raises(ValueError, match="publication"):
            activation._resolve_activation("/analysis run")
    else:
        request = SimpleNamespace(tool_call={"name": "bash", "id": "revoked", "args": {}}, state={"skill_context": [{"path": "/mnt/skills/custom/analysis/SKILL.md"}]}, runtime=SimpleNamespace(context={}))
        result = policy.wrap_tool_call(request, lambda _: pytest.fail("revoked skill executed"))
        assert result.status == "error"
