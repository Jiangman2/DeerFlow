from pydantic import BaseModel, ConfigDict, Field


class RequestAdmissionConfig(BaseModel):
    """Optional per-process pacing of model requests, shared by quota group."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requests_per_minute: int = Field(gt=0, strict=True)
    group: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    max_wait_seconds: float = Field(default=300, gt=0, allow_inf_nan=False)
    max_queue_size: int = Field(default=256, gt=0, strict=True)


class ModelConfig(BaseModel):
    """Config section for a model"""

    name: str = Field(..., description="Unique name for the model")
    request_admission: RequestAdmissionConfig | None = Field(
        default=None,
        description="Opt-in process-local RPM pacing. Changing an active group's policy requires a process restart.",
    )
    display_name: str | None = Field(..., default_factory=lambda: None, description="Display name for the model")
    description: str | None = Field(..., default_factory=lambda: None, description="Description for the model")
    use: str = Field(
        ...,
        description="Class path of the model provider(e.g. langchain_openai.ChatOpenAI)",
    )
    model: str = Field(..., description="Model name")
    model_config = ConfigDict(extra="allow")
    use_responses_api: bool | None = Field(
        default=None,
        description="Whether to route OpenAI ChatOpenAI calls through the /v1/responses API",
    )
    output_version: str | None = Field(
        default=None,
        description="Structured output version for OpenAI responses content, e.g. responses/v1",
    )
    supports_thinking: bool = Field(default_factory=lambda: False, description="Whether the model supports thinking")
    supports_reasoning_effort: bool = Field(default_factory=lambda: False, description="Whether the model supports reasoning effort")
    when_thinking_enabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is enabled",
    )
    when_thinking_disabled: dict | None = Field(
        default_factory=lambda: None,
        description="Extra settings to be passed to the model when thinking is disabled",
    )
    supports_vision: bool = Field(default_factory=lambda: False, description="Whether the model supports vision/image inputs")
    stream_chunk_timeout: float | None = Field(
        default=None,
        description=(
            "Maximum seconds to wait between successive streaming chunks before "
            "langchain-openai raises StreamChunkTimeoutError. None means use the "
            "factory default (240s for OpenAI-compatible clients). Tune higher for "
            "reasoning models with long thinking pauses; lower for latency-sensitive "
            "interactive endpoints. Has no effect on non-OpenAI-compatible providers."
        ),
    )
    thinking: dict | None = Field(
        default_factory=lambda: None,
        description=(
            "Thinking settings for the model. If provided, these settings will be passed to the model when thinking is enabled. "
            "This is a shortcut for `when_thinking_enabled` and will be merged with `when_thinking_enabled` if both are provided."
        ),
    )
