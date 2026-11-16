"""Prepare an offline candidate from immutable known local source snapshots."""

import difflib
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
TOOLS = Path("backend/packages/harness/deerflow/sandbox/tools.py")
HELPER = Path("backend/packages/harness/deerflow/utils/conversation_url.py")
TOOLS_SOURCE = REPO / ".codex-post44-sync-20261113" / TOOLS
HELPER_SOURCE = ROOT.parent / "gateway-link-r3" / HELPER
EXPECTED_HELPER_SHA256 = "f5ac36f16038a3532cd3110d134b72e58c42693c1b9ab9a1b6390d9b4209c0b8"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("PARENT_SOURCE_ANCHOR_MISMATCH")
    return text.replace(before, after, 1)


def main():
    original = TOOLS_SOURCE.read_bytes()
    helper = HELPER_SOURCE.read_bytes()
    if sha(helper) != EXPECTED_HELPER_SHA256:
        raise ValueError("GATEWAY_416_HELPER_SOURCE_MISMATCH")
    text = original.decode("utf-8")
    text = replace_once(text, "from langchain.tools import tool\n", "from langchain.tools import tool\nfrom langgraph.config import get_config\n")
    text = replace_once(text, "from deerflow.tools.types import Runtime\n", "from deerflow.tools.types import Runtime\nfrom deerflow.utils.conversation_url import current_conversation_url\n")
    addition = '''CONVERSATION_URL_ENV = "DEERFLOW_CONVERSATION_URL"


def _conversation_context_prefix(runtime: Runtime) -> str:
    """Expose the host's current conversation on each POSIX command invocation.

    Resolve the thread with the same precedence as ``present_files``. The URL
    comes only from that identity and the operator-configured web origin; a
    caller-provided URL is never used. An unavailable identity/origin clears a
    stale value in the persistent shell. This non-secret context deliberately
    does not use ``execute_command(env=...)`` or select its alternate AIO API.
    The URL identifies the conversation, not successful artifact publication.
    """
    try:
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if not thread_id:
            runtime_config = getattr(runtime, "config", None) or {}
            thread_id = runtime_config.get("configurable", {}).get("thread_id")
        if not thread_id:
            try:
                thread_id = get_config().get("configurable", {}).get("thread_id")
            except RuntimeError:
                thread_id = None
        url = current_conversation_url(thread_id)
    except Exception:
        # Optional context must not break execution or disclose configuration.
        url = ""
    if url:
        return f"export {CONVERSATION_URL_ENV}={shlex.quote(url)}; "
    return f"unset {CONVERSATION_URL_ENV}; "


'''
    text = replace_once(text, "def _github_env_from_runtime(runtime: Runtime) -> dict[str, str] | None:\n", addition + "def _github_env_from_runtime(runtime: Runtime) -> dict[str, str] | None:\n")
    text = replace_once(text, "        identity_prefix = _channel_identity_prefix(runtime)\n", "        identity_prefix = (_channel_identity_prefix(runtime) or \"\") + _conversation_context_prefix(runtime)\n")
    candidate = text.encode("utf-8")
    for destination, raw in ((ROOT / "parent" / TOOLS, original), (ROOT / TOOLS, candidate), (ROOT / HELPER, helper)):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    (ROOT / "tools.patch").write_text("".join(difflib.unified_diff(original.decode("utf-8").splitlines(True), text.splitlines(True), fromfile="a/" + TOOLS.as_posix(), tofile="b/" + TOOLS.as_posix())), encoding="utf-8", newline="\n")
    manifest = {
        "schema": "task-conversation-context-candidate.v1",
        "status": "OFFLINE_CANDIDATE_NOT_DEPLOYED",
        "runtime_parent_source_verified": False,
        "production_requests": 0,
        "source_basis": "POST44_KNOWN_LOCAL_TOOLS_AND_GATEWAY416_LOCKED_CONVERSATION_HELPER",
        "files": [
            {"path": TOOLS.as_posix(), "parent_path": str(TOOLS_SOURCE.relative_to(REPO)).replace("\\", "/"), "parent_bytes": len(original), "parent_sha256": sha(original), "candidate_bytes": len(candidate), "candidate_sha256": sha(candidate), "production_parent_sha256_pending": True},
            {"path": HELPER.as_posix(), "source_path": str(HELPER_SOURCE.relative_to(REPO)).replace("\\", "/"), "bytes": len(helper), "sha256": sha(helper), "gateway416_locked_source": True, "modified": False},
        ],
    }
    (ROOT / "SOURCE.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": manifest["status"], "parent_tools_sha256": sha(original), "candidate_tools_sha256": sha(candidate)}, sort_keys=True))


if __name__ == "__main__":
    main()
