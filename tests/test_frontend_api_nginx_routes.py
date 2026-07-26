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
