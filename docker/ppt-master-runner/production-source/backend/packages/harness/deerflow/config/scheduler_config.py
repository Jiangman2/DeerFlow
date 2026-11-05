from pydantic import BaseModel, Field


class SchedulerConfig(BaseModel):
    enabled: bool = Field(default=False)
    poll_interval_seconds: int = Field(default=5, ge=1, le=300)
    lease_seconds: int = Field(default=120, ge=5, le=3600)
    max_concurrent_runs: int = Field(default=3, ge=1, le=32)
    min_once_delay_seconds: int = Field(default=60, ge=1, le=86400)
    recursion_limit: int = Field(
        default=300,
        ge=1,
        description=(
            "LangGraph super-step budget for scheduled runs. The Gateway clamps "
            "this value to AppConfig.max_recursion_limit."
        ),
    )
