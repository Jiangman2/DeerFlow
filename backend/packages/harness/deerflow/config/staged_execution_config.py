"""Server-owned, opt-in limits for the sequential-stage experiment."""

from pydantic import BaseModel, ConfigDict, Field


class StagedExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    # Empty thread_ids and team_agents enable nobody. Never read these gates from run context.
    thread_ids: list[str] = Field(default_factory=list, max_length=100)
    # Exact internal team-agent names, resolved server-side; personal shadows are excluded.
    team_agents: list[str] = Field(default_factory=list, max_length=100)
    max_stages: int = Field(default=6, ge=2, le=8)
    max_stage_model_calls: int = Field(default=6, ge=1, le=12)
    max_total_model_calls: int = Field(default=24, ge=1, le=64)
