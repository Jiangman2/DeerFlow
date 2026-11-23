from pathlib import Path
import re

def test_machine_sse_route_preserves_headers_and_exact_upstream():
    source = (Path(__file__).resolve().parents[1] / "k8s/local-pvc/nginx.conf").read_text()
    route = re.search(r"location = /api/v1/integrations/procurement-review \{([^}]+)\}", source).group(1)
    for directive in ('proxy_pass http://$gateway_upstream;', 'proxy_set_header Authorization $http_authorization;', 'proxy_buffering off;', 'proxy_read_timeout 3900s;', 'client_max_body_size 101m;'):
        assert directive in route
    assert 'auth_request ' not in route
    assert 'weknora' not in route
