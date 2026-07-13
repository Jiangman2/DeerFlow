"""Configuration for automatic thread title generation."""

from pydantic import BaseModel, Field


class TitleConfig(BaseModel):
    """Configuration for automatic thread title generation."""

    enabled: bool = Field(
        default=True,
        description="Whether to enable automatic title generation",
    )
    max_words: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Maximum number of words in the generated title",
    )
    max_chars: int = Field(
        default=60,
        ge=10,
        le=200,
        description="Maximum number of characters in the generated title",
    )
    model_name: str | None = Field(
        default=None,
        description="Model name to use for LLM title generation (None = use local fallback title)",
    )
    prompt_template: str = Field(
        default=(
            "请为下面这段对话生成一个简洁的中文标题，最多 {max_words} 个词，最多 {max_chars} 个字符。\n"
            "要求：\n"
            "1. 必须使用简体中文。\n"
            "2. 不要输出英文标题，除非用户原始主题是无法翻译的专有名词。\n"
            "3. 不要加引号、句号、解释或前缀。\n"
            "4. 标题应概括用户任务，而不是描述助手身份。\n\n"
            "用户：{user_msg}\n"
            "助手：{assistant_msg}\n\n"
            "只返回标题。"
        ),
        description="Prompt template for LLM title generation when model_name is set",
    )


# Global configuration instance
_title_config: TitleConfig = TitleConfig()


def get_title_config() -> TitleConfig:
    """Get the current title configuration."""
    return _title_config


def set_title_config(config: TitleConfig) -> None:
    """Set the title configuration."""
    global _title_config
    _title_config = config


def load_title_config_from_dict(config_dict: dict) -> None:
    """Load title configuration from a dictionary."""
    global _title_config
    _title_config = TitleConfig(**config_dict)


def reset_title_config() -> None:
    """Restore the title configuration to its pristine ``TitleConfig()`` default.

    Public API so that tests do not have to reach into the private
    ``_title_config`` module attribute. ``AppConfig.from_file()`` calls
    :func:`load_title_config_from_dict`, which permanently mutates the
    singleton; tests that need a clean slate between cases should call
    this between tests.
    """
    global _title_config
    _title_config = TitleConfig()
