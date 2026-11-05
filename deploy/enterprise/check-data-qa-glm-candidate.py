"""Isolated candidate Job: config/import/offline checks only, never a server."""


def check_candidate(files, config_hash, require):
    import hashlib
    import importlib
    import os
    import pathlib
    import socket
    from types import SimpleNamespace

    # Deny process-originated network even if an imported module changes later.
    def no_network(*args, **kwargs):
        raise RuntimeError("CANDIDATE_NETWORK_FORBIDDEN")

    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    for path, expected in files.items():
        p = pathlib.Path("/" + path)
        require(
            not p.is_symlink() and p.stat().st_size == expected["bytes"],
            "CANDIDATE_SOURCE_SIZE",
        )
        require(
            hashlib.sha256(p.read_bytes()).hexdigest() == expected["sha256"],
            "CANDIDATE_SOURCE_HASH",
        )

    import yaml

    from deerflow.config.app_config import AppConfig
    from deerflow.config.datalake_tools import RETIRED_DATALAKE_TOOL_NAMES

    raw = pathlib.Path("/candidate/config.yaml").read_bytes()
    require(hashlib.sha256(raw).hexdigest() == config_hash, "CANDIDATE_CONFIG_HASH")
    source = yaml.safe_load(raw)
    resolved = AppConfig.resolve_env_variables(source)
    config = AppConfig.model_validate(resolved)
    names = sorted(t.name for t in config.tools if t.name.startswith("datalake_"))
    require(names == ["datalake_data_qa", "datalake_smart_qa"], "CANDIDATE_TOOL_NAMES")
    new = config.get_tool_config("datalake_data_qa").model_dump()
    require(
        new.get("api_key") == os.environ["APPFORGE_DATA_QA_API_KEY"]
        and bool(new["api_key"]),
        "CANDIDATE_KEY_REFERENCE",
    )
    require(
        new.get("app_id") == "app-v16p2syh"
        and new.get("api_url") == "http://10.77.124.142:9080"
        and new.get("trust_env") is False,
        "CANDIDATE_ENDPOINT",
    )
    require(
        not any(t.name in RETIRED_DATALAKE_TOOL_NAMES for t in config.tools),
        "RETIRED_TOOLS_PRESENT",
    )
    for tool in config.tools:
        if tool.name in names:
            module, name = tool.use.split(":")
            require(
                getattr(importlib.import_module(module), name).name == tool.name,
                "CANDIDATE_TOOL_EXPORT",
            )

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from deerflow.agents.middlewares.datalake_source_middleware import (
        DataLakeSourceMiddleware,
    )
    from deerflow.runtime.datalake_policy import ACCESS_HINT, query_scope

    middleware = DataLakeSourceMiddleware()
    state = {
        "messages": [HumanMessage(content="查询作业井基本信息表中设计井深最大的2口井")]
    }
    runtime = SimpleNamespace(context={"datalake_enabled": False, "run_id": "offline"})
    hint = middleware.before_model(state, runtime)
    require(
        hint.get("jump_to") == "end" and hint["messages"][0].content == ACCESS_HINT,
        "CANDIDATE_ACCESS_HINT",
    )
    runtime.context["datalake_enabled"] = True
    denied = 0
    for name in (
        "web_search",
        "web_fetch",
        "browser_navigate",
        "bash",
        "python",
        "external_mcp_search",
        "tool_search",
    ):
        request = SimpleNamespace(
            state=state,
            runtime=runtime,
            tool_call={"id": "offline", "name": name, "args": {}},
        )
        result = middleware.wrap_tool_call(request, no_network)
        require(
            "datalake_external_fallback_forbidden"
            in result.update["messages"][0].content,
            "CANDIDATE_EXTERNAL_FALLBACK",
        )
        denied += 1
    for outcome in ("empty", "failed", "http_403"):
        attempted = {
            "messages": [
                HumanMessage(content="帮我查这些井"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "datalake_data_qa", "id": "q", "args": {}}],
                ),
                ToolMessage(content=outcome, name="datalake_data_qa", tool_call_id="q"),
            ]
        }
        require(
            query_scope(attempted, runtime.context)["required"],
            "CANDIDATE_EMPTY_ERROR_SCOPE",
        )
    require(
        query_scope(
            {"messages": [HumanMessage(content="使用公开搜索")]},
            {"datalake_query_only": True},
        )["required"],
        "CANDIDATE_DELEGATE_SCOPE",
    )
    # Load the actual delegate/registry modules; do not create an agent or tools.
    for module in (
        "deerflow.tools.tools",
        "deerflow.tools.builtins.task_tool",
        "deerflow.subagents.executor",
    ):
        importlib.import_module(module)
    model_files = {p: v for p, v in files.items() if "/models/" in p}
    stream = check_stream_runtime(True, model_files, require)  # noqa: F821 -- packaged pinned GLM checker
    return dict(
        status="PASS",
        source_files=len(files),
        data_tools=names,
        dedicated_key_resolved=True,
        disabled_button_hint="PASS",
        external_paths_denied=denied,
        empty_error_and_delegate_scope="PASS",
        stream_completion=stream,
        provider_requests=0,
        business_values_verified=False,
    )


def candidate_entry(files, config_hash):
    import contextlib
    import io
    import json

    class CandidateRefused(Exception):
        pass

    def require(condition, code):
        if not condition:
            raise CandidateRefused(code)

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):  # fmt: skip -- compile checked on Python 3.7
            value = check_candidate(files, config_hash, require)
    except Exception as exc:
        # No exception message, config values, request text or credentials.
        value = dict(status="FAIL", error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, CandidateRefused) else "DETAILS_WITHHELD", provider_requests=0)
    print(json.dumps(value, sort_keys=True), flush=True)
    raise SystemExit(0 if value["status"] == "PASS" else 1)
