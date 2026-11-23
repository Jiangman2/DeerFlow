"""Durable review dispatch and validated reports for HTTP stream delivery."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import reset_current_user, set_current_user
from deerflow.utils.file_io import run_file_io

from .files import digest, reference_inventory, snapshot_references, snapshot_report, validate_report
from .markdown_report import validate_markdown_report
from .rules import RULE_VERSION, build_prompt

LEGACY_RULE_VERSION = "technical-requirements-20261117-v5"
LEGACY_MARKDOWN_VERSION = "technical-requirements-20261119-v6-md"


def report_format(payload):
    version = payload.get("rule_version")
    if version in {RULE_VERSION, LEGACY_MARKDOWN_VERSION}:
        return ".md", "text/markdown; charset=utf-8", validate_markdown_report
    if version == LEGACY_RULE_VERSION:
        return ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", validate_report
    raise ValueError("Unsupported review report version")


logger = logging.getLogger(__name__)


class LeaseLost(RuntimeError):
    pass


class ReviewService:
    def __init__(self, app, repo, config):
        self.app, self.repo, self.config = app, repo, config
        self.worker = uuid.uuid4().hex
        self.references = None
        self.task = None

    @property
    def is_running(self):
        return self.task is not None and not self.task.done()

    async def start(self):
        from app.gateway.deps import get_local_provider
        from deerflow.config.agents_config import load_agent_config
        from deerflow.sandbox.sandbox_provider import get_sandbox_provider

        user = await get_local_provider().get_user(self.config.owner_user_id)
        if user is None or getattr(user, "is_disabled", False):
            raise ValueError("Configured review owner is unavailable")
        agent = await run_file_io(load_agent_config, self.config.assistant_id, user_id=self.config.owner_user_id)
        if agent is None:
            raise ValueError("Configured review employee does not exist")
        if not getattr(get_sandbox_provider(), "uses_thread_data_mounts", False):
            raise ValueError("Procurement review requires shared thread-data mounts")
        self.references = await run_file_io(reference_inventory, self.config.reference_root)
        self.task = asyncio.create_task(self._loop(), name="procurement-review")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _loop(self):
        while True:
            try:
                job = await self.repo.claim(self.worker, self.config.lease_seconds, self.config.max_active_jobs)
                if job:
                    await self.process(job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Logs contain job IDs and exception types, never documents,
                # credentials, raw upstream errors or callback response bodies.
                logger.error("Procurement worker cycle failed (%s)", type(exc).__name__)
            await asyncio.sleep(self.config.poll_seconds)

    async def save(self, job, release=True):
        if not await self.repo.save(job, self.worker, release=release, seconds=self.config.lease_seconds):
            raise LeaseLost()

    async def heartbeat(self, job):
        while True:
            await asyncio.sleep(self.config.lease_seconds / 3)
            if not await self.repo.renew(job["id"], self.worker, self.config.lease_seconds):
                raise LeaseLost()

    async def process(self, job):
        work = asyncio.create_task(self._step_guarded(job))
        heartbeat = asyncio.create_task(self.heartbeat(job))
        try:
            done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    async def _step_guarded(self, job):
        token = set_current_user(SimpleNamespace(id=job["payload"]["owner_user_id"]))
        try:
            await self.step(job)
        except (asyncio.CancelledError, LeaseLost):
            raise
        except Exception as exc:
            logger.error("Procurement job %s failed at %s (%s)", job["id"], job["state"], type(exc).__name__)
            if job["state"] == "launching":
                # The run may have persisted before the dispatcher lost its
                # response. Recover by metadata; NEVER submit a second run.
                job["due_at"] = time.time() + self.config.poll_seconds
            elif job["state"] == "callback":
                self.callback_retry(job)
            else:
                self.fail(job, "审查执行失败，请联系管理员并提供任务号。")
            await self.save(job)
        finally:
            reset_current_user(token)

    def fail(self, job, message):
        job["state"] = "result_ready" if job["payload"].get("delivery_mode") == "stream" else "callback"
        job["payload"].update(result_status="failed", message=message)
        job["due_at"] = time.time()

    async def step(self, job):
        p = job["payload"]
        # Changing configuration must never silently send old reports to a new
        # recipient or execute somebody else's task as the new configured user.
        direct = p.get("delivery_mode") == "stream"
        binding_matches = (p["owner_user_id"], p["assistant_id"]) == (self.config.owner_user_id, self.config.assistant_id)
        delivery_matches = (direct and p["rule_version"] == RULE_VERSION) or (
            not direct and p.get("delivery_mode") is None and p["rule_version"] in {LEGACY_RULE_VERSION, LEGACY_MARKDOWN_VERSION} and bool(self.config.callback_url) and p.get("callback_url") == self.config.callback_url
        )
        if not binding_matches or not delivery_matches or (job["state"] == "queued" and p["rule_version"] != RULE_VERSION):
            job["state"] = "configuration_blocked"
            await self.save(job)
            return
        if job["state"] == "callback":
            if direct:
                job["state"] = "result_ready"
                await self.save(job)
                return
            await self.deliver(job)
            await self.save(job)
            return
        if time.time() > p["deadline"]:
            if p.get("run_id"):
                p["cancel_outcome"] = str(await self.app.state.run_manager.cancel(p["run_id"]))
            self.fail(job, "审查超过任务处理期限，未交付完成报告；请联系管理员。")
            await self.save(job)
            return
        if job["state"] == "queued":
            async with asyncio.timeout(max(1, p["deadline"] - time.time())):
                await self.prepare(job)
            # Commit launch intent BEFORE calling the existing run dispatcher.
            job["state"] = "launching"
            p["launch_at"] = time.time()
            await self.save(job, release=False)
            from app.gateway.services import launch_scheduled_thread_run

            launched = await launch_scheduled_thread_run(
                app=self.app,
                thread_id=p["thread_id"],
                assistant_id=p["assistant_id"],
                owner_user_id=p["owner_user_id"],
                prompt=build_prompt(job, p["files"]),
                stream_mode=["values", "messages"],
                metadata={"source": "procurement_review", "procurement_review_job_id": job["id"]},
            )
            p["run_id"] = launched["run_id"]
            job["state"] = "running"
        elif job["state"] == "launching":
            runs = await self.app.state.run_store.list_by_thread(p["thread_id"], user_id=p["owner_user_id"])
            matching = [r for r in runs if (r.get("metadata") or {}).get("procurement_review_job_id") == job["id"]]
            if len(matching) == 1:
                p["run_id"] = matching[0]["run_id"]
                job["state"] = "running"
            elif len(matching) > 1 or time.time() - p["launch_at"] > 120:
                self.fail(job, "任务启动结果无法确认，未重复发起审查；请联系管理员。")
        elif job["state"] == "running":
            run = await self.app.state.run_store.get(p["run_id"], user_id=p["owner_user_id"])
            if not run or run["thread_id"] != p["thread_id"] or (run.get("metadata") or {}).get("procurement_review_job_id") != job["id"]:
                self.fail(job, "任务执行记录不匹配，无法交付报告。")
            elif run["status"] == "success":
                await self.accept_run(job)
                p.update(result_status="succeeded", message="审查已完成，结论及无法判断项见审查报告。")
                job["state"] = "result_ready" if direct else "callback"
            elif run["status"] not in {"pending", "running"}:
                self.fail(job, "数字员工未完成审查，未交付完成报告。")
        job["due_at"] = time.time() + self.config.poll_seconds
        await self.save(job)

    async def prepare(self, job):
        from app.gateway.routers.uploads import _auto_convert_documents_enabled, _make_uploaded_paths_sandbox_readable, _markdown_companion_has_content, _require_parsed_text_enabled, _write_parsed_markdown
        from app.gateway.upload_document_memory_guard import parse_conversation_document_file
        from app.gateway.upload_document_parser import conversation_document_parser_endpoint
        from deerflow.config.app_config import get_app_config
        from deerflow.utils.file_conversion import convert_file_to_markdown

        p = job["payload"]
        uploads = get_paths().sandbox_uploads_dir(p["thread_id"], user_id=p["owner_user_id"])
        submitted = [f for f in p["files"] if f["kind"] == "submitted"]
        for file in submitted:
            path = uploads / file["stored"]
            if await run_file_io(digest, path) != file["sha256"]:
                raise ValueError("Submitted material changed")
            # Match ordinary conversation uploads: text/JSON/CSV stay as
            # originals; spreadsheets use the existing opt-in local converter,
            # its isolation limits and the shared require-parsed-text policy.
            if path.suffix.lower() in {".xls", ".xlsx"} and _auto_convert_documents_enabled(get_app_config()):
                parsed = await convert_file_to_markdown(path)
                if parsed and await run_file_io(_markdown_companion_has_content, Path(parsed)):
                    file["text"] = "/mnt/user-data/uploads/" + Path(parsed).name
                    file["text_sha256"] = await run_file_io(digest, parsed)
                elif _require_parsed_text_enabled():
                    raise ValueError("Spreadsheet parsing failed; ordinary upload policy requires parsed text")
                else:
                    file["parse_note"] = "表格解析未生成有效文本，按普通问答保留原件；必须核对原件，无法读取则标明无法判断。"
            endpoint = conversation_document_parser_endpoint()
            if endpoint and path.suffix in {".pdf", ".doc", ".docx"}:
                try:
                    markdown = await parse_conversation_document_file(endpoint=endpoint, filename=file["name"], file_path=path)
                    parsed = await run_file_io(_write_parsed_markdown, path, markdown)
                    file["text"] = "/mnt/user-data/uploads/" + parsed.name
                    file["text_sha256"] = await run_file_io(digest, parsed)
                except Exception:
                    file["parse_note"] = "全文解析失败；需读取原件及图像，无法完整读取则在报告标明。"
        refs = await run_file_io(snapshot_references, self.config.reference_root, uploads, p["reference_sha"])
        p["files"] = submitted + refs
        await run_file_io(lambda: _make_uploaded_paths_sandbox_readable(list(uploads.iterdir())))

    async def accept_run(self, job):
        p = job["payload"]
        runs = await self.app.state.run_store.list_by_thread(p["thread_id"], user_id=p["owner_user_id"], limit=2)
        if len(runs) != 1 or runs[0]["run_id"] != p["run_id"] or runs[0]["status"] != "success":
            raise ValueError("Result must belong to the original review run")
        saved = await self.app.state.checkpointer.aget_tuple({"configurable": {"thread_id": p["thread_id"]}})
        if (getattr(saved, "metadata", None) or {}).get("deerflow_timing_run_id") != p["run_id"]:
            raise ValueError("Result checkpoint belongs to a different run")
        artifacts = saved.checkpoint.get("channel_values", {}).get("artifacts", [])
        if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], str) or Path(artifacts[0]).suffix.lower() != report_format(p)[0]:
            raise ValueError("Review must present exactly one report matching its version")
        await run_file_io(self.accept, job, artifacts[0])
        current = await self.app.state.run_store.list_by_thread(p["thread_id"], user_id=p["owner_user_id"], limit=2)
        if current != runs:
            raise ValueError("Thread changed during acceptance")

    def accept(self, job, virtual_path):
        p = job["payload"]
        paths = get_paths()
        uploads = paths.sandbox_uploads_dir(p["thread_id"], user_id=p["owner_user_id"])
        for file in p["files"]:
            path = uploads / file["stored"]
            if path.is_symlink() or digest(path) != file["sha256"]:
                raise ValueError("Input changed during review")
            if file.get("text"):
                companion = path.with_suffix(".md")
                if companion.is_symlink() or digest(companion) != file["text_sha256"]:
                    raise ValueError("Parsed companion changed during review")
        outputs = paths.sandbox_outputs_dir(p["thread_id"], user_id=p["owner_user_id"])
        output = paths.resolve_virtual_path(p["thread_id"], virtual_path, user_id=p["owner_user_id"])
        if output.is_symlink() or not output.resolve().is_relative_to(outputs.resolve()) or output.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("Invalid result path or size")
        with output.open("rb") as stream:
            raw = stream.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Result grew beyond limit")
        p["report_validation"] = report_format(p)[2](raw, [f["name"] for f in p["files"] if f["kind"] == "submitted"])
        # Store the delivery snapshot outside sandbox-visible user-data.
        report = paths.thread_dir(p["thread_id"], user_id=p["owner_user_id"]) / "procurement-delivery" / ("report" + report_format(p)[0])
        if report.parent.is_symlink() or report.is_symlink():
            raise ValueError("Invalid delivery snapshot path")
        p["report_sha256"] = snapshot_report(report, raw)

    def callback_retry(self, job):
        attempts = job["payload"].get("callback_attempts", 0) + 1
        job["payload"]["callback_attempts"] = attempts
        job["state"] = "delivery_failed" if attempts >= self.config.callback_attempts else "callback"
        job["due_at"] = time.time() + min(3600, 30 * 2 ** min(attempts - 1, 7))

    async def deliver(self, job):
        p = job["payload"]
        if p.get("delivery_mode") == "stream":
            raise ValueError("Stream jobs must never use callback delivery")
        parts = [
            (key, (None, str(value)))
            for key, value in {
                "request_id": job["request_id"],
                "job_id": job["id"],
                "status": p["result_status"],
                "message": p["message"],
            }.items()
        ]
        if p["result_status"] == "succeeded":
            report = get_paths().thread_dir(p["thread_id"], user_id=p["owner_user_id"]) / "procurement-delivery" / ("report" + report_format(p)[0])

            def read_report():
                if report.parent.is_symlink() or report.is_symlink() or report.stat().st_size > 5 * 1024 * 1024 or digest(report) != p["report_sha256"]:
                    raise ValueError("Delivery report changed")
                with report.open("rb") as stream:
                    data = stream.read(5 * 1024 * 1024 + 1)
                if len(data) > 5 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != p["report_sha256"]:
                    raise ValueError("Delivery report failed integrity check")
                return data

            parts.append(("report", ("review-report" + report_format(p)[0], await run_file_io(read_report), report_format(p)[1])))
        async with asyncio.timeout(45):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
                async with client.stream("POST", self.config.callback_url, headers={"Authorization": "Bearer " + self.config.callback_key}, files=parts) as response:
                    if response.status_code not in {200, 204}:
                        raise ValueError("Callback not acknowledged")
        job["state"] = "delivered"
        p["delivered_at"] = time.time()


async def start_review_service(app):
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.procurement_review.sql import ReviewRepository

    from .config import ReviewConfig

    config = ReviewConfig.from_env()
    if config is None:
        return None
    sf = get_session_factory()
    if sf is None:
        raise ValueError("Procurement review requires SQL persistence")
    service = ReviewService(app, ReviewRepository(sf), config)
    await service.start()
    return service
