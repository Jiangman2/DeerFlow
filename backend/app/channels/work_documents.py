"""Compatibility shim; implementation lives in app.integrations.work."""

from app.integrations.work.agent import summarize_document
from app.integrations.work.client import RELAY_PATH, TOKEN_RE, UAT_APP_ID, WorkAPI, WorkError, document_links
from app.integrations.work.service import WorkDocumentService, build_work_document_service

__all__ = ["RELAY_PATH", "TOKEN_RE", "UAT_APP_ID", "WorkAPI", "WorkDocumentService", "WorkError", "build_work_document_service", "document_links", "summarize_document"]
