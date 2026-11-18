"""Real SQL queue and multipart/report tests; never call a model or partner."""

import asyncio
import base64
import json
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.procurement_review.config import ENDPOINT, ReviewConfig
from app.gateway.procurement_review.files import REFERENCE_ROLES, digest, reference_inventory, snapshot_references, validate_report
from app.gateway.procurement_review.router import router
from app.gateway.procurement_review.rules import MATERIAL_ROLES, RISK_BASELINES, RULE_VERSION, RULES, build_prompt
from app.gateway.procurement_review.service import LeaseLost, ReviewService
from deerflow.persistence.procurement_review.model import ProcurementReviewRow
from deerflow.persistence.procurement_review.sql import ReviewRepository

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


@pytest.fixture
def config(tmp_path):
    return ReviewConfig("review-owner", "review-agent", tmp_path / "refs", "inbound-secret", "https://partner.example/reports", "outbound-secret")


@pytest_asyncio.fixture
async def repo(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "queue.db"))
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: ProcurementReviewRow.__table__.create(conn))
    repository = ReviewRepository(async_sessionmaker(engine, expire_on_commit=False))
    yield repository
    await engine.dispose()


def material():
    return {"id": "material-00", "name": "技术委托要求.png", "stored": "material-00.png", "kind": "submitted", "path": "/mnt/user-data/uploads/material-00.png"}


def result():
    return {
        "project": "测试项目 <A&B>",
        "materials": {role: ["material-00"] for role in MATERIAL_ROLES},
        "coverage": [{"file_id": "material-00", "complete": True, "visual_checked": True, "detail": "测试完整覆盖"}],
        "checks": [{"id": key, "status": "符合", "finding": "测试结论", "basis": "业务规则", "suggestion": "无需修改", "evidence": [{"file_id": "material-00", "location": "第1页", "quote": "测试原文"}]} for key in RULES],
        "notes": [],
    }


def payload(config):
    return {
        "owner_user_id": config.owner_user_id,
        "assistant_id": config.assistant_id,
        "thread_id": "test-thread",
        "callback_url": config.callback_url,
        "deadline": time.time() + 100,
        "files": [material()],
        "reference_version": "v1",
        "reference_sha": "a" * 64,
        "rule_version": RULE_VERSION,
    }


async def make_job(repo, config, request_id="review-1"):
    job, created = await repo.create(job_id=request_id, request_id=request_id, fingerprint="f" * 64, payload=payload(config))
    assert created
    return job


@pytest.mark.asyncio
async def test_idempotency_and_exclusive_lease(repo, config):
    original = await make_job(repo, config)
    duplicate, created = await repo.create(job_id="different", request_id=original["request_id"], fingerprint="f" * 64, payload={})
    assert not created and duplicate["id"] == original["id"]
    claims = await asyncio.gather(repo.claim("a", 90), repo.claim("b", 90))
    assert sum(c is not None for c in claims) == 1
    winner = next(c for c in claims if c)
    loser = "b" if winner["lease_owner"] == "a" else "a"
    assert not await repo.save(winner, loser)
    assert not await repo.renew(winner["id"], loser, 90)
    async with repo.sf() as session:
        await session.execute(update(ProcurementReviewRow).values(lease_until=time.time() - 1))
        await session.commit()
    takeover = await repo.claim(loser, 90)
    assert takeover and not await repo.save(winner, winner["lease_owner"])


@pytest.mark.asyncio
async def test_submit_auth_duplicates_changed_content_and_cleanup(repo, config, tmp_path, monkeypatch):
    import app.gateway.procurement_review.router as router_module
    import deerflow.uploads.manager as uploads_module

    monkeypatch.setattr(router_module, "ensure_uploads_dir", lambda thread_id, user_id: uploads_module.ensure_uploads_dir(thread_id, user_id=user_id))
    monkeypatch.setattr(uploads_module, "get_uploads_dir", lambda thread_id, user_id: tmp_path / thread_id)
    service = SimpleNamespace(config=config, repo=repo, references={"manifest_sha256": "a" * 64, "version": "v1"}, is_running=True)
    app = FastAPI()
    app.include_router(router)
    app.state.procurement_review_service = service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        assert (await client.post(ENDPOINT, content=b"bad multipart")).status_code == 401
        headers = {"Authorization": "Bearer inbound-secret"}

        async def send(data=PNG, extra=None):
            return await client.post(ENDPOINT, headers=headers, data={"request_id": "business-1", **(extra or {})}, files=[("files", ("技术要求.png", data, "image/png"))])

        first = await send()
        assert first.status_code == 202, first.text
        duplicate = await send()
        assert duplicate.json() == first.json()
        assert len(list(tmp_path.glob("procurement-*"))) == 1
        conflict = await send(PNG + b"changed")
        assert conflict.status_code == 409
        assert (await send(extra={"callback_url": "https://untrusted.example"})).status_code in {400, 422}
        assert (await client.post(ENDPOINT, headers=headers, data={"request_id": "r2"}, files=[("files", ("x.html", b"<script/>"))])).status_code == 415
        assert len(list(tmp_path.glob("procurement-*"))) == 1
        saved = await repo.get(first.json()["job_id"])
        assert saved["payload"]["owner_user_id"] == config.owner_user_id
        assert saved["payload"]["files"][0]["name"] == "技术要求.png"
        assert saved["payload"]["callback_url"] == config.callback_url


def make_references(root):
    root.mkdir()
    documents = []
    for role in sorted(REFERENCE_ROLES):
        original = root / (role + ".docx")
        original.write_bytes(b"operator-provided-original")
        text = root / (role + ".txt")
        text.write_text("完整参考内容\n末尾", encoding="utf-8")
        documents.append({"role": role, "original": {"path": original.name, "sha256": digest(original)}, "text": {"path": text.name, "sha256": digest(text)}})
    (root / "manifest.json").write_text(json.dumps({"version": "v1", "documents": documents}), encoding="utf-8")


def test_reference_hashes_and_snapshot(config, tmp_path):
    make_references(config.reference_root)
    inventory = reference_inventory(config.reference_root)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    files = snapshot_references(config.reference_root, uploads, inventory["manifest_sha256"])
    assert len(files) == 10
    assert all((uploads / f["stored"]).is_file() for f in files)
    (config.reference_root / "policy.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        reference_inventory(config.reference_root)


def test_five_references_need_no_review_matrix(config):
    make_references(config.reference_root)
    manifest = config.reference_root / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["documents"] = [d for d in data["documents"] if d["role"] != "review_points"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert len(reference_inventory(config.reference_root)["entries"]) == 10
    data["documents"] = [d for d in data["documents"] if d["role"] != "policy"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        reference_inventory(config.reference_root)


def test_prompt_names_materials_for_all_eight_checks(config):
    prompt = build_prompt({"request_id": "r1", "payload": payload(config)}, [material()])
    assert "不需要上传审查矩阵" in prompt
    assert "SCM-01-01-01" in prompt
    assert prompt.count("【读取材料】") == 8
    assert "5.5.2" in prompt and "不核人员职务" in prompt
    assert "立项审批页" in prompt and "预算下达通知" in prompt
    assert "原编号" not in prompt and "原第" not in prompt and "已取消" not in prompt
    assert "一行表头加八项检查" in prompt


@pytest.mark.asyncio
async def test_recovery_does_not_launch_twice(repo, config):
    await make_job(repo, config)
    app = SimpleNamespace(
        state=SimpleNamespace(
            run_store=SimpleNamespace(
                list_by_thread=AsyncMock(
                    return_value=[
                        {
                            "run_id": "existing-run",
                            "metadata": {"procurement_review_job_id": "review-1"},
                        }
                    ]
                )
            )
        )
    )
    service = ReviewService(app, repo, config)
    job = await repo.claim(service.worker, 90)
    job["state"] = "launching"
    job["payload"]["launch_at"] = time.time()
    await service.step(job)
    saved = await repo.get(job["id"])
    assert saved["state"] == "running"
    assert saved["payload"]["run_id"] == "existing-run"


@pytest.mark.asyncio
async def test_callback_retry_never_reexecutes_agent(repo, config):
    await make_job(repo, config)
    service = ReviewService(SimpleNamespace(), repo, config)
    service.deliver = AsyncMock(side_effect=OSError("secret must not reach callback"))
    job = await repo.claim(service.worker, 90)
    job["state"] = "callback"
    job["payload"].update(result_status="failed", message="审查失败")
    await service._step_guarded(job)
    saved = await repo.get(job["id"])
    assert saved["state"] == "callback"
    assert saved["payload"]["callback_attempts"] == 1
    assert saved["payload"]["message"] == "审查失败"
    assert saved["due_at"] > time.time()
    for _ in range(9):
        service.callback_retry(saved)
    assert saved["state"] == "delivery_failed"


@pytest.mark.asyncio
async def test_expired_lease_cannot_commit(repo, config):
    await make_job(repo, config)
    service = ReviewService(SimpleNamespace(), repo, config)
    job = await repo.claim("other-worker", 90)
    with pytest.raises(LeaseLost):
        await service.save(job)


def test_config_is_disabled_and_keys_are_not_printed(config, monkeypatch):
    monkeypatch.delenv("PROCUREMENT_REVIEW_ENABLED", raising=False)
    assert ReviewConfig.from_env() is None
    assert config.submit_key not in repr(config) and config.callback_key not in repr(config)
    monkeypatch.setenv("PROCUREMENT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("PROCUREMENT_REVIEW_CALLBACK_URL", "http://partner.example/callback")
    with pytest.raises(ValueError, match="HTTPS"):
        ReviewConfig.from_env()


@pytest.mark.parametrize("assistant_id,accepted", [
    ("技术委托要求审查助手", True), ("review-agent", True),
    ("../other", False), ("other/agent", False), ("other\\agent", False),
    ("review\u200b", False), ("review_agent", False), ("审" * 65, False),
    ("lead-agent", False), ("LEAD-AGENT", False),
])
def test_config_uses_platform_agent_identifier(assistant_id, accepted, tmp_path, monkeypatch):
    for key, value in {
        "ENABLED": "true", "OWNER_USER_ID": "owner-1", "ASSISTANT_ID": assistant_id,
        "REFERENCE_ROOT": str(tmp_path), "SUBMIT_KEY": "submit-secret",
        "CALLBACK_URL": "https://partner.example/callback", "CALLBACK_KEY": "callback-secret",
    }.items():
        monkeypatch.setenv("PROCUREMENT_REVIEW_" + key, value)
    if accepted:
        actual = ReviewConfig.from_env()
        assert actual.assistant_id == assistant_id
        assert actual.owner_user_id == "owner-1"
    else:
        with pytest.raises(ValueError):
            ReviewConfig.from_env()


@pytest.mark.asyncio
async def test_cluster_capacity_counts_leased_preparation(repo, config):
    for i in range(6):
        await make_job(repo, config, request_id=f"request-{i}")
    claims = await asyncio.gather(*(repo.claim(f"worker-{i}", 90, max_active=4) for i in range(6)))
    assert sum(job is not None for job in claims) == 4


@pytest.mark.asyncio
async def test_end_to_end_dispatch_report_and_multipart_callback(repo, config, tmp_path, monkeypatch):
    from email import policy
    from email.parser import BytesParser
    from io import BytesIO

    import app.gateway.procurement_review.service as service_module
    import app.gateway.services as gateway_services
    from deerflow.config.paths import Paths
    from deerflow.runtime.user_context import get_effective_user_id

    paths = Paths(tmp_path / "data")
    monkeypatch.setattr(service_module, "get_paths", lambda: paths)
    make_references(config.reference_root)
    references = reference_inventory(config.reference_root)
    job = await make_job(repo, config)
    job["payload"]["reference_sha"] = references["manifest_sha256"]
    uploads = paths.sandbox_uploads_dir("test-thread", user_id=config.owner_user_id)
    uploads.mkdir(parents=True)
    source = uploads / "material-00.png"
    source.write_bytes(PNG)
    job["payload"]["files"][0]["sha256"] = digest(source)
    async with repo.sf() as session:
        await session.execute(update(ProcurementReviewRow).where(ProcurementReviewRow.id == job["id"]).values(payload=job["payload"]))
        await session.commit()
    output = paths.sandbox_outputs_dir("test-thread", user_id=config.owner_user_id)
    output.mkdir(parents=True)
    run = {"run_id": "original-run", "thread_id": "test-thread", "status": "success", "metadata": {"procurement_review_job_id": job["id"]}}
    saved = SimpleNamespace(metadata={"deerflow_timing_run_id": "original-run"}, checkpoint={"channel_values": {"artifacts": ["/mnt/user-data/outputs/procurement-review.docx"]}})
    app = SimpleNamespace(
        state=SimpleNamespace(
            run_store=SimpleNamespace(get=AsyncMock(return_value=run), list_by_thread=AsyncMock(return_value=[run])),
            checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=saved)),
        )
    )
    launches = []

    async def launch(**kwargs):
        assert get_effective_user_id() == config.owner_user_id
        assert kwargs["owner_user_id"] == config.owner_user_id
        assert kwargs["assistant_id"] == config.assistant_id
        assert kwargs["stream_mode"] == ["values", "messages"]
        launches.append(kwargs)
        write_report(output / "procurement-review.docx", report_fixture(), job)
        return {"run_id": "original-run"}

    monkeypatch.setattr(gateway_services, "launch_scheduled_thread_run", launch)
    service = ReviewService(app, repo, config)
    claimed = await repo.claim(service.worker, 90)
    await service._step_guarded(claimed)
    assert (await repo.get(job["id"]))["state"] == "running"

    async def next_step():
        async with repo.sf() as session:
            await session.execute(update(ProcurementReviewRow).where(ProcurementReviewRow.id == job["id"]).values(due_at=0))
            await session.commit()
        claim = await repo.claim(service.worker, 90)
        assert claim
        await service._step_guarded(claim)

    await next_step()
    accepted = await repo.get(job["id"])
    assert accepted["state"] == "callback"
    assert accepted["payload"]["result_status"] == "succeeded"
    received = []

    async def partner(request):
        assert str(request.url) == config.callback_url
        assert request.headers["authorization"] == "Bearer outbound-secret"
        content = await request.aread()
        message = BytesParser(policy=policy.default).parsebytes(("Content-Type: " + request.headers["content-type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + content)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.iter_parts()}
        assert fields["request_id"] == b"review-1" and fields["status"] == b"succeeded"
        assert fields["report"] == (output / "procurement-review.docx").read_bytes()
        with zipfile.ZipFile(BytesIO(fields["report"])) as report:
            assert "核心审查结果" in report.read("word/document.xml").decode()
        received.append(fields)
        return httpx.Response(204)

    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(partner), **kwargs)

    monkeypatch.setattr(service_module.httpx, "AsyncClient", client)
    await next_step()
    assert (await repo.get(job["id"]))["state"] == "delivered"
    assert len(received) == len(launches) == 1


@pytest.mark.asyncio
async def test_reject_other_run_checkpoint(repo, config):
    job = await make_job(repo, config)
    job["payload"]["run_id"] = "run-one"
    app = SimpleNamespace(
        state=SimpleNamespace(
            run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[{"run_id": "run-one", "status": "success"}])),
            checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=SimpleNamespace(metadata={"deerflow_timing_run_id": "run-two"}))),
        )
    )
    with pytest.raises(ValueError, match="different run"):
        await ReviewService(app, repo, config).accept_run(job)


@pytest.mark.asyncio
async def test_launch_uncertainty_fails_without_second_run(repo, config):
    await make_job(repo, config)
    service = ReviewService(
        SimpleNamespace(
            state=SimpleNamespace(
                run_store=SimpleNamespace(list_by_thread=AsyncMock(return_value=[])),
            )
        ),
        repo,
        config,
    )
    job = await repo.claim(service.worker, 90)
    job["state"] = "launching"
    job["payload"]["launch_at"] = time.time() - 121
    await service.step(job)
    saved = await repo.get(job["id"])
    assert saved["state"] == "callback"
    assert saved["payload"]["result_status"] == "failed"


def test_machine_auth_exemption_is_exact():
    from starlette.requests import Request

    from app.gateway.auth_middleware import _is_public
    from app.gateway.csrf_middleware import should_check_csrf

    assert _is_public(ENDPOINT)
    assert not _is_public(ENDPOINT + "/unregistered")
    request = Request({"type": "http", "method": "POST", "path": ENDPOINT, "headers": []})
    assert not should_check_csrf(request)


def write_report(path: Path, result, job):
    """Small OOXML document, plain text only: no macros, links or embedded objects."""

    def paragraph(text, heading=False, highlight=False):
        props = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else ""
        lines = str(text).splitlines() or [""]
        runs = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in lines)
        emphasis = '<w:rPr><w:highlight w:val="yellow"/></w:rPr>' if highlight else ""
        return f"<w:p>{props}<w:r>{emphasis}{runs}</w:r></w:p>"

    p = job["payload"]
    file_names = {f["id"]: f["name"] for f in p["files"]}
    submitted_names = [f["name"] for f in p["files"] if f["kind"] == "submitted"]
    body = [
        paragraph("技术委托要求合规风险核查报告", True),
        paragraph("一、基本信息", True),
        paragraph("项目名称：" + result["project"]),
        paragraph("业务场景：技术委托要求审查\n审查材料：" + "、".join(submitted_names)),
        paragraph("审查日期：" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d（北京时间）")),
        paragraph("审查依据：已确认的八项审查规则、SCM-01-01-01《需求管理实施细则（2026-1）》及适用的技术委托要求模板；具体适用位置见逐项依据。"),
        paragraph(f"业务请求号：{job['request_id']}\n任务号：{job['id']}"),
        paragraph(f"规则版本：{RULE_VERSION}\n参考资料版本：{p['reference_version']}\n参考清单SHA256：{p['reference_sha']}"),
        paragraph("二、核心审查结果", True),
        paragraph("风险基准仅说明问题发生时的等级；符合标为未命中，证据不足标为待核实，不作为已命中高风险统计。"),
    ]
    widths = [650, 1550, 1000, 1000, 3300, 2200, 2500, 2370]

    def row(values, header=False, highlight_column=None):
        props = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if header else "<w:trPr><w:cantSplit/></w:trPr>"
        cells = []
        for index, (value, width) in enumerate(zip(values, widths, strict=True)):
            shade = '<w:shd w:val="clear" w:fill="E7EEF7"/>' if header else ""
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>{paragraph(value, highlight=index == highlight_column)}</w:tc>')
        return "<w:tr>" + props + "".join(cells) + "</w:tr>"

    rows = [row(["序号", "检查项", "判定状态", "风险等级", "命中内容及材料位置", "基准依据", "修正建议", "提示"], header=True)]
    for check in result["checks"]:
        evidence_text = "\n".join(f"材料：{file_names[e['file_id']]}；位置：{e['location']}\n证据：{e['quote']}" for e in check["evidence"])
        rows.append(
            row(
                [
                    check["id"],
                    RULES[check["id"]][0],
                    check["status"],
                    "基准：" + check["risk_baseline"] + "\n本次：" + check["risk_level"],
                    check["finding"] + ("\n" + evidence_text if evidence_text else ""),
                    check["basis"],
                    check["suggestion"],
                    check["hint"],
                ],
                highlight_column=4 if check["status"] == "不符合" else None,
            )
        )
    borders = "".join(f'<w:{edge} w:val="single" w:sz="4" w:color="B7C3D0"/>' for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    body.append(
        '<w:tbl><w:tblPr><w:tblW w:w="14570" w:type="dxa"/>'
        f'<w:tblBorders>{borders}</w:tblBorders><w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
        '<w:bottom w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>'
        f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>" + "".join(rows) + "</w:tbl>"
    )
    body.append(paragraph("三、补充审查意见与材料覆盖", True))
    for entry in result["coverage"]:
        body.append(paragraph(f"{file_names[entry['file_id']]}：{'已完整读取' if entry['complete'] else '未完整读取'}；{entry['detail']}"))
    body.extend(paragraph("[补充建议] " + note) for note in result["notes"])
    if not result["notes"]:
        body.append(paragraph("无补充审查意见。"))
    body.append(paragraph("四、审查结论", True))
    counts = {s: sum(c["status"] == s for c in result["checks"]) for s in ("符合", "不符合", "不适用", "无法判断")}
    body.append(paragraph("；".join(f"{s} {n} 项" for s, n in counts.items())))
    body.append(paragraph("合规评价：" + result["conclusion"]))
    body.append(paragraph("优先整改项：" + ("；".join(RULES[key][0] for key in result["priority_checks"]) or "无已确认高风险命中项。")))
    conclusion = "发现不符合项，详见逐项修改建议。" if counts["不符合"] else "未发现明确不符合项。"
    if result["conclusion"] == "有条件通过":
        conclusion += "须完成中风险问题整改并复核，不代表已整改或正式审批通过。"
    if counts["不适用"] == len(RULES):
        conclusion += "全部检查项均不适用，无法形成有效审查结论。"
    if counts["无法判断"]:
        conclusion += "存在无法判断项，材料或证据不足，不能得出全部符合的结论。"
    body.append(paragraph(conclusion + "本报告不验证签名真实性。"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/><w:pgMar w:top="850" w:right="1134" w:bottom="850" w:left="1134"/></w:sectPr></w:body></w:document>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        )
        archive.writestr(
            "word/styles.xml",
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults>'
            '<w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="宋体"/><w:sz w:val="20"/></w:rPr></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:keepNext/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style></w:styles>',
        )
        archive.writestr("word/document.xml", document)
    os.replace(temporary, path)
    return digest(path)


def report_fixture():
    data = result()
    for check in data["checks"]:
        check.update(risk_baseline=RISK_BASELINES[check["id"]], risk_level="未命中", hint="未发现问题。")
    data.update(priority_checks=[], conclusion="通过")
    return data


def test_direct_word_prompt():
    prompt = build_prompt({"request_id": "request-1"}, [material()])
    assert "生成Word前先自查" in prompt and "无需生成或交付结果JSON" in prompt
    assert "审批完成日期只来自立项审批页" in prompt and "不核人员职务" in prompt
    assert "技术委托要求审查报告.docx" in prompt


def test_accept_agent_word(config, tmp_path):
    path = tmp_path / "report.docx"
    write_report(path, report_fixture(), {"id": "j", "request_id": "r", "payload": payload(config)})
    assert validate_report(path.read_bytes(), [material()["name"]])["checks"] == 8


@pytest.mark.parametrize("kind,expected", [("unknown", "无法判断"), ("inapplicable", "无法判断"), ("high", "不通过"), ("medium", "有条件通过"), ("mixed", "无法判断")])
def test_word_status_risk_conclusion_consistency(config, tmp_path, kind, expected):
    data = report_fixture()
    if kind in {"unknown", "inapplicable"}:
        for check in data["checks"]:
            check.update(status="无法判断" if kind == "unknown" else "不适用", risk_level="待核实" if kind == "unknown" else "不适用")
    else:
        check = data["checks"][0 if kind == "high" else 3]
        check.update(status="不符合", risk_level=check["risk_baseline"])
        if kind == "mixed":
            data["checks"][4].update(status="无法判断", risk_level="待核实")
    data["conclusion"] = expected
    path = tmp_path / "report.docx"
    write_report(path, data, {"id": "j", "request_id": "r", "payload": payload(config)})
    assert validate_report(path.read_bytes(), [material()["name"]])["conclusion"] == expected


@pytest.mark.parametrize("mutation", ["missing_row", "duplicate_row", "empty_evidence", "wrong_risk", "wrong_conclusion", "missing_material", "active_content", "duplicate_part"])
def test_reject_incomplete_agent_word(config, tmp_path, mutation):
    from io import BytesIO

    path = tmp_path / "report.docx"
    write_report(path, report_fixture(), {"id": "j", "request_id": "r", "payload": payload(config)})
    with zipfile.ZipFile(path) as source:
        parts = {n: source.read(n) for n in source.namelist()}
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    doc = ElementTree.fromstring(parts["word/document.xml"])
    table = doc.find(".//w:tbl", ns)
    rows = table.findall("w:tr", ns)
    if mutation == "missing_row":
        table.remove(rows[-1])
    elif mutation == "duplicate_row":
        table.append(ElementTree.fromstring(ElementTree.tostring(rows[1])))
    elif mutation in {"empty_evidence", "wrong_risk"}:
        cells = rows[1].findall("w:tc", ns)
        cells[4 if mutation == "empty_evidence" else 3].clear()
    elif mutation == "wrong_conclusion":
        for t in doc.findall(".//w:t", ns):
            if t.text == "合规评价：通过":
                t.text = "合规评价：不通过"
    elif mutation == "missing_material":
        for t in doc.findall(".//w:t", ns):
            t.text = (t.text or "").replace(material()["name"], "未列举")
    elif mutation == "active_content":
        parts["word/vbaProject.bin"] = b"macro"
    parts["word/document.xml"] = ElementTree.tostring(doc)
    target = BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
        if mutation == "duplicate_part":
            archive.writestr("word/document.xml", parts["word/document.xml"])
    with pytest.raises(ValueError):
        validate_report(target.getvalue(), [material()["name"]])
