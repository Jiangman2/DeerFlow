"""SandboxAuditMiddleware - bash command security auditing."""

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command classification rules
# ---------------------------------------------------------------------------

# Each pattern is compiled once at import time.
_HIGH_RISK_PATTERNS: list[re.Pattern[str]] = [
    # --- original rules (retained) ---
    re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(/\*?|~/?\*?|/home\b|/root\b)\s*$"),
    # Raw-device writes are destructive; ordinary dd copies inside user-data
    # remain available for document/media processing.
    re.compile(r"\bdd\b[^\n]*\bof=(?:/dev/(?:sd|nvme|vd|xvd|mapper/)|/proc/|/sys/)", re.IGNORECASE),
    re.compile(r"mkfs"),
    re.compile(r"cat\s+/etc/shadow"),
    re.compile(r">+\s*/etc/"),
    # --- pipe to sh/bash (generalised, replaces old curl|sh rule) ---
    re.compile(r"\|\s*(ba)?sh\b"),
    # --- overwrite system binaries ---
    re.compile(r">+\s*(/usr/bin/|/bin/|/sbin/)"),
    # --- overwrite shell startup files ---
    re.compile(r">+\s*~/?\.(bashrc|profile|zshrc|bash_profile)"),
    # --- process environment leakage ---
    re.compile(r"/proc/[^/]+/environ"),
    # --- outbound shell stream (read-only /dev/tcp probes remain allowed) ---
    re.compile(r">+&?\s*/dev/tcp/"),
    # --- fork bomb ---
    re.compile(r"\S+\(\)\s*\{[^}]*\|\s*\S+\s*&"),  # :(){ :|:& };:
    re.compile(r"while\s+true.*&\s*done"),  # while true; do bash & done
]

_MEDIUM_RISK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"chmod\s+777"),
    re.compile(r"pip3?\s+install"),
    re.compile(r"apt(-get)?\s+install"),
    # sudo/su: no-op under Docker root; warn so LLM is aware
    re.compile(r"\b(sudo|su)\b"),
    # PATH modification: long attack chain, warn rather than block
    re.compile(r"\bPATH\s*="),
]

_CLUSTER_CONTROL_COMMANDS = {
    "crictl",
    "ctr",
    "docker",
    "helm",
    "kubectl",
    "nerdctl",
}

_SENSITIVE_PLATFORM_PATH = re.compile(
    r"(?:^|[\s'\"=])(?:"
    r"/proc/[^/]+/environ(?:\b|$)|/etc/(?:environment|machine-id|kubernetes)(?:/|$)|"
    r"/var/run/secrets(?:/|$)|/run/secrets(?:/|$)|/root/\.kube(?:/|$)|"
    r"/app/(?:config\.ya?ml|extensions_config\.json|backend|frontend)(?:/|$)|"
    r"/(?:app|root|etc)/(?:[^\s'\"]*/)?\.env(?:\.[A-Za-z0-9_-]+)?(?:$|[\s'\";])"
    r")",
    re.IGNORECASE,
)

_INTERNAL_SERVICE_TARGET = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://(?:[^/@\s]+@)?|--host(?:=|\s+)|\s-h\s+|\bhost\s*=\s*)(?:gateway|postgres|redis|weknora-app|"
    r"weknora-postgres|kubernetes\.default(?:\.svc)?)(?=[:/\s'\"]|$)",
    re.IGNORECASE,
)

_PYTHON_PLATFORM_INTROSPECTION = re.compile(
    r"(?:subprocess\.(?:run|Popen|check_output).{0,80}(?:env|printenv|kubectl|docker)|"
    r"open\s*\(.{0,80}(?:/proc/|/var/run/secrets|/root/\.kube|/etc/kubernetes))",
    re.IGNORECASE,
)

_SECRET_ENV_NAME = re.compile(
    r"(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|DATABASE_URL|REDIS_URL|DSN|PGPASS|MYSQL_PWD|REDISCLI_AUTH)",
    re.IGNORECASE,
)

_PYTHON_ENV_ACCESS = re.compile(
    r"(?:os\.getenv\s*\(|os\.environ(?:\.get)?\s*[\[(])\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


def _python_environment_read_is_sensitive(command: str) -> bool:
    """Block enumeration and literal reads of secret-looking env names only."""
    if "os.environ" in command and not _PYTHON_ENV_ACCESS.search(command):
        return True
    return any(_SECRET_ENV_NAME.search(match.group(1)) for match in _PYTHON_ENV_ACCESS.finditer(command))


_ENV_ENUMERATION = re.compile(
    r"^\s*(?:printenv|env|set|export\s+-p|declare\s+-x)\s*$",
    re.IGNORECASE,
)


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _effective_executable(tokens: list[str]) -> tuple[str, list[str]]:
    """Return the executable after harmless ``env NAME=value`` prefixes."""
    if not tokens:
        return "", []
    remaining = list(tokens)
    if remaining[0].lower() == "env":
        remaining = remaining[1:]
        while remaining and (remaining[0].startswith("-") or ("=" in remaining[0] and not remaining[0].startswith("="))):
            remaining = remaining[1:]
        if not remaining:
            return "env", []
    while remaining and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", remaining[0], flags=re.DOTALL):
        remaining = remaining[1:]
    if not remaining:
        return "", []
    return remaining[0].rsplit("/", 1)[-1].lower(), remaining[1:]


def _is_platform_introspection_command(command: str) -> bool:
    """Detect commands that turn the sandbox into a platform operations shell."""
    normalized = " ".join(command.split())
    if _ENV_ENUMERATION.fullmatch(normalized):
        return True
    if _SENSITIVE_PLATFORM_PATH.search(command):
        return True
    if _INTERNAL_SERVICE_TARGET.search(command):
        return True
    if _PYTHON_PLATFORM_INTROSPECTION.search(command):
        return True
    if _python_environment_read_is_sensitive(command):
        return True

    tokens = _command_tokens(command)
    executable, arguments = _effective_executable(tokens)
    if executable in _CLUSTER_CONTROL_COMMANDS:
        return True
    if executable in {"bash", "sh", "zsh"} and any(_is_platform_introspection_command(argument) for argument in arguments if isinstance(argument, str)):
        return True
    return False


_FILE_TOOLS = {"glob", "grep", "ls", "read_file", "str_replace", "write_file"}


def _sensitive_tool_reason(tool_name: str, args: object) -> str | None:
    """Return a block reason for non-bash platform introspection tools."""
    if tool_name not in _FILE_TOOLS or not isinstance(args, dict):
        return None
    for field in ("path", "root_path", "directory", "pattern"):
        value = args.get(field)
        if not isinstance(value, str):
            continue
        if tool_name == "ls" and value.strip() in {"/", "/app", "/etc", "/root", "/var/run"}:
            return "platform filesystem inspection is not allowed"
        if _SENSITIVE_PLATFORM_PATH.search(f" {value}"):
            return "platform files and runtime secrets are not accessible"
    return None


def _split_compound_command(command: str) -> list[str]:
    """Split a compound command into sub-commands (quote-aware).

    Scans the raw command string so unquoted shell control operators are
    recognised even when they are not surrounded by whitespace
    (e.g. ``safe;rm -rf /`` or ``rm -rf /&&echo ok``). Operators inside
    quotes are ignored. If the command ends with an unclosed quote or a
    dangling escape, return the whole command unchanged (fail-closed —
    safer to classify the unsplit string than silently drop parts).
    """
    parts: list[str] = []
    current: list[str] = []
    in_single_quote = False
    in_double_quote = False
    escaping = False
    index = 0

    while index < len(command):
        char = command[index]

        if escaping:
            current.append(char)
            escaping = False
            index += 1
            continue

        if char == "\\" and not in_single_quote:
            current.append(char)
            escaping = True
            index += 1
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            current.append(char)
            index += 1
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current.append(char)
            index += 1
            continue

        if not in_single_quote and not in_double_quote:
            if command.startswith("&&", index) or command.startswith("||", index):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 2
                continue
            if char == ";":
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                index += 1
                continue

        current.append(char)
        index += 1

    # Unclosed quote or dangling escape → fail-closed, return whole command
    if in_single_quote or in_double_quote or escaping:
        return [command]

    part = "".join(current).strip()
    if part:
        parts.append(part)
    return parts if parts else [command]


def _classify_single_command(command: str) -> str:
    """Classify a single (non-compound) command. Return 'block', 'warn', or 'pass'."""
    normalized = " ".join(command.split())

    if _is_platform_introspection_command(command):
        return "block"

    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Also try shlex-parsed tokens for high-risk detection
    try:
        tokens = shlex.split(command)
        joined = " ".join(tokens)
        for pattern in _HIGH_RISK_PATTERNS:
            if pattern.search(joined):
                return "block"
    except ValueError:
        # Heredocs and other multiline shell forms may be valid bash but
        # unparseable by shlex. Raw high-risk patterns were already checked.
        pass

    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(normalized):
            return "warn"

    return "pass"


def _classify_command(command: str) -> str:
    """Return 'block', 'warn', or 'pass'.

    Strategy:
    1. First scan the *whole* raw command against high-risk patterns. This
       catches structural attacks like ``while true; do bash & done`` or
       ``:(){ :|:& };:`` that span multiple shell statements — splitting them
       on ``;`` would destroy the pattern context.
    2. Then split compound commands (e.g. ``cmd1 && cmd2 ; cmd3``) and
       classify each sub-command independently. The most severe verdict wins.
    """
    # Pass 1: whole-command high-risk scan (catches multi-statement patterns)
    normalized = " ".join(command.split())
    if _is_platform_introspection_command(command):
        return "block"
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return "block"

    # Pass 2: per-sub-command classification
    sub_commands = _split_compound_command(command)
    worst = "pass"
    for sub in sub_commands:
        verdict = _classify_single_command(sub)
        if verdict == "block":
            return "block"  # short-circuit: can't get worse
        if verdict == "warn":
            worst = "warn"
    return worst


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class SandboxAuditMiddleware(AgentMiddleware[ThreadState]):
    """Bash command security auditing middleware.

    For every ``bash`` tool call:
    1. **Command classification**: regex + shlex analysis grades commands as
       high-risk (block), medium-risk (warn), or safe (pass).
    2. **Audit log**: every bash call is recorded as a structured JSON entry
       via the standard logger (visible in gateway.log).

    High-risk commands (e.g. ``rm -rf /``, ``curl url | bash``) are blocked:
    the handler is not called and an error ``ToolMessage`` is returned so the
    agent loop can continue gracefully.

    Medium-risk commands (e.g. ``pip install``, ``chmod 777``) are executed
    normally; a warning is appended to the tool result so the LLM is aware.
    """

    state_schema = ThreadState

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_thread_id(self, request: ToolCallRequest) -> str | None:
        runtime = request.runtime  # ToolRuntime; may be None-like in tests
        if runtime is None:
            return None
        ctx = getattr(runtime, "context", None) or {}
        thread_id = ctx.get("thread_id") if isinstance(ctx, dict) else None
        if thread_id is None:
            cfg = getattr(runtime, "config", None) or {}
            thread_id = cfg.get("configurable", {}).get("thread_id")
        return thread_id

    _AUDIT_COMMAND_LIMIT = 200

    def _write_audit(self, thread_id: str | None, command: str, verdict: str, *, truncate: bool = False) -> None:
        audited_command = command
        if truncate and len(command) > self._AUDIT_COMMAND_LIMIT:
            audited_command = f"{command[: self._AUDIT_COMMAND_LIMIT]}... ({len(command)} chars)"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id or "unknown",
            "command": audited_command,
            "verdict": verdict,
        }
        logger.info("[SandboxAudit] %s", json.dumps(record, ensure_ascii=False))

    def _build_block_message(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        tool_call_id = str(request.tool_call.get("id") or "missing_id")
        tool_name = str(request.tool_call.get("name") or "tool")
        return ToolMessage(
            content=f"Command blocked: {reason}. Please use a safer alternative approach.",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    def _append_warn_to_result(self, result: ToolMessage | Command, command: str) -> ToolMessage | Command:
        """Append a warning note to the tool result for medium-risk commands."""
        if not isinstance(result, ToolMessage):
            return result
        warning = f"\n\n⚠️ Warning: `{command}` is a medium-risk command that may modify the runtime environment."
        if isinstance(result.content, list):
            new_content = list(result.content) + [{"type": "text", "text": warning}]
        else:
            new_content = str(result.content) + warning
        return ToolMessage(
            content=new_content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            status=result.status,
        )

    # ------------------------------------------------------------------
    # Input sanitisation
    # ------------------------------------------------------------------

    # Presentation/document workflows legitimately use large heredocs. Keep a
    # bounded transport guard, but place it above normal generated source size.
    _MAX_COMMAND_LENGTH = 512 * 1024

    def _validate_input(self, command: str) -> str | None:
        """Return ``None`` if *command* is acceptable, else a rejection reason."""
        if not command.strip():
            return "empty command"
        if len(command) > self._MAX_COMMAND_LENGTH:
            return "command too long"
        if "\x00" in command:
            return "null byte detected"
        return None

    # ------------------------------------------------------------------
    # Core logic (shared between sync and async paths)
    # ------------------------------------------------------------------

    def _pre_process(self, request: ToolCallRequest) -> tuple[str, str | None, str, str | None]:
        """
        Returns (command, thread_id, verdict, reject_reason).
        verdict is 'block', 'warn', or 'pass'.
        reject_reason is non-None only for input sanitisation rejections.
        """
        args = request.tool_call.get("args", {})
        raw_command = args.get("command")
        command = raw_command if isinstance(raw_command, str) else ""
        thread_id = self._get_thread_id(request)

        # ① input sanitisation — reject malformed input before regex analysis
        reject_reason = self._validate_input(command)
        if reject_reason:
            self._write_audit(thread_id, command, "block", truncate=True)
            logger.warning("[SandboxAudit] INVALID INPUT thread=%s reason=%s", thread_id, reject_reason)
            return command, thread_id, "block", reject_reason

        # ② classify command
        verdict = _classify_command(command)

        # ③ audit log
        self._write_audit(thread_id, command, verdict)

        if verdict == "block":
            logger.warning("[SandboxAudit] BLOCKED thread=%s cmd=%r", thread_id, command)
        elif verdict == "warn":
            logger.warning("[SandboxAudit] WARN (medium-risk) thread=%s cmd=%r", thread_id, command)

        return command, thread_id, verdict, None

    # ------------------------------------------------------------------
    # wrap_tool_call hooks
    # ------------------------------------------------------------------

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        tool_name = str(request.tool_call.get("name") or "")
        sensitive_reason = _sensitive_tool_reason(tool_name, request.tool_call.get("args", {}))
        if sensitive_reason:
            self._write_audit(self._get_thread_id(request), f"{tool_name} <redacted arguments>", "block")
            return self._build_block_message(request, sensitive_reason)
        if tool_name != "bash":
            return handler(request)

        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        tool_name = str(request.tool_call.get("name") or "")
        sensitive_reason = _sensitive_tool_reason(tool_name, request.tool_call.get("args", {}))
        if sensitive_reason:
            self._write_audit(self._get_thread_id(request), f"{tool_name} <redacted arguments>", "block")
            return self._build_block_message(request, sensitive_reason)
        if tool_name != "bash":
            return await handler(request)

        command, _, verdict, reject_reason = self._pre_process(request)
        if verdict == "block":
            reason = reject_reason or "security violation detected"
            return self._build_block_message(request, reason)
        result = await handler(request)
        if verdict == "warn":
            result = self._append_warn_to_result(result, command)
        return result
