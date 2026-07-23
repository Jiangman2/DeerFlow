import json
from datetime import UTC, datetime, timedelta

from app.gateway.routers.dashboard import (
    _day_key,
    _extract_artifact_paths,
    _run_status_summary,
    _summarize_artifact_events,
    classify_dashboard_features,
)
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.run.model import RunRow


def test_classify_dashboard_features_detects_business_buckets():
    features = classify_dashboard_features(
        "run ppt-master with svg_to_pptx, present_files and weknora retrieval",
        {"tool": "image_gen.py", "path": "/mnt/user-data/outputs/report.pptx"},
    )

    assert "PPT生成" in features
    assert "知识库检索" in features
    assert "图片生成" in features
    assert "产物生成" in features


def test_classify_dashboard_features_returns_empty_for_plain_chat():
    assert classify_dashboard_features("hello, summarize this paragraph") == set()


def test_classify_dashboard_features_ignores_run_end_system_prompt_payload():
    payload = {
        "messages": [
            "The system prompt documents weknora_search, present_files, read_file, bash, and ppt-master.",
        ],
    }

    assert classify_dashboard_features(json.dumps(payload), event_type="run.end") == set()


def test_classify_dashboard_features_reads_actual_tool_calls_only():
    payload = {
        "content": "The assistant may mention unrelated tools here.",
        "tool_calls": [
            {
                "name": "weknora_search",
                "args": {"query": "reservoir"},
            },
            {
                "name": "present_files",
                "args": {
                    "filepaths": [
                        "/mnt/user-data/outputs/report.pdf",
                    ],
                },
            },
        ],
    }

    assert classify_dashboard_features(
        json.dumps(payload),
        event_type="llm.ai.response",
    ) == {"知识库检索", "产物生成"}


def test_classify_dashboard_features_does_not_treat_assistant_prose_as_tool_usage():
    payload = {
        "content": "I can use WeKnora or present_files if you select a knowledge base.",
        "tool_calls": [],
    }

    assert (
        classify_dashboard_features(
            json.dumps(payload),
            event_type="llm.ai.response",
        )
        == set()
    )


def test_presenting_an_existing_ppt_does_not_count_as_ppt_generation():
    payload = {
        "content": "",
        "tool_calls": [
            {
                "name": "present_files",
                "args": {"filepaths": ["/mnt/user-data/outputs/existing.pptx"]},
            },
        ],
    }

    assert classify_dashboard_features(
        json.dumps(payload),
        event_type="llm.ai.response",
    ) == {"产物生成"}


def test_knowledge_query_text_does_not_create_unrelated_feature_counts():
    payload = {
        "content": "",
        "tool_calls": [
            {
                "name": "weknora_search",
                "args": {"query": "查找现有 PPT presentation 模板"},
            },
        ],
    }

    assert classify_dashboard_features(
        json.dumps(payload, ensure_ascii=False),
        event_type="llm.ai.response",
    ) == {"知识库检索"}


def test_classify_dashboard_features_prefers_structured_metadata():
    features = classify_dashboard_features(
        "ordinary tool event",
        {
            "business_features": [
                "knowledge_retrieval",
                "artifact_generation",
                "PPT生成",
            ],
        },
    )

    assert features == {"知识库检索", "产物生成", "PPT生成"}


def test_extract_artifact_paths_reads_structured_metadata_and_content():
    event = RunEventRow(
        thread_id="thread-1",
        run_id="run-1",
        event_type="tool",
        category="trace",
        content="saved outputs/report.xlsx and /mnt/user-data/outputs/chart.png",
        event_metadata={
            "artifacts": [
                {"path": "/mnt/user-data/outputs/deck.pptx"},
                "outputs/summary.docx",
            ],
        },
        seq=1,
    )
    paths = _extract_artifact_paths(event)

    assert set(paths) == {
        "/mnt/user-data/outputs/chart.png",
        "/mnt/user-data/outputs/deck.pptx",
        "/mnt/user-data/outputs/report.xlsx",
        "/mnt/user-data/outputs/summary.docx",
    }


def test_extract_artifact_paths_ignores_run_end_prompt_history():
    event = RunEventRow(
        thread_id="thread-1",
        run_id="run-1",
        event_type="run.end",
        category="outputs",
        content=json.dumps(
            {
                "messages": [
                    "System instructions mention /mnt/user-data/outputs/example.pptx",
                ],
            },
        ),
        event_metadata={"status": "success"},
        seq=1,
    )

    assert _extract_artifact_paths(event) == []


def test_extract_artifact_paths_reads_present_files_arguments_and_trims_punctuation():
    event = RunEventRow(
        thread_id="thread-1",
        run_id="run-1",
        event_type="llm.ai.response",
        category="message",
        content=json.dumps(
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "present_files",
                        "args": {
                            "filepaths": [
                                "/mnt/user-data/outputs/年度报告 2026.pptx",
                                "/mnt/user-data/outputs/年度报告 2026.preview.pdf",
                            ],
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        event_metadata={},
        seq=1,
    )

    assert _extract_artifact_paths(event) == [
        "/mnt/user-data/outputs/年度报告 2026.pptx",
        "/mnt/user-data/outputs/年度报告 2026.preview.pdf",
    ]


def test_artifact_summary_deduplicates_per_thread_and_counts_types_after_dedup():
    created_at = datetime(2026, 7, 23, tzinfo=UTC)
    events = [
        (
            RunEventRow(
                thread_id="thread-1",
                run_id="run-1",
                event_type="llm.ai.response",
                category="message",
                content="",
                event_metadata={},
                seq=1,
                created_at=created_at,
            ),
            "/mnt/user-data/outputs/report.pdf",
        ),
        (
            RunEventRow(
                thread_id="thread-1",
                run_id="run-1",
                event_type="llm.tool.result",
                category="message",
                content="",
                event_metadata={},
                seq=2,
                created_at=created_at + timedelta(seconds=1),
            ),
            "/mnt/user-data/outputs/report.pdf",
        ),
        (
            RunEventRow(
                thread_id="thread-2",
                run_id="run-2",
                event_type="llm.ai.response",
                category="message",
                content="",
                event_metadata={},
                seq=1,
                created_at=created_at + timedelta(seconds=2),
            ),
            "/mnt/user-data/outputs/report.pdf",
        ),
    ]

    summary = _summarize_artifact_events(events, limit=10)

    assert summary.total == 2
    assert summary.previewable == 2
    assert [(item.type, item.count) for item in summary.by_type] == [("文档", 2)]


def test_run_status_summary_excludes_active_runs_from_success_rate_and_duration():
    created_at = datetime(2026, 7, 23, tzinfo=UTC)
    runs = [
        RunRow(
            run_id="success",
            thread_id="thread-1",
            status="success",
            multitask_strategy="reject",
            metadata_json={},
            kwargs_json={},
            message_count=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_tokens=0,
            llm_call_count=0,
            lead_agent_tokens=0,
            subagent_tokens=0,
            middleware_tokens=0,
            token_usage_by_model={},
            created_at=created_at,
            updated_at=created_at + timedelta(seconds=10),
        ),
        RunRow(
            run_id="running",
            thread_id="thread-2",
            status="running",
            multitask_strategy="reject",
            metadata_json={},
            kwargs_json={},
            message_count=0,
            total_input_tokens=0,
            total_output_tokens=0,
            total_tokens=0,
            llm_call_count=0,
            lead_agent_tokens=0,
            subagent_tokens=0,
            middleware_tokens=0,
            token_usage_by_model={},
            created_at=created_at,
            updated_at=created_at + timedelta(seconds=2),
        ),
    ]

    summary = _run_status_summary(runs)

    assert summary.successful == 1
    assert summary.failed == 0
    assert summary.running == 1
    assert summary.terminal == 1
    assert summary.success_rate == 1.0
    assert summary.duration_values == [10_000]


def test_day_key_uses_beijing_calendar_day():
    assert _day_key(datetime(2026, 7, 22, 16, 30, tzinfo=UTC)) == "2026-07-23"
