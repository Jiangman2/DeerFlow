"""Read-only feature-flag endpoint for the frontend bootstrap.

Reports which optional, config-gated features are exposed over HTTP so the
frontend can gate UI and avoid firing requests that the backend would reject
with 403. Reads through ``get_config`` so edits to ``config.yaml`` take effect
on the next request without a restart (config hot-reload boundary).
"""

import hmac
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.browser_capability import browser_capability
from app.gateway.deps import get_config
from app.gateway.routers.ppt_confirmations import router as ppt_confirmations_router
from deerflow.config.app_config import AppConfig
from deerflow.persistence import get_session_factory

router = APIRouter(prefix="/api", tags=["features"])
router.include_router(ppt_confirmations_router)


class AgentsApiFeature(BaseModel):
    """Availability of the custom-agent management API."""

    enabled: bool = Field(..., description="Whether the agents_api routes are exposed over HTTP")


class BrowserControlFeature(BaseModel):
    """Availability of live agentic browser control."""

    enabled: bool = Field(..., description="Whether the live browser routes and UI are available")


class PptConfirmationFeature(BaseModel):
    """Availability, never a global automatic-consent setting."""

    enabled: bool
    protocol: str = "ppt-confirmation-v1"


class FeaturesResponse(BaseModel):
    """Frontend-facing feature availability flags."""

    agents_api: AgentsApiFeature
    browser_control: BrowserControlFeature
    ppt_confirmation: PptConfirmationFeature


def _ppt_confirmation_available(config: AppConfig) -> bool:
    """Configured transport availability; never grant automatic user consent."""
    if os.environ.get("PPT_CONFIRMATION_TRANSPORT_ENABLED") != "true":
        return False
    if len(os.environ.get("PPT_MASTER_RESPONSES_RELAY_KEY", "")) < 16:
        return False
    service_key = os.environ.get("SANDBOX_API_KEY", "")
    config_key = getattr(getattr(config, "sandbox", None), "sandbox_api_key", None)
    if not service_key or not isinstance(config_key, str):
        return False
    try:
        if not hmac.compare_digest(service_key.encode("utf-8"), config_key.encode("utf-8")):
            return False
        return get_session_factory() is not None
    except (UnicodeError, RuntimeError):
        return False


@router.get(
    "/features",
    response_model=FeaturesResponse,
    summary="List Feature Flags",
    description="Report which optional config-gated features are enabled, so the frontend can gate UI before issuing requests.",
)
async def list_features(config: AppConfig = Depends(get_config)) -> FeaturesResponse:
    """Return availability of optional, config-gated frontend features."""
    browser = browser_capability(config)
    return FeaturesResponse(
        agents_api=AgentsApiFeature(enabled=config.agents_api.enabled),
        browser_control=BrowserControlFeature(enabled=browser.available),
        ppt_confirmation=PptConfirmationFeature(enabled=_ppt_confirmation_available(config)),
    )
