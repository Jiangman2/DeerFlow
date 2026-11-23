import importlib.util
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.gateway.procurement_review.files import digest
from app.gateway.procurement_review.markdown_report import validate_markdown_report
from app.gateway.procurement_review.rules import RISK_BASELINES, RULE_VERSION, RULES, build_prompt
from app.gateway.procurement_review.service import LEGACY_RULE_VERSION, ReviewService, report_format
from deerflow.config.paths import Paths

ROOT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("receiver", ROOT / "fixtures/procurement_callback.py")
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


HEADERS = ["序号", "检查项", "判定状态", "风险等级", "命中内容及材料位置", "基准依据", "修正建议", "提示"]


def report(statuses=None, conclusion="无法判断"):
    statuses = statuses or ["符合", "无法判断", "符合", "符合", "无法判断", "符合", "不符合", "符合"]
    lines = ["# 审查报告", "## 基本信息", "材料.docx", "## 核心审查结果", "| " + " | ".join(HEADERS) + " |", "| " + " | ".join(["---"] * 8) + " |"]
    for i, status in enumerate(statuses, 1):
        key = str(i)
        risk = RISK_BASELINES[key] if status == "不符合" else {"符合": "未命中", "无法判断": "待核实", "不适用": "不适用"}[status]
        lines.append("| " + " | ".join([key, RULES[key][0], status, "基准：" + RISK_BASELINES[key] + "；本次：" + risk, "材料第1节，原文与问题", "依据", "建议", "提示"]) + " |")
    lines += ["", "## 补充审查意见与材料覆盖", "材料.docx已完整读取，缺失依据如实列明。", "## 审查结论", "**合规评价：** " + conclusion + "。"]
    return "\n".join(lines).encode()


class MarkdownTests(unittest.TestCase):
    def test_deliver_content_without_semantic_grading(self):
        for raw in (report(), report(conclusion="通过"), b"```unclosed", b"| partial | table |", "服务/供货内容明确性：不符合".encode()):
            self.assertEqual(validate_markdown_report(raw, ["材料.docx"]), {"scope": "MARKDOWN_TRANSPORT_ONLY", "content_validation": False})

    def test_reject_untransportable_text(self):
        for raw in (b"", b"\xff", b"\x00", b" \n", b"\xef\xbb\xbf", b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                validate_markdown_report(raw, [])
        self.assertFalse(validate_markdown_report(b"x" * (2 * 1024 * 1024), [])["content_validation"])

    def test_version_and_prompt(self):
        self.assertEqual(report_format({"rule_version": RULE_VERSION})[:2], (".md", "text/markdown; charset=utf-8"))
        self.assertEqual(report_format({"rule_version": LEGACY_RULE_VERSION})[0], ".docx")
        with self.assertRaises(ValueError):
            report_format({"rule_version": "unknown"})
        prompt = build_prompt({"request_id": "test"}, [])
        self.assertIn("技术委托要求审查报告.md", prompt)
        self.assertIn("不要生成DOCX、PDF", prompt)
        self.assertNotIn("正常Office预览转换须成功", prompt)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_run_acceptance_and_real_multipart_receiver(self):
        from app.gateway.procurement_review import service as service_module

        with tempfile.TemporaryDirectory() as temp:
            paths = Paths(Path(temp) / "data")
            uploads = paths.sandbox_uploads_dir("thread", user_id="owner")
            uploads.mkdir(parents=True)
            source = uploads / "material.docx"
            source.write_bytes(b"input snapshot")
            output = paths.sandbox_outputs_dir("thread", user_id="owner")
            output.mkdir(parents=True)
            (output / "report.md").write_bytes(report())
            job = {
                "id": str(uuid.uuid4()),
                "request_id": "test-md",
                "payload": {
                    "rule_version": RULE_VERSION,
                    "owner_user_id": "owner",
                    "thread_id": "thread",
                    "run_id": "original",
                    "files": [{"stored": source.name, "name": "材料.docx", "kind": "submitted", "sha256": digest(source)}],
                    "result_status": "succeeded",
                    "message": "完成",
                },
            }
            saved = SimpleNamespace(metadata={"deerflow_timing_run_id": "original"}, checkpoint={"channel_values": {"artifacts": ["/mnt/user-data/outputs/report.md"]}})
            app = SimpleNamespace(
                state=SimpleNamespace(checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=saved)), run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[{"run_id": "original", "status": "success"}])))
            )
            config = SimpleNamespace(callback_url="https://partner.example/callback", callback_key="test-key", callback_attempts=3)
            service = ReviewService(app, None, config)
            storage = Path(temp) / "reports"
            storage.mkdir()
            count = []

            async def partner(request):
                fields, raw = receiver.parse_body(request.headers["content-type"], await request.aread())
                self.assertEqual(raw, report())
                self.assertEqual(receiver.save_delivery(storage, fields, raw), 200)
                count.append(1)
                return httpx.Response(200)

            real_client = httpx.AsyncClient
            with patch.object(service_module, "get_paths", return_value=paths), patch.object(service_module.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(partner), **kw)):
                await service.accept_run(job)
                self.assertFalse(job["payload"]["report_validation"]["content_validation"])
                await service.deliver(job)
                await service.deliver(job)
                saved.checkpoint["channel_values"]["artifacts"] = ["/mnt/user-data/outputs/report.docx"]
                with self.assertRaises(ValueError):
                    await service.accept_run(job)
            self.assertEqual(len(count), 2)
            self.assertEqual((storage / job["id"] / "report.md").read_bytes(), report())
            self.assertEqual(json.loads((storage / job["id"] / "receipt.json").read_text(encoding="utf-8"))["report_format"], "md")
            self.assertEqual(job["state"], "delivered")


if __name__ == "__main__":
    unittest.main()
