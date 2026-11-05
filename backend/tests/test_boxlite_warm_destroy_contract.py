"""BoxLite's destruction result must agree with shared warm-pool retry semantics.

These tests use no real SDK, daemon threads, cluster, or live resources.
Construction deliberately bypasses provider startup while exercising its real
callback and inherited lifecycle methods with controlled close receipts.
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from deerflow.community.boxlite.provider import BoxliteProvider


class _Entry:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.closed = False
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        if self.fail:
            raise RuntimeError("controlled close failure")
        self.closed = True


class TestBoxliteWarmDestroyContract(unittest.TestCase):
    def setUp(self):
        self.provider = object.__new__(BoxliteProvider)
        self.provider._lock = threading.Lock()
        self.provider._warm_pool = {}
        self.provider._boxes = {}
        self.provider._skip_health_check_warm_ids = set()
        self.provider._config = {"replicas": 2, "idle_timeout": 10}

    def test_close_success_returns_true_and_clears_health_skip(self):
        entry = _Entry()
        self.provider._skip_health_check_warm_ids.add("warm")
        self.assertIs(self.provider._destroy_warm_entry("warm", entry, reason="idle_timeout"), True)
        self.assertTrue(entry.closed)
        self.assertEqual(entry.close_calls, 1)
        self.assertNotIn("warm", self.provider._skip_health_check_warm_ids)

    def test_close_exception_returns_false(self):
        entry = _Entry(fail=True)
        self.assertIs(self.provider._destroy_warm_entry("warm", entry, reason="replica_enforcement"), False)
        self.assertFalse(entry.closed)
        self.assertEqual(entry.close_calls, 1)

    def test_idle_cleanup_removes_closed_but_not_fresh_entries(self):
        expired, fresh = _Entry(), _Entry()
        self.provider._warm_pool = {"expired": (expired, 0.0), "fresh": (fresh, 99.0)}
        with patch("deerflow.community.warm_pool_lifecycle.time.time", return_value=100.0):
            self.provider._reap_expired_warm()
        self.assertEqual(self.provider._warm_pool, {"fresh": (fresh, 99.0)})
        self.assertTrue(expired.closed)
        self.assertFalse(fresh.closed)

    def test_idle_failure_retains_tracking_and_can_retry(self):
        entry = _Entry(fail=True)
        self.provider._warm_pool = {"warm": (entry, 0.0)}
        with patch("deerflow.community.warm_pool_lifecycle.time.time", return_value=100.0):
            self.provider._reap_expired_warm()
        self.assertEqual(self.provider._warm_pool, {"warm": (entry, 100.0)})
        self.assertFalse(entry.closed)
        entry.fail = False
        with patch("deerflow.community.warm_pool_lifecycle.time.time", return_value=200.0):
            self.provider._reap_expired_warm()
        self.assertEqual(self.provider._warm_pool, {})
        self.assertTrue(entry.closed)
        self.assertEqual(entry.close_calls, 2)

    def test_eviction_removes_only_oldest_closed_entry(self):
        oldest, newest = _Entry(), _Entry()
        self.provider._warm_pool = {"old": (oldest, 0.0), "new": (newest, 10.0)}
        self.assertEqual(self.provider._evict_oldest_warm(), "old")
        self.assertEqual(self.provider._warm_pool, {"new": (newest, 10.0)})
        self.assertTrue(oldest.closed)
        self.assertFalse(newest.closed)

    def test_eviction_preserves_failed_entry_and_tries_next_candidate(self):
        failed, available = _Entry(fail=True), _Entry()
        self.provider._warm_pool = {"failed": (failed, 0.0), "available": (available, 10.0)}
        with patch("deerflow.community.warm_pool_lifecycle.time.time", return_value=100.0):
            self.assertEqual(self.provider._evict_oldest_warm(), "available")
        self.assertEqual(self.provider._warm_pool, {"failed": (failed, 100.0)})
        self.assertFalse(failed.closed)
        self.assertTrue(available.closed)
        self.assertEqual(failed.close_calls, 1)

    def test_all_failed_eviction_is_bounded_and_keeps_active_resources(self):
        first, second, active = _Entry(fail=True), _Entry(fail=True), _Entry()
        self.provider._warm_pool = {"first": (first, 0.0), "second": (second, 10.0)}
        self.provider._boxes = {"active": active}
        self.assertEqual(self.provider._evict_warm_until_capacity(reserve=1), [])
        self.assertEqual(set(self.provider._warm_pool), {"first", "second"})
        self.assertEqual((first.close_calls, second.close_calls), (1, 1))
        self.assertEqual(self.provider._boxes, {"active": active})
        self.assertEqual(active.close_calls, 0)

    def test_capacity_counts_active_plus_warm_and_reserves_next_slot(self):
        warm, active = _Entry(), _Entry()
        self.provider._warm_pool = {"warm": (warm, 0.0)}
        self.provider._boxes = {"active": active}
        self.assertEqual(self.provider._evict_warm_until_capacity(reserve=1), ["warm"])
        self.assertEqual(self.provider._warm_pool, {})
        self.assertTrue(warm.closed)
        self.assertEqual(active.close_calls, 0)
        self.assertEqual(self.provider._replica_count(), (2, 1))
