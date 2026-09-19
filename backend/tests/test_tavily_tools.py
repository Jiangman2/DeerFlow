from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.utils.function_calling import convert_to_openai_tool

from deerflow.community.tavily import tools


def test_tavily_tls_compat_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TAVILY_TLS_COMPAT", raising=False)
    monkeypatch.setattr(
        tools.ssl,
        "create_default_context",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("default mode must not create a custom TLS context")),
    )

    assert tools._build_tavily_session() is None


def test_tavily_tls_compat_uses_enterprise_ca_and_security_level_one(
    monkeypatch,
    tmp_path,
):
    ca_file = tmp_path / "enterprise-ca.crt"
    ca_file.write_text("test-only-ca", encoding="utf-8")

    class _Context:
        def __init__(self):
            self.ciphers = []

        def set_ciphers(self, value):
            self.ciphers.append(value)

    context = _Context()
    create_calls = []

    def _create_default_context(**kwargs):
        create_calls.append(kwargs)
        return context

    monkeypatch.setenv("TAVILY_TLS_COMPAT", "true")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_file))
    monkeypatch.setattr(
        tools.ssl,
        "create_default_context",
        _create_default_context,
    )

    session = tools._build_tavily_session()

    assert create_calls == [{"cafile": str(ca_file)}]
    assert context.ciphers == ["DEFAULT:@SECLEVEL=1"]
    assert session is not None
    adapter = session.adapters["https://"]
    assert adapter._tavily_ssl_context is context


def test_tavily_client_receives_scoped_compat_session(monkeypatch):
    compat_session = object()
    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("TAVILY_TLS_COMPAT", "true")
    monkeypatch.setattr(
        tools,
        "get_app_config",
        lambda: SimpleNamespace(get_tool_config=lambda _name: SimpleNamespace(model_extra={"api_key": "tavily-secret"})),
    )
    monkeypatch.setattr(
        tools,
        "_build_tavily_session",
        lambda: compat_session,
    )
    monkeypatch.setattr(tools, "TavilyClient", _Client)

    tools._get_tavily_client()

    assert captured == {
        "api_key": "tavily-secret",
        "session": compat_session,
    }


def test_tavily_clients_use_their_own_tool_credentials(monkeypatch):
    compat_session = object()
    captured = []

    class _Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(
        tools,
        "get_app_config",
        lambda: SimpleNamespace(get_tool_config=lambda name: SimpleNamespace(model_extra={"api_key": f"{name}-secret"})),
    )
    monkeypatch.setattr(tools, "_build_tavily_session", lambda: compat_session)
    monkeypatch.setattr(tools, "TavilyClient", _Client)

    tools._get_tavily_client()
    tools._get_tavily_client("web_fetch")

    assert captured == [
        {"api_key": "web_search-secret", "session": compat_session},
        {"api_key": "web_fetch-secret", "session": compat_session},
    ]


def test_web_search_forwards_deployment_domain_filters(monkeypatch):
    client = MagicMock()
    client.search.return_value = {"results": []}
    config = SimpleNamespace(
        model_extra={
            "max_results": 3,
            "include_domains": ["docs.example.com"],
            "exclude_domains": ["archive.example.com"],
        }
    )
    monkeypatch.setattr(
        tools,
        "get_app_config",
        lambda: SimpleNamespace(get_tool_config=lambda _name: config),
    )
    monkeypatch.setattr(tools, "_get_tavily_client", lambda: client)

    assert tools.web_search_tool.invoke({"query": "documentation"}) == "[]"
    client.search.assert_called_once_with(
        "documentation",
        max_results=3,
        include_domains=["docs.example.com"],
        exclude_domains=["archive.example.com"],
        include_domains_mode="filter",
    )


def test_web_search_preserves_explicit_empty_domain_filters(monkeypatch):
    client = MagicMock()
    client.search.return_value = {"results": []}
    config = SimpleNamespace(
        model_extra={
            "include_domains": [],
            "exclude_domains": [],
        }
    )
    monkeypatch.setattr(
        tools,
        "get_app_config",
        lambda: SimpleNamespace(get_tool_config=lambda _name: config),
    )
    monkeypatch.setattr(tools, "_get_tavily_client", lambda: client)

    tools.web_search_tool.invoke({"query": "documentation"})

    client.search.assert_called_once_with(
        "documentation",
        max_results=5,
        include_domains=[],
        exclude_domains=[],
    )


def test_web_search_keeps_domain_filters_out_of_model_schema():
    parameters = convert_to_openai_tool(tools.web_search_tool)["function"]["parameters"]

    assert set(parameters["properties"]) == {"query"}
    assert parameters["required"] == ["query"]


def test_web_fetch_falls_back_to_result_url_when_title_is_missing(monkeypatch):
    client = MagicMock()
    client.extract.return_value = {
        "results": [
            {
                "url": "https://example.com/report",
                "raw_content": "Important findings.",
            }
        ],
        "failed_results": [],
    }

    def _client_for(tool_name="web_search"):
        assert tool_name == "web_fetch"
        return client

    monkeypatch.setattr(tools, "_get_tavily_client", _client_for)

    output = tools.web_fetch_tool.invoke({"url": "https://example.com/requested"})

    assert output == "# https://example.com/report\n\nImportant findings."
    client.extract.assert_called_once_with(["https://example.com/requested"])
