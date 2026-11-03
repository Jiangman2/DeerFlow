"""Registration freeze applies before deferred discovery, without network I/O."""
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from deerflow.tools import tools as registry
from deerflow.tools.builtins.tool_search import build_deferred_tool_setup


def tool(name):
    def call(url: str) -> str:
        return url
    return StructuredTool.from_function(call, name=name, description='Synthetic tool for registration QA')


@pytest.fixture
def setup_registry(monkeypatch):
    monkeypatch.delenv('DEERFLOW_BROWSER_NAVIGATE_ENABLED', raising=False)
    monkeypatch.setattr(registry, 'is_host_bash_allowed', lambda config: True)
    monkeypatch.setattr(registry, 'BUILTIN_TOOLS', [])
    monkeypatch.setattr(registry, 'SUBAGENT_TOOLS', [])
    monkeypatch.setattr('deerflow.community.media_generation.get_configured_media_generation_tools', lambda: [])
    return SimpleNamespace(tools=[], models=[], acp_agents={}, get_model_config=lambda name: None)


@pytest.mark.parametrize('name', ['browser_navigate', 'browser_browser_navigate', 'playwright_browser_navigate',
                                  'mcp__browser__browser_navigate'])
def test_configured_navigation_is_not_imported(setup_registry, monkeypatch, name):
    setup_registry.tools = [SimpleNamespace(name=name, use='should:not_resolve', group='browser')]
    def fail(*args):
        raise AssertionError('FROZEN_TOOL_MUST_NOT_BE_IMPORTED')
    monkeypatch.setattr(registry, 'resolve_variable', fail)
    assert registry.get_available_tools(app_config=setup_registry, include_mcp=False) == []


def test_alias_resolving_to_navigation_is_removed(setup_registry, monkeypatch):
    setup_registry.tools = [SimpleNamespace(name='open_page', use='test:alias', group='browser')]
    monkeypatch.setattr(registry, 'resolve_variable', lambda *args: tool('browser_navigate'))
    assert registry.get_available_tools(app_config=setup_registry, include_mcp=False) == []


@pytest.mark.parametrize('name', ['browser_snapshot', 'browser_click', 'browser_navigate_back', 'web_search', 'web_fetch'])
def test_other_tools_unchanged(setup_registry, monkeypatch, name):
    setup_registry.tools = [SimpleNamespace(name=name, use='test:tool', group='browser')]
    monkeypatch.setattr(registry, 'resolve_variable', lambda *args: tool(name))
    assert [t.name for t in registry.get_available_tools(app_config=setup_registry, include_mcp=False)] == [name]


def test_cached_mcp_navigation_absent_from_deferred_catalog(setup_registry, monkeypatch):
    from deerflow.config.extensions_config import ExtensionsConfig
    monkeypatch.setattr(ExtensionsConfig, 'from_file', lambda: SimpleNamespace(get_enabled_mcp_servers=lambda: {'browser': {}}))
    monkeypatch.setattr('deerflow.mcp.cache.get_cached_mcp_tools', lambda: [tool('browser_browser_navigate'), tool('browser_snapshot')])
    available = registry.get_available_tools(app_config=setup_registry, include_mcp=True)
    assert [t.name for t in available] == ['browser_snapshot']
    setup = build_deferred_tool_setup(available, enabled=True)
    assert setup.deferred_names == frozenset({'browser_snapshot'})


def test_explicit_operator_restore(setup_registry, monkeypatch):
    monkeypatch.setenv('DEERFLOW_BROWSER_NAVIGATE_ENABLED', 'true')
    setup_registry.tools = [SimpleNamespace(name='browser_navigate', use='test:tool', group='browser')]
    monkeypatch.setattr(registry, 'resolve_variable', lambda *args: tool('browser_navigate'))
    assert [t.name for t in registry.get_available_tools(app_config=setup_registry, include_mcp=False)] == ['browser_navigate']


@pytest.mark.parametrize('value', ['', 'false', '0', 'invalid'])
def test_other_flag_values_keep_freeze(monkeypatch, value):
    monkeypatch.setenv('DEERFLOW_BROWSER_NAVIGATE_ENABLED', value)
    assert registry._browser_navigation_frozen('browser_navigate')
