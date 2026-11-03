from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "k8s" / "local-pvc" / "nginx.conf"
SUBMIT_ROUTE = "location = /api/v1/integrations/oa/policy-flow/jobs"
CHILD_ROUTE = "location ^~ /api/v1/integrations/oa/policy-flow/jobs/"
WEKNORA_CATCH_ALL = "location ^~ /api/v1/"


def _block(text: str, marker: str) -> str:
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated Nginx block: {marker}")


def test_oa_policy_flow_routes_reach_gateway_before_weknora() -> None:
    config = CONFIG_PATH.read_text(encoding="utf-8")

    assert config.count(SUBMIT_ROUTE + " {") == 1
    assert config.count(CHILD_ROUTE + " {") == 1
    catch_all_position = config.index(WEKNORA_CATCH_ALL + " {")
    assert config.index(SUBMIT_ROUTE + " {") < catch_all_position
    assert config.index(CHILD_ROUTE + " {") < catch_all_position

    for marker in (SUBMIT_ROUTE, CHILD_ROUTE):
        block = _block(config, marker)
        assert "proxy_pass http://$gateway_upstream;" in block
        assert "proxy_set_header Authorization $http_authorization;" in block
        assert "proxy_http_version 1.1;" in block
        assert "weknora-app" not in block


def test_oa_policy_flow_submission_route_has_bounded_upload_controls() -> None:
    config = CONFIG_PATH.read_text(encoding="utf-8")
    block = _block(config, SUBMIT_ROUTE)

    assert "client_max_body_size 11m;" in block
    assert "client_body_timeout 60s;" in block
    assert "proxy_connect_timeout 30s;" in block
    assert "proxy_send_timeout 60s;" in block
    assert "proxy_read_timeout 60s;" in block
