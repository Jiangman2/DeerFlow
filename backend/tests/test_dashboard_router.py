from app.gateway.routers.dashboard import (
    _extract_artifact_paths,
    classify_dashboard_features,
)
from deerflow.persistence.models.run_event import RunEventRow


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
