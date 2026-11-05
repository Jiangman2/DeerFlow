"""Installed source/config and loopback health checks; no membership operation."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys


def inspect(pins):
    root = Path('/app/backend')
    for relative, expected in pins.items():
        file = root / relative
        assert file.is_file() and not file.is_symlink()
        assert hashlib.sha256(file.read_bytes()).hexdigest() == expected
    sys.path.insert(0, str(root))
    from app.gateway import weknora_company_knowledge as company
    from app.gateway import weknora_identity as identity
    assert identity.ensure_company_knowledge_access is company.ensure_company_knowledge_access
    config = company.CompanyKnowledge.from_environment()
    assert config is not None and config.organization_ids == ('b9cfbf7f-7281-4f62-a72b-6a22379efe60',)
    import httpx
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        with client.stream('GET', 'http://127.0.0.1:8001/health') as response:
            assert response.status_code == 200
            body = bytearray()
            for chunk in response.iter_bytes(4096):
                body.extend(chunk)
                assert len(body) <= 16384
            assert isinstance(json.loads(body), dict)
    return {'status': 'PASS', 'sources_verified': True, 'auto_enrollment_enabled': True,
            'organization_ids': list(config.organization_ids), 'local_health_http_status': 200,
            'provider_requests': 0, 'membership_grants': 0, 'credentials_exported': False,
            'database_access_by_check': False, 'application_started_by_check': False}


if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = inspect(json.loads(sys.argv[1]))
    except Exception as exc:
        result = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_errors_exported': False}
    print(json.dumps(result, sort_keys=True))
