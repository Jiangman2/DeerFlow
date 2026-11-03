"""Per-run policy registration for the Feishu channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.channels.run_policy import CHANNEL_RUN_POLICY, ChannelRunPolicy

if TYPE_CHECKING:
    from app.channels.message_bus import InboundMessage

logger = logging.getLogger(__name__)

# Names of the request-scoped secrets installed into ``run_context["secrets"]``
# for Feishu-triggered runs. They mirror the ``required-secrets`` declared by
# the ``feishu-document-summary`` skill; the skill-activation middleware binds
# them for injection into that skill's sandbox subprocess when the model loads
# the skill (issue #3861/#3914 mechanism). Values are never logged.
_FEISHU_SECRET_KEYS = ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_DOMAIN")


async def inject_feishu_credentials(msg: InboundMessage, run_context: dict[str, Any]) -> None:
    """Install Feishu app credentials into ``run_context["secrets"]``.

    Skills running inside the sandbox cannot inherit the host process
    environment — ``env_policy.build_sandbox_env`` scrubs every
    KEY/SECRET/TOKEN-like variable. The only sanctioned path is the
    required-secrets carrier: the caller puts values into
    ``run_context["secrets"]`` and an activated skill that declares the
    names via ``required-secrets`` gets them injected as env vars in its
    own subprocess only.

    The values come from the live ``channels.feishu`` config tracked by
    the running ChannelService (falling back to the channel instance's
    merged config), so a runtime config update from the UI takes effect
    on the next run without a restart.

    Failures (channel not started, keys missing) degrade silently: the
    run proceeds without credentials and the skill's reader script will
    report a ``token_failed`` error to the user instead of crashing the
    delivery.
    """
    if msg.channel_name != "feishu":
        return

    from app.channels.service import get_channel_service

    config: dict[str, Any] | None = None
    service = get_channel_service()
    if service is not None:
        config = service.get_channel_config("feishu")
        if config is None:
            channel = service.get_channel("feishu")
            channel_config = getattr(channel, "config", None)
            if isinstance(channel_config, dict):
                config = channel_config
    if not config:
        logger.debug("[feishu-run-policy] no live feishu channel config; skipping credential injection")
        return

    app_id = config.get("app_id")
    app_secret = config.get("app_secret")
    domain = config.get("domain") or "https://open.feishu.cn"
    if not isinstance(app_id, str) or not app_id or not isinstance(app_secret, str) or not app_secret:
        logger.warning("[feishu-run-policy] feishu config lacks app_id/app_secret; skills requiring Feishu secrets will fail")
        return

    secrets = run_context.get("secrets")
    if not isinstance(secrets, dict):
        secrets = {}
        run_context["secrets"] = secrets
    secrets.setdefault("FEISHU_APP_ID", app_id)
    secrets.setdefault("FEISHU_APP_SECRET", app_secret)
    secrets.setdefault("FEISHU_DOMAIN", str(domain))
    logger.info("[feishu-run-policy] installed feishu app credentials for skill secret binding (keys=%s)", ", ".join(_FEISHU_SECRET_KEYS))


def register_policy() -> None:
    """Register Feishu's queue-same-thread behavior in the shared policy map."""
    CHANNEL_RUN_POLICY["feishu"] = ChannelRunPolicy(
        serialize_thread_runs=True,
        credentials_provider=inject_feishu_credentials,
    )


register_policy()
