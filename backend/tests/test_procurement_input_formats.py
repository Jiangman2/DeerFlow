import hashlib
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.gateway.procurement_review.router import ALLOWED
from app.gateway.procurement_review.service import ReviewService
from deerflow.config.paths import Paths


class InputTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, suffix, enabled, required, output):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as patches:
            paths = Paths(Path(temp))
            uploads = paths.sandbox_uploads_dir("thread", user_id="owner")
            uploads.mkdir(parents=True)
            original = uploads / ("material-00" + suffix)
            original.write_bytes(b"unchanged test input")
            f = {"kind": "submitted", "stored": original.name, "name": "input" + suffix, "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}
            job = {"payload": {"owner_user_id": "owner", "thread_id": "thread", "files": [f], "reference_sha": "test"}}

            async def convert(path):
                self.assertEqual(path, original)
                if output is None:
                    return None
                companion = original.with_suffix(".md")
                companion.write_text(output, encoding="utf-8", newline="\n")
                return companion

            converter = patches.enter_context(patch("deerflow.utils.file_conversion.convert_file_to_markdown", new=AsyncMock(side_effect=convert)))
            patches.enter_context(patch("deerflow.config.app_config.get_app_config", return_value=SimpleNamespace()))
            patches.enter_context(patch("app.gateway.routers.uploads._auto_convert_documents_enabled", return_value=enabled))
            patches.enter_context(patch("app.gateway.routers.uploads._require_parsed_text_enabled", return_value=required))
            patches.enter_context(patch("app.gateway.routers.uploads._make_uploaded_paths_sandbox_readable"))
            patches.enter_context(patch("app.gateway.upload_document_parser.conversation_document_parser_endpoint", return_value=""))
            patches.enter_context(patch("app.gateway.procurement_review.service.get_paths", return_value=paths))
            patches.enter_context(patch("app.gateway.procurement_review.service.snapshot_references", return_value=[]))
            service = ReviewService(None, None, SimpleNamespace(reference_root=Path(temp)))
            await service.prepare(job)
            self.assertEqual(original.read_bytes(), b"unchanged test input")
            return f, converter.await_count

    async def test_excel_reuses_ordinary_converter(self):
        for suffix in (".xlsx", ".xls"):
            f, count = await self.prepare(suffix, True, True, "# worksheet\n| a | b |")
            self.assertEqual(count, 1)
            self.assertEqual(f["text"], "/mnt/user-data/uploads/material-00.md")
            self.assertEqual(f["text_sha256"], hashlib.sha256(b"# worksheet\n| a | b |").hexdigest())

    async def test_text_originals_and_conversion_switch(self):
        for suffix in (".json", ".txt", ".csv", ".xlsx", ".xls"):
            self.assertIn(suffix, ALLOWED)
            f, count = await self.prepare(suffix, suffix not in (".xlsx", ".xls"), True, "# unused")
            self.assertEqual(count, 0)
            self.assertNotIn("text", f)

    async def test_shared_failure_policy_no_blank_companion(self):
        for output in (None, "   \n"):
            with self.assertRaises(ValueError):
                await self.prepare(".xlsx", True, True, output)
            f, count = await self.prepare(".xls", True, False, output)
            self.assertEqual(count, 1)
            self.assertNotIn("text", f)
            self.assertIn("parse_note", f)


if __name__ == "__main__":
    unittest.main()
