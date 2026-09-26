"""Shared runtime protocol constants."""

ENTERPRISE_PRODUCT_NAME = "中国海油通用智能体"
ENTERPRISE_PRODUCT_DESCRIPTION = "面向油气勘探开发业务的企业级多智能体工作平台"

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"

# Hidden transient frames used by the live browser panel. They are progress
# state, not deliverable workspace artifacts.
BROWSER_FRAMES_DIRNAME = ".browser-frames"

# Process feedback is not a deliverable. Shared by tool config and snapshots.
TOOL_RESULTS_DIRNAME = ".tool-results"
MCP_INTERNAL_DIRNAME = ".mcp"
WORKSPACE_CHANGES_EVENT_TYPE = "workspace_changes"
WORKSPACE_CHANGES_EVENT_CATEGORY = "workspace"

# Shared with ORM column widths without importing the runtime from storage.
RUN_EVENT_TYPE_MAX_LENGTH = 32
RUN_EVENT_CATEGORY_MAX_LENGTH = 16
