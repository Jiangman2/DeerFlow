"""Versioned creator knowledge selection; never a delegation or credential."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ResourceId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]


class AgentKnowledgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["kb", "file"]
    id: ResourceId
    kb_id: ResourceId | None = None
    tenant_id: ResourceId | None = None
    name: str = Field(default="", max_length=512)
    kb_name: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def consistent_parent(self) -> "AgentKnowledgeItem":
        if self.type == "file" and not self.kb_id:
            raise ValueError("A document selection requires its knowledge base")
        if self.type == "kb" and self.kb_id not in (None, self.id):
            raise ValueError("Knowledge base identity must match its parent")
        return self


class AgentKnowledgeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: Literal["inherit", "disabled", "selected"] = "inherit"
    items: list[AgentKnowledgeItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def explicit_selection(self) -> "AgentKnowledgeScope":
        if bool(self.items) != (self.mode == "selected"):
            raise ValueError("Only selected mode requires a non-empty resource list")
        identities = [(item.type, item.kb_id, item.id) for item in self.items]
        if len(set(identities)) != len(identities):
            raise ValueError("Duplicate knowledge selections are not allowed")
        return self

    def to_run_context(self) -> dict[str, Any]:
        bases = [item for item in self.items if item.type == "kb"]
        return {
            "weknora_knowledge_base_ids": [item.id for item in bases],
            "weknora_knowledge_bases": [{"id": item.id, "name": item.name} for item in bases],
            "weknora_knowledge_ids": [item.id for item in self.items if item.type == "file"],
            "weknora_tag_scopes": [],
            "weknora_scope_items": [item.model_dump(exclude_none=True) for item in self.items],
        }


def clear_weknora_context(config: dict[str, Any]) -> None:
    for section in ("context", "configurable"):
        values = config.setdefault(section, {})
        for key in list(values):
            if key.startswith("weknora_"):
                values.pop(key)
        values.update(AgentKnowledgeScope(mode="disabled").to_run_context())


def enforce_agent_knowledge_context(scope: AgentKnowledgeScope | None, config: dict[str, Any], *, source: str | None, personal: bool, publication=None) -> None:
    """Guard factory/direct invocations and admission-to-worker config drift.

    This is a scope check, not an auth credential. WeKnora still authorizes the
    current caller on each tool request; shared invocation needs its own grant.
    """
    if scope is None or scope.mode == "inherit":
        return
    if scope.mode == "selected":
        if source in ("team", "legacy") or not personal:
            # Only the factory's server-read, grant-checked snapshot is valid;
            # no flag in browser/context/configurable can enable delegation.
            if publication is not None and publication.config.get("resource_policy_version") == 1 and AgentKnowledgeScope.model_validate(publication.config.get("knowledge_scope")) == scope:
                clear_weknora_context(config)
                return
            raise ValueError("Published knowledge requires an owner-approved delegation grant")
        expected = scope.to_run_context()
        for section in ("context", "configurable"):
            current = config.get(section, {})
            for key in ("weknora_knowledge_base_ids", "weknora_knowledge_ids", "weknora_tag_scopes"):
                if current.get(key) != expected[key]:
                    raise ValueError("Employee knowledge scope changed and must be revalidated before execution")
            # Names can legitimately change during the live metadata refresh;
            # resource identities and consumer-tenant routes cannot.
            items = current.get("weknora_scope_items")
            keys = ("type", "id", "kb_id", "tenant_id")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Employee knowledge scope must be revalidated before execution")
            actual = [tuple(item.get(key) for key in keys) for item in items]
            canonical = [tuple(item.get(key) for key in keys) for item in expected["weknora_scope_items"]]
            if actual != canonical:
                raise ValueError("Employee knowledge scope changed and must be revalidated before execution")
        return
    clear_weknora_context(config)
