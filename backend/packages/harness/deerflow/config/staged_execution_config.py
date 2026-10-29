"""Server-owned, opt-in limits for the sequential-stage experiment."""

from pydantic import BaseModel, ConfigDict, Field


class StagedExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Empty means nobody, not everybody. Never read this gate from run context.
    thread_ids: list[str] = Field(default_factory=list, max_length=100)
    max_stages: int = Field(default=6, ge=2, le=8)
    max_stage_model_calls: int = Field(default=6, ge=1, le=12)
    max_total_model_calls: int = Field(default=24, ge=1, le=64)
