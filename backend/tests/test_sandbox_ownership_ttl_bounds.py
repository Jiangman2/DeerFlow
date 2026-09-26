"""Regression checks for Redis ownership lease TTL bounds (#5104)."""

from unittest.mock import MagicMock

import pytest

from deerflow.community.aio_sandbox.ownership import compute_lease_ttl
from deerflow.community.aio_sandbox.ownership.redis import RedisOwnershipStore
from deerflow.config.sandbox_config import SandboxOwnershipConfig


@pytest.mark.parametrize(
    ("interval", "multiplier", "error"),
    [
        (1e308, 4.0, "finite"),
        (0.0004, 2.0, "at least 1 millisecond"),
        (2**62 / 4000, 4.0, "headroom"),
    ],
)
def test_reject_unusable_redis_ownership_ttl(interval, multiplier, error):
    with pytest.raises(ValueError, match=error):
        SandboxOwnershipConfig(type="redis", renewal_interval_seconds=interval, ttl_multiplier=multiplier)


def test_redis_lease_millisecond_conversion_rounds_up():
    config = SandboxOwnershipConfig(type="redis", renewal_interval_seconds=0.0009, ttl_multiplier=2.0)
    ttl_seconds = compute_lease_ttl(config)
    store = RedisOwnershipStore(owner_id="worker", redis_url="redis://unused", ttl_seconds=ttl_seconds, client=MagicMock())

    assert store._ttl_ms == 2
    assert store._ttl_ms >= ttl_seconds * 1000
