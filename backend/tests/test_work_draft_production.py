"""Protocol upgrades preserve grants but cannot replay older calendar jobs."""

import pytest
from test_work_production import principal_message, production_service, stores  # noqa: F401

from app.integrations.work.client import WorkError


@pytest.mark.asyncio
async def test_unversioned_pending_job_is_rejected_without_oauth_exchange_or_losing_grant(stores):  # noqa: F811 - imported pytest fixture
    first, second = production_service(stores[0]), production_service(stores[1])
    try:
        await first.handle(principal_message())
        ticket = next(iter(first.jobs))
        principal = first._identity(principal_message())
        owner = first.vault.principal_key(principal)
        kept = await stores[0].put("grant", owner, {"token": "synthetic-kept-grant", "scopes": ["docx:document"]}, owner=owner, ttl=600, status="active")
        old = await stores[0].read("job", ticket, owner=owner)
        payload = dict(old.value)
        payload.pop("protocol")
        assert await stores[0].change("job", ticket, old, value=payload)
        with pytest.raises(WorkError, match="job_protocol_upgrade_required"):
            await second.authorize(ticket, "")
        second._api.assert_not_awaited()
        grant = await stores[0].read("grant", owner, owner=owner)
        assert grant.value == kept.value and grant.revision == kept.revision
    finally:
        await first.close()
        await second.close()
