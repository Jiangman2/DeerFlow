"""Static contract checks; these do not claim real Nginx HTTP coverage."""

import re
from pathlib import Path

import pytest


def fragment():
    return (Path(__file__).resolve().parents[2] / "k8s/local-pvc/gateway-run-placement-nginx.inc").read_text(encoding="utf-8")


def test_creation_placement_and_internal_owner_lookup_remain_separate():
    text = fragment()
    assert text.count("location = /_deerflow_select_gateway_run {") == 1
    assert text.count("location = /_deerflow_resolve_gateway_owner {") == 1
    internal = text.split("location = /_deerflow_resolve_gateway_owner {", 1)[1].split("}", 1)[0]
    for directive in (
        "internal;",
        "proxy_method GET;",
        "proxy_pass_request_body off;",
        "proxy_pass_request_headers off;",
        'proxy_set_header Content-Length "";',
        "proxy_pass http://gateway-run-dispatcher:8004/resolve;",
    ):
        assert directive in internal
    assert "$args" not in internal
    assert "proxy_set_header Cookie" not in internal
    assert "proxy_set_header Authorization" not in internal


def test_owner_route_snapshots_metadata_and_disables_mutation_retry():
    route = fragment().split("location ~ ^/api/langgraph/threads/(?<", 1)[1]
    assert "cancel|stream" in route
    assert "set $df_owner_method $request_method;" in route
    assert "auth_request /_deerflow_resolve_gateway_owner;" in route
    assert "proxy_next_upstream off;" in route
    assert "rewrite ^/api/langgraph/(.*) /api/$1 break;" in route
    assert "proxy_pass http://$df_owner_gateway_target;" in route
    assert "proxy_pass_request_headers off;" not in route
    assert "proxy_pass_request_body off;" not in route


@pytest.mark.parametrize(
    "query,present",
    [
        ("action=interrupt", True),
        ("action=", True),
        ("action", True),
        ("foo=1&action=&action=interrupt", True),
        ("a%63tion=interrupt", True),
        ("%61%63%74%69%6F%6E=interrupt", True),
        ("actionable=interrupt", False),
        ("foo=action", False),
    ],
)
def test_action_presence_contract(query, present):
    expression = re.search(r'if\s+\(\$args\s+~\s+"([^"]+)"\)', fragment())
    assert expression is not None
    assert bool(re.search(expression.group(1), query)) is present
