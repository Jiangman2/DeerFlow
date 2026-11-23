"""Operator-owned configuration; none of these settings are caller parameters."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from deerflow.config.agents_config import validate_agent_name

ENDPOINT = "/api/v1/integrations/procurement-review"


@dataclass(frozen=True)
class ReviewConfig:
    owner_user_id: str
    assistant_id: str
    reference_root: Path
    submit_key: str = field(repr=False)
    callback_url: str = field(repr=False)
    callback_key: str = field(repr=False)
    max_files: int = 12
    max_file_bytes: int = 30 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024
    job_timeout: int = 1800
    callback_attempts: int = 10
    lease_seconds: int = 90
    poll_seconds: int = 5
    max_active_jobs: int = 4

    @classmethod
    def from_env(cls) -> ReviewConfig | None:
        prefix = "PROCUREMENT_REVIEW_"
        if os.getenv(prefix + "ENABLED", "false").lower() not in {"true", "1"}:
            return None

        def required(name: str) -> str:
            value = os.getenv(prefix + name, "").strip()
            if not value:
                raise ValueError(f"Missing {prefix}{name}")
            return value

        url = required("CALLBACK_URL")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("Callback must be a fixed HTTPS URL without credentials, query or fragment")
        root = Path(required("REFERENCE_ROOT"))
        if not root.is_absolute():
            raise ValueError("Reference root must be absolute")
        assistant_id = required("ASSISTANT_ID")
        validate_agent_name(assistant_id)
        if assistant_id.casefold() == "lead-agent":
            raise ValueError("Use a dedicated custom-agent identifier")
        return cls(
            owner_user_id=required("OWNER_USER_ID"),
            assistant_id=assistant_id,
            reference_root=root,
            submit_key=required("SUBMIT_KEY"),
            callback_url=url,
            callback_key=required("CALLBACK_KEY"),
        )
