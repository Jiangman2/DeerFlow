"""History offloading preserves authorization, owner context, and HTTP errors."""

import contextvars
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.gateway.routers import skills as skills_router


class SkillHistoryOffloadBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_runs_off_loop_with_owner_context_after_auth(self):
        loop_thread = threading.get_ident()
        owner = contextvars.ContextVar("history_test_owner", default=None)
        token = owner.set("trusted-owner")
        observed = []

        def record():
            self.assertNotEqual(threading.get_ident(), loop_thread)
            self.assertEqual(owner.get(), "trusted-owner")
            self.assertEqual(auth.await_count, 1)
            observed.append("storage")

        def exists(_name):
            record()
            return True

        def read(_name):
            record()
            return []

        def storage(_config):
            record()
            return SimpleNamespace(custom_skill_exists=exists, read_history=read)

        auth = AsyncMock()
        try:
            with patch.object(skills_router, "require_admin_user", auth), patch.object(skills_router, "_get_user_skill_storage", storage):
                response = await skills_router.get_custom_skill_history("demo", object(), object())
            self.assertEqual(response.history, [])
            self.assertEqual(len(observed), 3)
        finally:
            owner.reset(token)

    async def test_denied_request_never_constructs_storage(self):
        with patch.object(skills_router, "require_admin_user", AsyncMock(side_effect=HTTPException(403, "denied"))), patch.object(skills_router, "_get_user_skill_storage") as storage:
            with self.assertRaises(HTTPException) as caught:
                await skills_router.get_custom_skill_history("demo", object(), object())
            self.assertEqual(caught.exception.status_code, 403)
            storage.assert_not_called()

    async def test_missing_history_preserves_404(self):
        storage = SimpleNamespace(custom_skill_exists=lambda _name: False, get_skill_history_file=lambda _name: SimpleNamespace(exists=lambda: False))
        with patch.object(skills_router, "require_admin_user", AsyncMock()), patch.object(skills_router, "_get_user_skill_storage", return_value=storage):
            with self.assertRaises(HTTPException) as caught:
                await skills_router.get_custom_skill_history("demo", object(), object())
            self.assertEqual(caught.exception.status_code, 404)

    async def test_worker_error_preserves_existing_500_contract(self):
        with patch.object(skills_router, "require_admin_user", AsyncMock()), patch.object(skills_router, "_get_user_skill_storage", side_effect=OSError("unavailable")):
            with self.assertRaises(HTTPException) as caught:
                await skills_router.get_custom_skill_history("demo", object(), object())
            self.assertEqual(caught.exception.status_code, 500)
            self.assertEqual(caught.exception.detail, "Failed to read history: unavailable")


if __name__ == "__main__":
    unittest.main()
