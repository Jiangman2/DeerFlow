from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_owned_api_routes_precede_gateway_catch_all() -> None:
    cases = (
        (ROOT / "docker" / "nginx" / "nginx.conf", "$frontend_upstream"),
        (ROOT / "docker" / "nginx" / "nginx.local.conf", "frontend"),
        (ROOT / "k8s" / "local-pvc" / "nginx.conf", "$frontend_upstream"),
    )

    for path, frontend_upstream in cases:
        config = path.read_text(encoding="utf-8")
        shop_cart = "location ^~ /api/shop-cart/ {"
        external_agents = "location ^~ /api/cnooc/external-agents/ {"
        gateway_catch_all = "location /api/ {"

        assert config.count(shop_cart) == 1, path
        assert config.count(external_agents) == 1, path
        assert config.index(shop_cart) < config.index(gateway_catch_all), path
        assert config.index(external_agents) < config.index(gateway_catch_all), path

        shop_cart_block = config[
            config.index(shop_cart) : config.index("}", config.index(shop_cart))
        ]
        external_agents_block = config[
            config.index(external_agents) : config.index(
                "}", config.index(external_agents)
            )
        ]
        assert f"proxy_pass http://{frontend_upstream};" in shop_cart_block, path
        assert (
            f"proxy_pass http://{frontend_upstream};" in external_agents_block
        ), path


def test_weknora_tenant_validation_hides_expected_native_errors() -> None:
    config = (ROOT / "k8s" / "local-pvc" / "nginx.conf").read_text(
        encoding="utf-8"
    )

    validation_route = "location = /api/weknora-native/validate-tenant {"
    invalid_target_handler = "location @weknora_invalid_target_tenant {"
    gateway_catch_all = "location /api/ {"

    assert validation_route in config
    assert invalid_target_handler in config
    assert config.index(validation_route) < config.index(gateway_catch_all)

    validation_block = config[
        config.index(validation_route) : config.index(
            invalid_target_handler, config.index(validation_route)
        )
    ]
    invalid_target_start = config.index(invalid_target_handler)
    invalid_target_block = config[
        invalid_target_start : config.index("\n        }", invalid_target_start)
    ]

    assert "proxy_pass http://weknora-app:8080/api/v1/auth/me;" in validation_block
    assert "proxy_intercept_errors on;" in validation_block
    assert "error_page 400 403 = @weknora_invalid_target_tenant;" in validation_block
    assert "return 200 '{\"valid\":false}';" in invalid_target_block
