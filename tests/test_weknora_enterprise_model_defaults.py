from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_SQL = ROOT / "k8s" / "weknora" / "enterprise-model-feature-defaults.sql"


def test_enterprise_defaults_cover_models_knowledge_bases_and_agents():
    sql = DEFAULTS_SQL.read_text(encoding="utf-8")

    for model_id in (
        "deerflow-cnooc-deepseek-v4-flash",
        "deerflow-dashscope-embedding",
        "deerflow-dashscope-rerank",
        "deerflow-dashscope-vlm",
        "deerflow-dashscope-asr",
    ):
        assert model_id in sql

    assert "UPDATE knowledge_bases" in sql
    assert "UPDATE custom_agents" in sql
    assert "'enable_multimodal', true" in sql
    assert "'image_upload_enabled', true" in sql
    assert "'attachment_image_understanding', true" in sql
    assert "'audio_upload_enabled', true" in sql


def test_enterprise_defaults_install_insert_and_model_guards():
    sql = DEFAULTS_SQL.read_text(encoding="utf-8")

    assert "BEFORE INSERT ON knowledge_bases" in sql
    assert "BEFORE INSERT ON custom_agents" in sql
    assert "BEFORE INSERT OR UPDATE ON models" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "DPORTAL" not in sql


def test_enterprise_defaults_migrate_embedding_to_gateway_relay():
    sql = DEFAULTS_SQL.read_text(encoding="utf-8")

    assert "WHERE id = 'deerflow-dashscope-embedding'" in sql
    assert "to_jsonb('http://gateway:8001/api/weknora/bailian/v1'::text)" in sql
    assert "to_jsonb('generic'::text)" in sql
