"""Markdown transport eligibility only; report content is owned by the agent."""


def validate_markdown_report(raw, submitted_names):
    """Keep the existing caller signature without grading the review content.

    The service owns run identity, artifact ownership and snapshot integrity.
    This function only ensures the bytes can be carried by the Markdown SSE
    transport. It must not infer a compliance conclusion or rewrite the report.
    """
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise ValueError("Invalid Markdown report size")
    content = raw.decode("utf-8-sig", errors="strict")
    if "\x00" in content or not content.strip():
        raise ValueError("Invalid Markdown text")
    return {"scope": "MARKDOWN_TRANSPORT_ONLY", "content_validation": False}
