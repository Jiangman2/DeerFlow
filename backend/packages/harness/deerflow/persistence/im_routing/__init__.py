"""IM session routing persistence (pointers, message links, event inbox)."""

from deerflow.persistence.im_routing.bindings import ImUserBindingRepository, ImUserBindingRow
from deerflow.persistence.im_routing.catalog import GroupMeta, ImGroupCatalogRepository, ImGroupCatalogRow
from deerflow.persistence.im_routing.model import ImEventInboxRow, ImMessageLinkRow, ImSessionPointerRow
from deerflow.persistence.im_routing.registry import (
    GroupAggregate,
    ImThreadRegistryRepository,
    RegistryThread,
)
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.im_routing.sql import ImRoutingRepository, MessageLink, OwnerPointer, SessionPointer

__all__ = [
    "GroupAggregate",
    "GroupMeta",
    "ImEventInboxRow",
    "ImGroupCatalogRepository",
    "ImGroupCatalogRow",
    "ImMessageLinkRow",
    "ImRoutingRepository",
    "ImSessionPointerRow",
    "ImThreadRegistryRepository",
    "ImThreadRegistryRow",
    "ImUserBindingRepository",
    "ImUserBindingRow",
    "MessageLink",
    "OwnerPointer",
    "RegistryThread",
    "SessionPointer",
]