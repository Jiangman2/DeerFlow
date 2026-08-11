import logging
import shlex
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.tools.builtins.presentation_acceptance import validate_ppt_master_delivery
from deerflow.tools.types import Runtime

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"
logger = logging.getLogger(__name__)


def _get_thread_id(runtime: Runtime) -> str | None:
    """Resolve the current thread id from runtime context or RunnableConfig."""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id

    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None


def _get_active_sandbox(runtime: Runtime):
    state = getattr(runtime, "state", None)
    if not isinstance(state, dict):
        return None

    sandbox_state = state.get("sandbox")
    if not isinstance(sandbox_state, dict):
        return None

    sandbox_id = sandbox_state.get("sandbox_id")
    if not sandbox_id:
        return None

    try:
        return get_sandbox_provider().get(sandbox_id)
    except Exception:
        logger.exception("Failed to resolve sandbox %s while presenting file", sandbox_id)
        return None


def _download_virtual_file_to_host(
    runtime: Runtime,
    virtual_path: str,
    actual_path: Path,
    *,
    log_errors: bool = True,
) -> bool:
    stripped = virtual_path.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if not (stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/")):
        return False

    sandbox = _get_active_sandbox(runtime)
    if sandbox is None:
        return False

    try:
        data = sandbox.download_file(virtual_path)
        actual_path.parent.mkdir(parents=True, exist_ok=True)
        actual_path.write_bytes(data)
        return True
    except Exception:
        if log_errors:
            logger.exception("Failed to download presented file from sandbox: %s", virtual_path)
        else:
            logger.debug(
                "Optional presented file is not available in sandbox: %s",
                virtual_path,
                exc_info=True,
            )
        return False


def _preview_pdf_path_for(filepath: str) -> str | None:
    lower = filepath.lower()
    if lower.endswith(".pptx"):
        return filepath[:-5] + ".preview.pdf"
    if lower.endswith(".ppt"):
        return filepath[:-4] + ".preview.pdf"
    return None


def _generate_missing_ppt_preview(
    runtime: Runtime,
    source_ppt_path: str,
    normalized_ppt_path: str,
) -> None:
    """Best-effort generation of a browser preview in the active sandbox.

    ``normalized_ppt_path`` is the persistent Gateway artifact path.  A model
    commonly presents a deck directly from ``workspace/projects/.../exports``;
    in that case only Gateway has copied the file into the normalized outputs
    bucket.  LibreOffice still runs inside the active sandbox, so it must read
    the original sandbox-visible source path rather than the new host-side
    destination.
    """
    preview_path = _preview_pdf_path_for(normalized_ppt_path)
    if preview_path is None:
        return

    thread_id = _get_thread_id(runtime)
    if thread_id:
        try:
            try:
                actual_preview_path = get_paths().resolve_virtual_path(
                    thread_id,
                    preview_path,
                    user_id=get_effective_user_id(),
                )
            except TypeError:
                actual_preview_path = get_paths().resolve_virtual_path(
                    thread_id,
                    preview_path,
                )
            if actual_preview_path.is_file():
                return
        except Exception:
            logger.debug(
                "Could not probe optional PPT preview path %s",
                preview_path,
                exc_info=True,
            )

    sandbox = _get_active_sandbox(runtime)
    if sandbox is None:
        return

    temp_dir = f"/tmp/deerflow-ppt-preview-{uuid.uuid4().hex}"
    virtual_prefix = VIRTUAL_PATH_PREFIX.rstrip("/") + "/"
    sandbox_source_path = source_ppt_path if source_ppt_path.startswith(virtual_prefix) else normalized_ppt_path
    generated_pdf = f"{temp_dir}/{PurePosixPath(sandbox_source_path).stem}.pdf"
    command = "\n".join(
        [
            "set -eu",
            f"preview_tmp={shlex.quote(temp_dir)}",
            'mkdir -p "$preview_tmp"',
            "trap 'rm -rf -- \"$preview_tmp\"' EXIT",
            'office_bin="$(command -v libreoffice || command -v soffice)"',
            f"test -s {shlex.quote(sandbox_source_path)}",
            (f'"$office_bin" --headless --convert-to pdf --outdir "$preview_tmp" {shlex.quote(sandbox_source_path)} >"$preview_tmp/convert.log" 2>&1'),
            f"test -s {shlex.quote(generated_pdf)}",
            f"cp -- {shlex.quote(generated_pdf)} {shlex.quote(preview_path)}",
        ]
    )
    try:
        sandbox.execute_command(command, timeout=240)
    except Exception:
        logger.warning(
            "Failed to generate optional PPT preview for %s",
            normalized_ppt_path,
            exc_info=True,
        )


def _validate_pptx_file(path: Path) -> None:
    """Reject files that only use a .pptx suffix but are not PowerPoint packages."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        raise ValueError(f"Invalid PPTX file: {path.name} is not a ZIP-based PowerPoint file") from None

    required = {"[Content_Types].xml", "ppt/presentation.xml"}
    if not required.issubset(names):
        raise ValueError(f"Invalid PPTX file: {path.name} is missing required PowerPoint package entries")


def _validate_skill_file(path: Path) -> None:
    """Validate a packaged skill before exposing it as a downloadable artifact."""
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = [name.replace("\\", "/") for name in archive.namelist()]
    except zipfile.BadZipFile:
        raise ValueError(f"Invalid skill package: {path.name} is not a ZIP archive") from None

    for name in names:
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts:
            raise ValueError(f"Invalid skill package: {path.name} contains an unsafe path")
    if not any(PurePosixPath(name).name == "SKILL.md" for name in names):
        raise ValueError(f"Invalid skill package: {path.name} is missing SKILL.md")


def _validate_presented_file(actual_path: Path) -> None:
    if actual_path.suffix.lower() == ".pptx":
        _validate_pptx_file(actual_path)
    elif actual_path.suffix.lower() == ".skill":
        _validate_skill_file(actual_path)


def _normalize_presented_filepath(
    runtime: Runtime,
    filepath: str,
    *,
    log_missing_virtual_download: bool = True,
) -> str:
    """Normalize a presented file path to the `/mnt/user-data/outputs/*` contract.

    Accepts either:
    - A virtual sandbox path such as `/mnt/user-data/outputs/report.md`
    - A host-side thread outputs path such as
      `/app/backend/.deer-flow/threads/<thread>/user-data/outputs/report.md`

    Returns:
        The normalized virtual path.

    Raises:
        ValueError: If runtime metadata is missing or the path is outside the
            current thread's outputs directory.
    """
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")

    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    thread_data = runtime.state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    outputs_dir = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    is_virtual_path = stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/")

    if is_virtual_path:
        try:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath, user_id=get_effective_user_id())
        except TypeError:
            actual_path = get_paths().resolve_virtual_path(thread_id, filepath)
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError:
        workspace_path = thread_data.get("workspace_path")
        if not workspace_path:
            raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from None

        workspace_dir = Path(workspace_path).resolve()
        try:
            actual_path.relative_to(workspace_dir)
        except ValueError:
            raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} or the current workspace can be presented: {filepath}") from None

        if not actual_path.is_file() and is_virtual_path:
            _download_virtual_file_to_host(
                runtime,
                filepath,
                actual_path,
                log_errors=log_missing_virtual_download,
            )

        if not actual_path.is_file():
            raise ValueError(f"Only files can be presented: {filepath}")

        target_path = outputs_dir / actual_path.name
        outputs_dir.mkdir(parents=True, exist_ok=True)
        if actual_path.resolve() != target_path.resolve():
            shutil.copy2(actual_path, target_path)
        actual_path = target_path
        relative_path = actual_path.relative_to(outputs_dir)

    if not actual_path.is_file() and is_virtual_path:
        _download_virtual_file_to_host(
            runtime,
            filepath,
            actual_path,
            log_errors=log_missing_virtual_download,
        )

    if not actual_path.is_file():
        raise ValueError(f"Only files can be presented: {filepath}")

    _validate_presented_file(actual_path)

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


def _normalize_presented_filepath_with_preview(runtime: Runtime, filepath: str) -> list[str]:
    normalized_path = _normalize_presented_filepath(runtime, filepath)
    normalized_paths = [normalized_path]
    required_preview_path = _preview_pdf_path_for(normalized_path)

    workflow = runtime.state.get("skill_workflow") if isinstance(runtime.state, dict) else None
    if isinstance(workflow, dict) and workflow.get("skill_name") == "ppt-master" and normalized_path.lower().endswith(".pptx"):
        thread_id = _get_thread_id(runtime)
        sandbox = _get_active_sandbox(runtime)
        if not thread_id or sandbox is None:
            raise ValueError("PPT 工作流验收需要当前线程和活动 Sandbox；无法在缺少运行上下文时发布")
        try:
            actual_pptx = get_paths().resolve_virtual_path(
                thread_id,
                normalized_path,
                user_id=get_effective_user_id(),
            )
        except TypeError:
            actual_pptx = get_paths().resolve_virtual_path(thread_id, normalized_path)
        validate_ppt_master_delivery(
            sandbox=sandbox,
            workflow=workflow,
            source_path=filepath,
            actual_pptx=actual_pptx,
        )

    _generate_missing_ppt_preview(runtime, filepath, normalized_path)
    preview_candidates = [
        candidate
        for candidate in (
            _preview_pdf_path_for(filepath),
            _preview_pdf_path_for(normalized_path),
        )
        if candidate
    ]
    seen_candidates: set[str] = set()
    for preview_path in preview_candidates:
        if preview_path in seen_candidates:
            continue
        seen_candidates.add(preview_path)
        try:
            preview_normalized_path = _normalize_presented_filepath(
                runtime,
                preview_path,
                log_missing_virtual_download=False,
            )
            if preview_normalized_path not in normalized_paths:
                normalized_paths.append(preview_normalized_path)
        except ValueError:
            pass

    if required_preview_path and required_preview_path not in normalized_paths:
        raise ValueError(
            "PowerPoint preview generation failed. Keep the active sandbox available, verify LibreOffice can convert the final deck, and call present_files again; the deck is not published until its paired .preview.pdf is ready."
        )

    return normalized_paths


@tool("present_files", parse_docstring=True)
def present_file_tool(
    runtime: Runtime,
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Make files visible to the user for viewing and rendering in the client interface.

    When to use the present_files tool:

    - Making any file available for the user to view, download, or interact with
    - Presenting multiple related files at once
    - After creating files that should be presented to the user

    When NOT to use the present_files tool:
    - When you only need to read file contents for your own processing
    - For temporary or intermediate files not meant for user viewing

    Notes:
    - You should call this tool after creating files. Files in `/mnt/user-data/outputs` are presented directly; final files in the current `/mnt/user-data/workspace` are copied into the persistent outputs directory first.
    - This tool can be safely called in parallel with other tools. State updates are handled by a reducer to prevent conflicts.

    Args:
        filepaths: List of absolute file paths to present to the user. Files must be in `/mnt/user-data/outputs` or the current `/mnt/user-data/workspace`.
    """
    try:
        normalized_paths: list[str] = []
        seen_paths: set[str] = set()
        for filepath in filepaths:
            for normalized_path in _normalize_presented_filepath_with_preview(runtime, filepath):
                if normalized_path in seen_paths:
                    continue
                normalized_paths.append(normalized_path)
                seen_paths.add(normalized_path)
    except ValueError as exc:
        workflow = runtime.state.get("skill_workflow") if isinstance(runtime.state, dict) else None
        workflow_update = None
        if isinstance(workflow, dict) and workflow.get("activation_id") and workflow.get("completion_contract") == "presentation_delivery":
            action = ", ".join(filepaths)
            workflow_update = {
                "activation_id": workflow["activation_id"],
                "status": "blocked",
                "current_stage": "delivery",
                "gate_status": "failed",
                "gate_detail": str(exc)[:1200],
                "gate_owner_receipt_id": tool_call_id,
                "gate_owner_tool": "present_files",
                "gate_owner_action": action,
                "gate_owner_stage": "delivery",
                "guard_detail": "",
                "resume_pointer": action,
            }
        update = {
            "messages": [
                ToolMessage(
                    f"Error: {exc}",
                    tool_call_id=tool_call_id,
                    name="present_files",
                    status="error",
                )
            ]
        }
        if workflow_update is not None:
            update["skill_workflow"] = workflow_update
        return Command(
            update=update,
        )

    # The merge_artifacts reducer will handle merging and deduplication
    workflow = runtime.state.get("skill_workflow") if isinstance(runtime.state, dict) else None
    workflow_update = None
    if isinstance(workflow, dict) and workflow.get("activation_id") and workflow.get("completion_contract") == "presentation_delivery" and any(path.lower().endswith(".pptx") for path in normalized_paths):
        workflow_update = {
            "activation_id": workflow["activation_id"],
            "status": "completed",
            "current_stage": "delivery-accepted",
            "gate_status": "passed",
            "gate_detail": "",
            "gate_owner_receipt_id": "",
            "gate_owner_tool": "",
            "gate_owner_action": "",
            "gate_owner_stage": "",
            "guard_detail": "",
            "resume_pointer": "",
            "delivered_artifacts": normalized_paths,
        }
    update = {
        "artifacts": normalized_paths,
        "messages": [
            ToolMessage(
                "Successfully presented files",
                tool_call_id=tool_call_id,
                name="present_files",
                artifact={"presented_files": normalized_paths},
            )
        ],
    }
    if workflow_update is not None:
        update["skill_workflow"] = workflow_update
    return Command(
        update=update,
    )
