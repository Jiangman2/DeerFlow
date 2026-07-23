"""Security regression tests for data-lake token forwarding to agent tools."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

NGINX_CONFIGS = (
    "docker/nginx/nginx.conf",
    "docker/nginx/nginx.local.conf",
    "k8s/local-pvc/nginx.conf",
)

AGENT_TOOL_PATHS = (
    "/agent-tools/doc-parse/",
    "/agent-tools/seismic-resolution/",
    "/agent-tools/report-review/",
    "/agent-tools/auto-data-collection/",
    "/agent-tools/report-writing/",
)


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_agent_tool_proxies_forward_platform_token_as_bearer_header():
    for path in NGINX_CONFIGS:
        config = _read(path)
        assert "cnooc\\.platform_token=([^;]+)" in config
        assert "map $cnooc_platform_token $cnooc_platform_authorization" in config
        for location in AGENT_TOOL_PATHS:
            block_start = config.index(f"location {location}")
            block_end = config.index("\n        }", block_start)
            block = config[block_start:block_end]
            assert "proxy_set_header Authorization $cnooc_platform_authorization;" in block


def test_agent_tool_links_do_not_put_platform_token_in_url():
    card_source = _read("frontend/src/components/workspace/agents/agent-card.tsx")

    assert "cnooc.platform_token" not in card_source
    assert "?token=" not in card_source
