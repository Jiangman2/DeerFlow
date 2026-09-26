import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "patch_nginx_timeouts",
    ROOT / "docker" / "sandbox-ppt-master" / "patch_nginx_timeouts.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _config(port: str) -> str:
    return f"""events {{}}
http {{
    server {{
        location /api/ {{
            proxy_set_header Host $host;
            proxy_pass http://127.0.0.1:{port};
        }}
        location /ws/ {{
            proxy_http_version 1.1;
            proxy_pass http://127.0.0.1:{port};
        }}
    }}
}}
"""


@pytest.mark.parametrize("port", ["${AUTH_BACKEND_PORT}", "8081"])
def test_patch_accepts_variable_and_concrete_backend_ports(port: str):
    patched, mode = MODULE.patch_text(_config(port))

    assert mode == "patched"
    assert patched.count("proxy_connect_timeout 5s;") == 2
    assert patched.count("proxy_read_timeout 86400s;") == 2
    assert patched.count("proxy_send_timeout 86400s;") == 2

    second, second_mode = MODULE.patch_text(patched)
    assert second == patched
    assert second_mode == "already-compatible"


def test_patch_normalizes_existing_timeout_values_without_duplicates():
    source = _config("8081").replace(
        "            proxy_pass http://127.0.0.1:8081;",
        "            proxy_read_timeout 60s;\n            proxy_pass http://127.0.0.1:8081;",
    )
    patched, mode = MODULE.patch_text(source)

    assert mode == "patched"
    assert "proxy_read_timeout 60s;" not in patched
    assert patched.count("proxy_read_timeout 86400s;") == 2


def test_patch_fails_closed_when_route_count_changes():
    source = _config("8081").replace("            proxy_pass http://127.0.0.1:8081;\n", "", 1)
    with pytest.raises(ValueError, match="expected_two_loopback_proxy_routes"):
        MODULE.patch_text(source)
