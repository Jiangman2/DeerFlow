"""Offline operator-evidence boundaries; never an activation or cluster read."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy/enterprise'))
import scheduler_budget_runtime_acceptance_20261113 as acceptance


def read(path):
    return (ROOT / path).read_bytes()


def test_operator_readonly_receipt_accepts_only_runtime_scope():
    result = acceptance.verify(read)
    assert result['scheduler_budget_deployed'] and result['gateway_generation'] == 416
    assert result['verified_pods'] == 70 and result['verified_source_files_per_pod'] == 13
    assert result['new_run_business_acceptance_pending']
    assert not result['loaded_application_modules_verified'] and not result['production_access']
    assert result['evidence_basis'] == 'operator_pasted_readonly_stdout_json'


@pytest.mark.parametrize('path', [acceptance.RECEIPT, acceptance.OPERATOR, acceptance.PROVENANCE])
def test_changed_or_missing_evidence_is_rejected(path):
    with pytest.raises(ValueError):
        acceptance.verify(lambda name: read(name) + b'changed' if name == path else read(name))
    def missing(name):
        if name == path:
            raise KeyError(path)
        return read(name)
    with pytest.raises(KeyError):
        acceptance.verify(missing)


@pytest.mark.parametrize('field,value', [
    ('future_scheduled_runs_budget', 300), ('verified_pods', 69),
    ('service_backends_verified', False), ('provider_requests', 1),
    ('business_acceptance_pending', False), ('loaded_application_modules_verified', True),
    ('existing_runs_replayed', True), ('plan_sha256', '0' * 64),
])
def test_rehashed_scope_overclaims_still_fail(monkeypatch, field, value):
    data = json.loads(read(acceptance.RECEIPT))
    data[field] = value
    raw = json.dumps(data).encode()
    monkeypatch.setattr(acceptance, 'RECEIPT_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        acceptance.verify(lambda name: raw if name == acceptance.RECEIPT else read(name))
