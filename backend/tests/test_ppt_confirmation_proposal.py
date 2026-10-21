"""Execute the exact official page against in-memory replies, not production.

No model/provider or live server. Submission in these fixtures uses the real
vendored Flask owner only AFTER capture; the capture itself writes no receipts.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/packages/harness"))
from deerflow.ppt_confirmation import proposal as proposal_module  # noqa: E402
from deerflow.ppt_confirmation.proposal import ProposalError, capture_proposal, load_assets, prepare_snapshot  # noqa: E402

OFFICIAL = ROOT / "vendor/ppt-master-v6.6.0/scripts/confirm_ui"


def official_module():
    spec = importlib.util.spec_from_file_location("ppt_confirmation_official_test", OFFICIAL / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfficialFixture:
    def __init__(self, directory):
        self.project = Path(directory) / "project"
        self.confirm = self.project / "confirm_ui"
        self.confirm.mkdir(parents=True)
        self.stage1 = {
            "stage": "stage1",
            "lang": "zh",
            "primary_language": "zh-CN",
            "template_options": {"schema_version": 1, "phase": "template", "lang": "zh", "default_mode": "free_design", "explicit_workspace_roots": []},
            "recommend": {"canvas": "ppt169"},
            "audience": {"value": "本地验收用户"},
            "communication_intent": {"value": "验证官方确认往返"},
            "content_divergence": {"value": "", "locked": True},
        }
        self.write("recommendations.stage1.json", self.stage1)
        self.module = official_module()
        self.app = self.module.create_app(str(self.project), idle_timeout=0, server_port=5050)
        self.client = self.app.test_client()

    def write(self, name, data):
        (self.confirm / name).write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    def get(self, path):
        reply = self.client.get(path, base_url="http://127.0.0.1:5050")
        if reply.status_code != 200:
            raise AssertionError((path, reply.status_code, reply.json))
        return reply.json

    def snapshot_args(self):
        health = self.get("/api/health")
        return dict(before=health, recommendations=self.get("/api/recommendations"), catalogs=self.get("/api/catalogs"), after=self.get("/api/health"), expected_project=str(self.project.resolve()), source_root=OFFICIAL)

    def snapshot(self, **overrides):
        args = self.snapshot_args()
        args.update(overrides)
        return prepare_snapshot(**args)

    def submit(self, payload):
        return self.client.post("/api/confirm", json=payload, base_url="http://127.0.0.1:5050")

    def stage2(self):
        selection = json.loads((self.confirm / "template_selection.json").read_text("utf-8"))
        palette = dict(background="#FFFFFF", secondary_bg="#F4F6F8", primary="#1A3A6B", accent="#E8A317", secondary_accent="#4A7BB5", body_text="#1D2430")
        candidate = {
            "id": "local",
            "name_zh": "本地验收",
            "note_zh": "用于隔离测试",
            "mode": "custom",
            "mode_behavior_zh": "每章先给判断再解释证据。",
            "visual_style": "custom",
            "visual_style_behavior_zh": "统一栅格和留白。",
            "icons": "tabler-outline",
            "color": {"name_zh": "本地配色", "palette": palette},
            "typography": {"name_zh": "本地字体", "heading": {"primary": "Microsoft YaHei", "english": "Arial", "css": "sans-serif"}, "body": {"primary": "Microsoft YaHei", "english": "Arial", "css": "sans-serif"}, "body_size": 24},
            "image_strategy": {"name_zh": "本地插图", "rendering": "custom", "visual_zh": "扁平", "mood_zh": "审慎", "behavior_zh": "仅用于默认选择序列化测试，不执行生图。"},
        }
        candidates = []
        for i in range(3):
            row = copy.deepcopy(candidate)
            row.update(id=f"local-{i}", name_zh=f"隔离方案 {i}", mode_behavior_zh=f"使用第 {i} 个完整方案。")
            candidates.append(row)
        data = {
            "stage": "stage2",
            "selection_sha256": selection["selection_sha256"],
            "lang": "zh",
            "recommend": {"delivery_purpose": "balanced", "mode": "custom", "visual_style": "custom", "image_strategy": "custom", "image_usage": ["none"], "generation_mode": "continuous"},
            "page_count": {"value": "5"},
            "refine_spec": {"value": False},
            "design_spec_depth": {"value": "brief"},
            "proactive_speaker_notes": {"value": True},
            "proactive_custom_animations": {"value": False},
            "proactive_narration_audio": {"value": False},
            "design_directions": {"selected": 1, "candidates": candidates},
        }
        self.write("recommendations.stage2.json", data)
        return data


class ProposalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-official-proposal-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = OfficialFixture(self.temp.name)

    def test_source_assets_are_exact_upstream_not_a_copy_of_its_schema(self):
        assets, digest = load_assets(OFFICIAL)
        self.assertEqual(set(assets), {"/", "/static/app.js", "/static/style.css"})
        self.assertEqual(len(digest), 64)

    def test_health_project_must_match_server_resolved_project(self):
        with self.assertRaisesRegex(ProposalError, "owner_project_mismatch"):
            self.fixture.snapshot(expected_project="/different/project")

    def test_changing_recommendation_during_read_is_not_a_proposal(self):
        after = self.fixture.get("/api/health")
        after["session"]["recommendation_version"] = "f" * 64
        with self.assertRaisesRegex(ProposalError, "owner_changed_during_snapshot"):
            self.fixture.snapshot(after=after)

    def test_waiting_agent_is_not_confirmation_ready(self):
        before = self.fixture.get("/api/health")
        before["session"]["status"] = "waiting_agent"
        with self.assertRaisesRegex(ProposalError, "official_owner_not_ready"):
            self.fixture.snapshot(before=before, after=before)

    def test_snapshot_freezes_actual_recommendations_and_catalogs(self):
        rec = self.fixture.get("/api/recommendations")
        snapshot = self.fixture.snapshot(recommendations=rec)
        rec["audience"]["value"] = "should not mutate"
        self.assertNotIn("should not mutate", repr(snapshot))
        self.assertNotIn("should not mutate", snapshot.recommendations_json.decode())

    def test_tampered_official_assets_fail_closed(self):
        path = Path(self.temp.name) / "unknown-owner"
        path.mkdir()
        (path / "server.py").write_text("raise Exception('should not execute')", "utf-8")
        with self.assertRaises(ProposalError):
            load_assets(path)

    def test_malformed_or_boolean_recommendation_stage_is_a_safe_refusal(self):
        base = self.fixture.snapshot_args()
        for value in ([], {}, None, True, False, 1.0, "final"):
            with self.subTest(stage=value):
                args = copy.deepcopy(base)
                args["recommendations"]["stage"] = value
                with self.assertRaisesRegex(ProposalError, "official_stage_mismatch"):
                    prepare_snapshot(**args)

    def test_missing_or_invalid_process_identity_is_not_a_stable_owner(self):
        base = self.fixture.snapshot_args()
        for value in (None, 0, -1, True, "123"):
            with self.subTest(pid=value):
                args = copy.deepcopy(base)
                args["before"]["pid"] = value
                args["after"]["pid"] = value
                with self.assertRaisesRegex(ProposalError, "invalid_owner_process"):
                    prepare_snapshot(**args)

    def test_owner_restart_invalidates_the_snapshot(self):
        before = self.fixture.get("/api/health")
        after = copy.deepcopy(before)
        after["pid"] += 1
        with self.assertRaisesRegex(ProposalError, "owner_changed_during_snapshot"):
            self.fixture.snapshot(before=before, after=after)


class CaptureResourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.snapshot = proposal_module.PageSnapshot(1, "a" * 64, "b" * 64, b"{}", {}, {})

    async def test_python310_capture_reaches_browser_and_closes_context(self):
        calls = []

        class Context:
            async def route(self, *args):
                calls.append("route")
                raise RuntimeError("private browser diagnostic")

            async def close(self):
                calls.append("close")

        class Browser:
            async def new_context(self, **kwargs):
                calls.append("create")
                return Context()

        # The deployed Host's Python 3.10 has wait_for, not asyncio.timeout.
        with patch.dict(asyncio.__dict__):
            asyncio.__dict__.pop("timeout", None)
            with self.assertRaisesRegex(ProposalError, "official_page_capture_failed"):
                await capture_proposal(Browser(), self.snapshot)
        self.assertEqual(calls, ["create", "route", "close"])

    async def test_external_cancellation_closes_context_and_remains_cancelled(self):
        entered = asyncio.Event()
        closed = asyncio.Event()

        class Context:
            async def route(self, *args):
                entered.set()
                await asyncio.Future()

            async def close(self):
                closed.set()

        class Browser:
            async def new_context(self, **kwargs):
                return Context()

        pending = asyncio.create_task(capture_proposal(Browser(), self.snapshot))
        await asyncio.wait_for(entered.wait(), timeout=1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(closed.is_set())

    async def test_browser_context_creation_is_inside_the_deadline(self):
        class StalledBrowser:
            async def new_context(self, **kwargs):
                await asyncio.Future()

        with patch.object(proposal_module, "CAPTURE_DEADLINE_SECONDS", 0.01, create=True):
            with self.assertRaisesRegex(ProposalError, "official_page_capture_failed"):
                await asyncio.wait_for(capture_proposal(StalledBrowser(), self.snapshot), timeout=1)

    async def test_failed_cleanup_is_bounded_and_redacted(self):
        class Context:
            async def route(self, *args):
                raise RuntimeError("private fake browser diagnostic")

            async def close(self):
                await asyncio.Future()

        class Browser:
            async def new_context(self, **kwargs):
                return Context()

        with patch.object(proposal_module, "CLOSE_DEADLINE_SECONDS", 0.01, create=True):
            with self.assertRaisesRegex(ProposalError, "official_page_cleanup_failed"):
                await asyncio.wait_for(capture_proposal(Browser(), self.snapshot), timeout=1)


class OfficialBrowserProposalTests(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        # The SQL driver selects Selector on Windows at import time. Browser
        # subprocesses need Proactor; scope this to this test's own loop only.
        factory = asyncio.ProactorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
        self._asyncioRunner = asyncio.Runner(debug=False, loop_factory=factory)

    async def asyncSetUp(self):
        from playwright.async_api import async_playwright

        # unittest enables asyncio debug by default. Playwright's many transport
        # callbacks otherwise flood the receipt with unrelated slow-task traces.
        asyncio.get_running_loop().set_debug(False)
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-official-browser-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = OfficialFixture(self.temp.name)
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        # Optional existing LOCAL TEST binary only; production uses image's own
        # Playwright browser. No download, default browser or live service touched.
        binary = os.environ.get("PPT_CONFIRMATION_TEST_CHROMIUM")
        self.browser = await self.playwright.chromium.launch(headless=True, executable_path=binary)
        self.addAsyncCleanup(self.browser.close)

    async def test_conditional_stage1_stage2_real_owner_rejects_stale_final_receipt(self):
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            self.fixture.app = self.fixture.module.create_app(str(self.fixture.project), idle_timeout=0, server_port=5050)
        self.fixture.client = self.fixture.app.test_client()
        first = await capture_proposal(self.browser, self.fixture.snapshot())
        self.assertIsNotNone(first.owner_version)
        self.assertNotIn("_confirmation_request", first.payload)
        self.assertEqual(self.fixture.submit(first.conditional_payload("auto-stage1")).status_code, 200)
        self.fixture.stage2()
        final = await capture_proposal(self.browser, self.fixture.snapshot())
        payload = final.conditional_payload("auto-final")
        reply = self.fixture.submit(payload)
        self.assertEqual(reply.status_code, 200, reply.json)
        self.assertEqual(self.fixture.submit(payload).status_code, 200)
        path = self.fixture.confirm / "result.json"
        self.assertEqual(self.fixture.module._wait_result_status(path, "final"), 0)
        changed = json.loads((self.fixture.confirm / "recommendations.stage2.json").read_bytes())
        changed["page_count"]["value"] = "7"
        self.fixture.write("recommendations.stage2.json", changed)
        self.assertIsNone(self.fixture.module._wait_result_status(path, "final"))
        self.assertNotEqual(self.fixture.submit(payload).status_code, 200)
        health = self.fixture.get("/api/health")
        self.assertEqual(health["session"]["status"], "error")

    async def test_conditional_browser_captures_same_choices_with_stable_digest(self):
        with patch.dict(os.environ, {"PPT_MASTER_CONFIRMATION_PROTOCOL": "1"}):
            self.fixture.app = self.fixture.module.create_app(str(self.fixture.project), idle_timeout=0, server_port=5050)
        self.fixture.client = self.fixture.app.test_client()
        snapshot = self.fixture.snapshot()
        first = await capture_proposal(self.browser, snapshot)
        second = await capture_proposal(self.browser, snapshot)
        self.assertEqual(first.payload_digest, second.payload_digest)
        self.assertEqual(first.owner_version, second.owner_version)
        self.assertFalse((self.fixture.confirm / "result.json").exists())

    async def test_stage1_official_page_payload_and_real_owner_receipt(self):
        snapshot = self.fixture.snapshot()
        screenshot = os.environ.get("PPT_CONFIRMATION_TEST_SCREENSHOT")
        result = await capture_proposal(self.browser, snapshot, screenshot_path=Path(screenshot) if screenshot else None)
        self.assertFalse((self.fixture.confirm / "result.json").exists())
        self.assertFalse((self.fixture.confirm / "template_selection.json").exists())
        self.assertEqual(result.payload["stage"], "stage1")
        self.assertEqual(result.payload["template_selection"], {"mode": "free_design", "selection_keys": []})
        self.assertEqual(result.payload["audience"], "本地验收用户")
        self.assertFalse(result.confirmation_submitted)
        self.assertEqual(result.console_errors, 0)
        self.assertEqual(result.console_warnings, 0)
        self.assertEqual(result.page_title, "确认设计方案")
        self.assertEqual(self.fixture.submit(result.payload).status_code, 200)
        receipt = json.loads((self.fixture.confirm / "result.json").read_text("utf-8"))
        self.assertEqual(receipt["status"], "stage1-confirmed")
        self.assertEqual(receipt["stage1_sha256"], self.fixture.module._stage1_sha256(self.fixture.confirm))
        self.assertIn("selection_sha256", receipt)

    async def test_stage2_selected_direction_and_real_final_receipt(self):
        first = await capture_proposal(self.browser, self.fixture.snapshot())
        self.assertEqual(self.fixture.submit(first.payload).status_code, 200)
        self.fixture.stage2()
        before = (self.fixture.confirm / "result.json").read_bytes()
        screenshot = os.environ.get("PPT_CONFIRMATION_TEST_SCREENSHOT")
        stage2_screenshot = Path(screenshot).with_name("ppt-confirmation-stage2-local.png") if screenshot else None
        proposal = await capture_proposal(self.browser, self.fixture.snapshot(), screenshot_path=stage2_screenshot)
        self.assertEqual(proposal.console_errors, 0)
        self.assertEqual(proposal.console_warnings, 0)
        self.assertEqual((self.fixture.confirm / "result.json").read_bytes(), before)
        self.assertEqual(proposal.payload["stage"], "final")
        self.assertEqual(proposal.payload["mode_behavior"], "使用第 1 个完整方案。")
        self.assertEqual(proposal.payload["image_usage"], ["none"])
        self.assertEqual(proposal.payload["design_spec_depth"], "brief")
        self.assertTrue(proposal.payload["proactive_speaker_notes"])
        self.assertFalse(proposal.payload["proactive_custom_animations"])
        self.assertFalse(proposal.payload["proactive_narration_audio"])
        self.assertEqual(self.fixture.submit(proposal.payload).status_code, 200)
        receipt = json.loads((self.fixture.confirm / "result.json").read_text("utf-8"))
        self.assertEqual(receipt["status"], "confirmed")
        self.assertEqual(self.fixture.get("/api/health")["session"]["status"], "done")
        # A new OFFICIAL application instance reads the same persisted receipt.
        # This is not an Agent Host/Pod recovery or continuation test.
        restored_app = self.fixture.module.create_app(str(self.fixture.project), idle_timeout=0, server_port=5050)
        restored = restored_app.test_client().get("/api/health", base_url="http://127.0.0.1:5050")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json["session"]["status"], "done")

    async def test_recommendations_are_not_the_submission_payload(self):
        self.assertNotEqual(self.fixture.submit(self.fixture.stage1).status_code, 200)
        screenshot = os.environ.get("PPT_CONFIRMATION_TEST_SCREENSHOT")
        mobile_screenshot = Path(screenshot).with_name("ppt-confirmation-stage1-mobile-local.png") if screenshot else None
        proposal = await capture_proposal(self.browser, self.fixture.snapshot(), viewport={"width": 390, "height": 844}, screenshot_path=mobile_screenshot)
        self.assertEqual(proposal.console_errors, 0)
        self.assertEqual(proposal.console_warnings, 0)
        self.assertEqual(self.fixture.submit(proposal.payload).status_code, 200)


if __name__ == "__main__":
    unittest.main()
