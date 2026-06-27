from pydantic import BaseModel, Field


class KnowledgeConfig(BaseModel):
    """Configuration for the user-private knowledge base."""

    semantic_search_enabled: bool = Field(
        default=True,
        description="Enable embedding-based semantic retrieval when the database and embedding provider support it.",
    )
    embedding_model: str = Field(
        default="text-embedding-v4",
        description="Embedding model name. The default matches DashScope's OpenAI-compatible text embedding model.",
    )
    embedding_dimensions: int = Field(
        default=1024,
        ge=2,
        le=16000,
        description="Embedding vector dimensions. Must match the pgvector column dimensions.",
    )
    embedding_api_key: str | None = Field(
        default=None,
        description="API key for the OpenAI-compatible embedding endpoint. Use $ENV_VAR in config.yaml.",
    )
    embedding_base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible embedding API base URL.",
    )
    embedding_batch_size: int = Field(
        default=10,
        ge=1,
        le=128,
        description="Maximum number of chunks to embed in one API call.",
    )
    semantic_candidates: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Number of semantic nearest-neighbor candidates to retrieve before hybrid ranking.",
    )
    keyword_candidates: int = Field(
        default=300,
        ge=1,
        le=1000,
        description="Number of keyword candidates to retrieve for hybrid ranking and fallback.",
    )
    semantic_weight: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Hybrid ranking weight for semantic similarity.",
    )
    keyword_weight: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Hybrid ranking weight for keyword relevance.",
    )
    fallback_to_keyword: bool = Field(
        default=True,
        description="Fall back to keyword retrieval if semantic retrieval is unavailable or returns no candidates.",
    )
