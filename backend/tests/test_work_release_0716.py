"""Work candidates extend the current 0716 schema and ingress security baseline."""

from pathlib import Path

from alembic.script import ScriptDirectory

from deerflow.persistence.bootstrap import _get_alembic_config

ROOT = Path(__file__).resolve().parents[2]


def test_work_has_one_head_after_existing_ppt_and_oa_revisions():
    from sqlalchemy.ext.asyncio import create_async_engine

    config = _get_alembic_config(create_async_engine("sqlite+aiosqlite:///:memory:"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0022_work_integration_state"]
    assert scripts.get_revision("0022_work_integration_state").down_revision == "0021_oa_result_sets"
    assert scripts.get_revision("0021_oa_result_sets").down_revision == "0020_ppt_confirmation"


def test_new_oauth_locations_preserve_production_header_security():
    for relative in ("docker/nginx/nginx.conf", "docker/nginx/nginx.local.conf", "k8s/local-pvc/nginx.conf"):
        config = (ROOT / relative).read_text()
        for path in ("/api/integrations/work/oauth/authorize", "/api/integrations/work/oauth/callback"):
            block = config.split("location = " + path + " {", 1)[1].split("}", 1)[0]
            for directive in (
                'proxy_set_header Forwarded "";',
                'proxy_set_header X-Forwarded-Port "";',
                "proxy_set_header X-Forwarded-Host $host;",
                "proxy_hide_header X-Powered-By;",
                "proxy_hide_header X-AspNet-Version;",
                "proxy_hide_header X-AspNetMvc-Version;",
                "access_log off;",
                "error_log /dev/null;",
            ):
                assert directive in block, (relative, path, directive)
