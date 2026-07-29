from types import SimpleNamespace

from deerflow.community.tavily import tools


def test_tavily_tls_compat_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TAVILY_TLS_COMPAT", raising=False)
    monkeypatch.setattr(
        tools.ssl,
        "create_default_context",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("default mode must not create a custom TLS context")
        ),
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
        lambda: SimpleNamespace(
            get_tool_config=lambda _name: SimpleNamespace(
                model_extra={"api_key": "tavily-secret"}
            )
        ),
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
