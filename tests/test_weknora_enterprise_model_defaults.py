from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_SQL = ROOT / "k8s" / "weknora" / "enterprise-model-feature-defaults.sql"
RUNTIME_PATCH = ROOT / "k8s" / "enterprise" / "runtime-patches.yaml"
INTERNAL_MODELS = ROOT / "k8s" / "enterprise" / "builtin-models-internal-only.yaml"


def test_enterprise_defaults_cover_models_knowledge_bases_and_agents():
    sql = DEFAULTS_SQL.read_text(encoding="utf-8")

    for model_id in (
        "__DEERFLOW_DEFAULT_CHAT_MODEL_ID__",
        "__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__",
        "__DEERFLOW_DEFAULT_RERANK_MODEL_ID__",
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
    assert "BEFORE INSERT OR UPDATE ON custom_agents" in sql
    assert "BEFORE INSERT OR UPDATE ON models" in sql
    assert "BEGIN;" in sql
    assert "COMMIT;" in sql
    assert "DPORTAL" not in sql


def test_enterprise_overlay_selects_internal_model_defaults():
    documents = [
        document
        for document in yaml.safe_load_all(RUNTIME_PATCH.read_text(encoding="utf-8"))
        if document
    ]
    job = next(
        document
        for document in documents
        if document.get("kind") == "Job"
        and document.get("metadata", {}).get("name")
        == "weknora-enterprise-model-defaults-v5"
    )
    env = {
        item["name"]: item.get("value")
        for item in job["spec"]["template"]["spec"]["containers"][0]["env"]
    }

    assert env["DEFAULT_CHAT_MODEL_ID"] == "deerflow-cnooc-deepseek-v4-flash"
    assert env["DEFAULT_EMBEDDING_MODEL_ID"] == "deerflow-cnooc-qwen3-embedding-8b"
    assert env["DEFAULT_RERANK_MODEL_ID"] == "deerflow-cnooc-qwen3-reranker-8b"


def test_internal_embedding_and_rerank_use_authenticated_gateway_relay():
    catalog = yaml.safe_load(INTERNAL_MODELS.read_text(encoding="utf-8"))
    models = {model["id"]: model for model in catalog["builtin_models"]}

    for model_id in (
        "deerflow-cnooc-qwen3-embedding-8b",
        "deerflow-cnooc-qwen3-reranker-8b",
    ):
        parameters = models[model_id]["parameters"]
        assert parameters["base_url"] == (
            "http://gateway:8001/api/weknora/internal-models/v1"
        )
        assert parameters["api_key"] == "${WEKNORA_INTERNAL_MODEL_RELAY_KEY}"
        assert parameters["provider"] == "generic"

    assert (
        models["deerflow-cnooc-qwen3-embedding-8b"]["parameters"][
            "embedding_parameters"
        ]["dimension"]
        == 1024
    )
