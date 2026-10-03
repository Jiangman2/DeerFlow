"""Regression tests for the built-in external-agent links."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

NGINX_CONFIGS = (
    "docker/nginx/nginx.conf",
    "docker/nginx/nginx.local.conf",
    "k8s/local-pvc/nginx.conf",
)

CNOOCCLAW_AGENTS = {
    "专业文档解析专家": "https://cnoocclaw.cnooc.com.cn/zywdjxzj/",
    "前期研究报告编写专家": "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/",
    "专业报告校审专家": "https://cnoocclaw.cnooc.com.cn/zjbgjszj/",
    "自动数采专家": "https://cnoocclaw.cnooc.com.cn/zdsczj/",
    "地震数据提频专家": "https://cnoocclaw.cnooc.com.cn/dzsjtpzj/",
    "钻采技术查重智能体": "https://cnoocclaw.cnooc.com.cn/zwjkylxcczs/",
}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_shared_external_agents_use_cnoocclaw_entry_points():
    shared_agents = _read("frontend/src/core/agents/shared-agents.ts")

    for name, url in CNOOCCLAW_AGENTS.items():
        card_definition = rf'name: "{re.escape(name)}",.*?external_url: "{re.escape(url)}"'
        assert re.search(card_definition, shared_agents, re.DOTALL)

    assert "10.57.128.79" not in shared_agents
    assert "?token=" not in shared_agents
    assert 'name: "地震数据分辨率提升工具"' not in shared_agents


def test_agent_cards_open_external_urls_without_proxy_rewriting():
    card_source = _read("frontend/src/components/workspace/agents/agent-card.tsx")
    launch_source = _read("frontend/src/core/agents/external-launch.ts")
    next_config = _read("frontend/next.config.js")

    assert "resolveExternalAgentLaunchHref" in card_source
    assert "return value" in launch_source
    assert "hostname === CNOOCCLAW_EXTERNAL_AGENT_HOST" in launch_source
    assert "hostname === LEGACY_EXTERNAL_AGENT_HOST" in launch_source

    assert "EXTERNAL_AGENT_PROXY_PATHS" not in card_source
    assert "buildExternalAgentHref" not in card_source
    assert "agent-tools" not in next_config
    assert "cnooc.platform_token" not in card_source
    assert "?token=" not in card_source


def test_external_agent_launch_route_reads_http_only_cookie_server_side():
    route_source = _read("frontend/src/app/api/cnooc/external-agents/[agent]/launch/route.ts")
    authorization_source = _read("frontend/src/server/cnooc-auth/platform-token.ts")
    target_source = _read("frontend/src/server/cnooc-auth/external-agent-launch.ts")

    assert "authorizeCnoocPlatformToken" in route_source
    assert "CNOOC_PLATFORM_TOKEN_COOKIE_NAME" in authorization_source
    assert "request.cookies.get" in authorization_source
    assert "Cache-Control" in route_source
    assert "no-store" in route_source
    assert "Referrer-Policy" in route_source
    assert "no-referrer" in route_source
    assert 'searchParams.set("token"' in target_source
    assert "DPORTAL" not in route_source
    assert "DPORTAL" not in target_source


def test_nginx_no_longer_exposes_legacy_agent_tool_proxies():
    for path in NGINX_CONFIGS:
        config = _read(path)
        assert "/agent-tools/" not in config
        assert "cnooc_platform_token" not in config
        if path != "k8s/local-pvc/nginx.conf":
            assert "10.57.128.79" not in config
