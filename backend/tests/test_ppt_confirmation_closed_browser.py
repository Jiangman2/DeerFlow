"""Official UI -> browser closed -> real 60s server countdown -> native receipt.

Opt-in LOCAL qualification only. Uses a temporary SQLite DB and actual vendored
Flask routes, no socket/provider/CLI/production task. It proves the closed-browser
countdown boundary, not Agent continuation or production authentication/recovery.
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_ppt_confirmation_proposal import OfficialFixture

from deerflow.persistence.ppt_confirmation.model import ConfirmationGateRow, ConfirmationTaskRow
from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
from deerflow.ppt_confirmation.controller import ConfirmationController, ObservedProposal
from deerflow.ppt_confirmation.lifecycle import ConsentConflict
from deerflow.ppt_confirmation.proposal import capture_proposal


@unittest.skipUnless(os.environ.get("PPT_CONFIRMATION_REAL_TIME_TEST") == "1", "explicit real-time local fixture required")
class ClosedBrowserTests(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        factory = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
        self._asyncioRunner = asyncio.Runner(debug=False, loop_factory=factory)

    async def test_native_stage1_continues_after_real_deadline_with_browser_closed(self):
        from playwright.async_api import async_playwright

        asyncio.get_running_loop().set_debug(False)
        with tempfile.TemporaryDirectory(prefix="ppt-closed-browser-") as directory:
            with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
                fixture = OfficialFixture(directory)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, executable_path=os.environ.get("PPT_CONFIRMATION_TEST_CHROMIUM"))
                try:
                    snapshot = fixture.snapshot()
                    proposal = await capture_proposal(browser, snapshot)
                finally:
                    await browser.close()
            self.assertFalse(browser.is_connected())
            self.assertFalse((fixture.confirm / "result.json").exists())
            scope = Scope("local-owner", "local-thread", "local-task")
            posts = []

            class NativeOwner:
                async def observe(self, requested):
                    if fixture.snapshot().version != proposal.version:
                        raise ConsentConflict("official_page_changed")
                    return ObservedProposal(scope, "local-project", "stage1", proposal)

                async def submit_guarded(self, requested, claim, captured):
                    # No competing Host process in this fixture. Production
                    # still needs its authenticated per-task cancellation lock.
                    posts.append(time.time())
                    reply = fixture.submit(captured.conditional_payload(claim["submission"]["id"]))
                    if reply.status_code != 200:
                        raise ConsentConflict("native_fixture_rejected")

                async def receipt(self, requested, operation):
                    return fixture.get("/api/confirmation-receipts/" + operation)

                async def receipt_by_version(self, requested, version):
                    return fixture.get("/api/confirmation-receipts/by-version/" + version)

            engine = create_async_engine(f"sqlite+aiosqlite:///{Path(directory) / 'state.db'}")
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(lambda db: ConfirmationTaskRow.metadata.create_all(db, tables=[ConfirmationTaskRow.__table__, ConfirmationGateRow.__table__]))
                repository = ConfirmationRepository(async_sessionmaker(engine, expire_on_commit=False))
                await repository.admit(scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=True)
                controller = ConfirmationController(repository, NativeOwner())
                first = await controller.tick(scope)
                deadline = first["gate"]["due_at"]
                self.assertEqual(deadline - first["gate"]["created_at"], 60)
                hard_deadline = time.monotonic() + 90
                current = first
                while current["gate"]["state"] == "waiting_user" and time.monotonic() < hard_deadline:
                    self.assertFalse(posts)
                    await asyncio.sleep(2)
                    current = await controller.tick(scope)
                self.assertEqual(current["gate"]["state"], "confirmed")
                self.assertEqual(len(posts), 1)
                self.assertGreaterEqual(posts[0], deadline)
                self.assertEqual(fixture.module._wait_result_status(fixture.confirm / "result.json", "stage1"), 0)
                self.assertFalse(browser.is_connected())
            finally:
                await engine.dispose()


if __name__ == "__main__":
    unittest.main()
