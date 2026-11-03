from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_nginx_variants_preserve_browser_live_websocket_upgrade() -> None:
    for relative in (
        "docker/nginx/nginx.conf",
        "docker/nginx/nginx.local.conf",
        "k8s/local-pvc/nginx.conf",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        browser_location = "location ~ ^/api/threads/[^/]+/browser/stream"
        generic_location = "location ~ ^/api/threads {"
        assert browser_location in text, relative
        assert text.index(browser_location) < text.index(generic_location), relative
        block = text[text.index(browser_location) : text.index(generic_location)]
        assert "proxy_set_header Upgrade $http_upgrade;" in block, relative
        assert "proxy_set_header Connection 'upgrade';" in block, relative
        assert "proxy_read_timeout 600s;" in block, relative
