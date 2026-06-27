from langchain.tools import tool

from deerflow.knowledge import search_knowledge
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime


def _scope_paths_from_runtime(runtime: Runtime) -> list[str]:
    context = getattr(runtime, "context", None)
    if not isinstance(context, dict):
        return []
    raw = context.get("knowledge_scope_paths")
    if not isinstance(raw, list | tuple):
        return []
    return [item for item in raw if isinstance(item, str)]


@tool("search_knowledge", parse_docstring=True)
async def search_knowledge_tool(runtime: Runtime, query: str, limit: int = 5) -> str:
    """Search the current user's private knowledge base.

    Use this when the user asks about their uploaded knowledge base, internal files,
    private references, project notes, or when the automatically provided snippets
    are insufficient.

    Args:
        query: A focused search query for the user's private knowledge base.
        limit: Maximum number of snippets to return, between 1 and 10.
    """
    user_id = resolve_runtime_user_id(runtime)
    scope_paths = _scope_paths_from_runtime(runtime)
    results = await search_knowledge(
        user_id,
        query,
        limit=max(1, min(int(limit or 5), 10)),
        scope_paths=scope_paths,
    )
    if not results:
        if scope_paths:
            return "No matching private knowledge snippets found in the selected knowledge scope."
        return "No matching private knowledge snippets found."

    lines = ["Private knowledge search results:"]
    if scope_paths:
        lines.append(f"Scope: {', '.join(scope_paths[:8])}")
    for index, item in enumerate(results, start=1):
        heading = f" / {item.heading}" if item.heading else ""
        lines.append("")
        lines.append(f"[{index}] {item.filename}{heading} (chunk {item.chunk_index}, score {item.score})")
        lines.append(item.snippet)
    return "\n".join(lines)
