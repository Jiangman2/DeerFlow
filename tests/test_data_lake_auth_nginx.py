from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_CONFIGS = (
    ROOT / "docker" / "nginx" / "nginx.conf",
    ROOT / "docker" / "nginx" / "nginx.local.conf",
    ROOT / "k8s" / "local-pvc" / "nginx.conf",
)


def test_data_lake_callback_is_rate_limited_before_gateway_catch_all() -> None:
    exact_route = "location = /api/v1/data-lake/auth/callback {"
    rate_limited_handler = "location @data_lake_callback_rate_limited {"
    gateway_catch_all = "location /api/ {"

    for path in NGINX_CONFIGS:
        config = path.read_text(encoding="utf-8")

        assert config.count(
            "limit_req_zone $binary_remote_addr "
            "zone=data_lake_callback:10m rate=5r/s;"
        ) == 1, path
        assert config.count(exact_route) == 1, path
        assert config.count(rate_limited_handler) == 1, path
        assert config.index(exact_route) < config.index(gateway_catch_all), path

        route_start = config.index(exact_route)
        handler_start = config.index(rate_limited_handler)
        route_block = config[route_start:handler_start]
        handler_block = config[
            handler_start : config.index("\n        }", handler_start)
        ]

        assert "limit_req zone=data_lake_callback burst=10 nodelay;" in route_block
        assert "limit_req_status 429;" in route_block
        assert "error_page 429 = @data_lake_callback_rate_limited;" in route_block
        assert "default_type application/json;" in handler_block
        assert 'add_header Cache-Control "no-store" always;' in handler_block
        assert 'add_header Referrer-Policy "no-referrer" always;' in handler_block
        assert "add_header X-Request-Id $request_id always;" in handler_block
        assert '"code":"callback_rate_limited"' in handler_block


def test_kubernetes_tls_loopback_restores_client_ip_for_rate_limiting() -> None:
    config = (ROOT / "k8s" / "local-pvc" / "nginx.conf").read_text(
        encoding="utf-8"
    )

    assert "set_real_ip_from 127.0.0.1;" in config
    assert "set_real_ip_from ::1;" in config
    assert "real_ip_header X-Real-IP;" in config
    assert "real_ip_recursive on;" in config
