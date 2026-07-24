"""Regression tests for the five data-lake external-agent links."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

NGINX_CONFIGS = (
    "docker/nginx/nginx.conf",
    "docker/nginx/nginx.local.conf",
    "k8s/local-pvc/nginx.conf",
)

DATA_LAKE_AGENTS = {
    "专业文档解析专家": "https://datalake.cnooc/zywdjxzj",
    "前期研究报告编写专家": "https://datalake.cnooc/qqyjbgbxzj",
    "专业报告校审专家": "https://datalake.cnooc/zjbgjszj/",
    "自动数采专家": "https://datalake.cnooc/zdsczj",
    "地震数据提频专家": "https://datalake.cnooc/dzsjtpzj",
}


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_shared_external_agents_use_data_lake_entry_points():
    shared_agents = _read("frontend/src/core/agents/shared-agents.ts")

    for name, url in DATA_LAKE_AGENTS.items():
        card_definition = rf'name: "{re.escape(name)}",.*?external_url: "{re.escape(url)}"'
        assert re.search(card_definition, shared_agents, re.DOTALL)

    assert "10.57.128.79" not in shared_agents
    assert "?token=" not in shared_agents
    assert 'name: "地震数据分辨率提升工具"' not in shared_agents


def test_agent_cards_open_external_urls_without_proxy_rewriting():
    card_source = _read("frontend/src/components/workspace/agents/agent-card.tsx")
    next_config = _read("frontend/next.config.js")

    for url in DATA_LAKE_AGENTS.values():
        assert url in card_source

    assert "EXTERNAL_AGENT_PROXY_PATHS" not in card_source
    assert "buildExternalAgentHref" not in card_source
    assert "agent-tools" not in next_config
    assert "cnooc.platform_token" not in card_source
    assert "?token=" not in card_source


def test_nginx_no_longer_exposes_legacy_agent_tool_proxies():
    for path in NGINX_CONFIGS:
        config = _read(path)
        assert "/agent-tools/" not in config
        assert "cnooc_platform_token" not in config
        assert "10.57.128.79" not in config
