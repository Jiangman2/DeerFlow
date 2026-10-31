"""Middleware for injecting image details into conversation before LLM call."""

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# Mirror the tool-side size cap as a defense-in-depth check. The tool
# enforces this at write time; the middleware re-checks at read time in
# case the file grew on disk between view and injection.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_REQUEST_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_REFERENCE_KEY = "deerflow_view_image_paths"
_IMAGE_HEADERS = ("Here are the images you've viewed:", "Here are the details of the images you've viewed:")


def _image_context_message(message: object) -> bool:
    """Recognize only host-generated hidden image context, never user uploads."""
    if not isinstance(message, HumanMessage) or message.additional_kwargs.get("hide_from_ui") is not True:
        return False
    content = message.content
    if isinstance(content, list):
        content = content[0] if content else ""
        if isinstance(content, dict):
            content = content.get("text", "")
    return isinstance(content, str) and content.startswith(_IMAGE_HEADERS)


def _has_image_header(message: HumanMessage) -> bool:
    # Do not stringify a message containing megabytes of base64 for deduplication.
    content = message.content
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].startswith(_IMAGE_HEADERS) for b in content)
    return isinstance(content, str) and content.startswith(_IMAGE_HEADERS)


class ViewImageMiddlewareState(ThreadState):
    """Reuse the thread state so reducer-backed keys keep their annotations."""


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """Injects image details as a human message before LLM calls when view_image tools have completed.

    This middleware:
    1. Runs before each LLM call
    2. Checks if the last assistant message contains view_image tool calls
    3. Verifies all tool calls in that message have been completed (have corresponding ToolMessages)
    4. Persists only a hidden text receipt and the current tool call's virtual paths
    5. Reads image bytes into a model-request copy, never checkpoint messages

    This enables the LLM to automatically receive and analyze images that were loaded via view_image tool,
    without requiring explicit user prompts to describe the images.
    """

    state_schema = ViewImageMiddlewareState

    def _get_last_assistant_message(self, messages: list) -> AIMessage | None:
        """Get the last assistant message from the message list.

        Args:
            messages: List of messages

        Returns:
            Last AIMessage or None if not found
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _has_view_image_tool(self, message: AIMessage) -> bool:
        """Check if the assistant message contains view_image tool calls.

        Args:
            message: Assistant message to check

        Returns:
            True if message contains view_image tool calls
        """
        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return False

        return any(tool_call.get("name") == "view_image" for tool_call in message.tool_calls)

    def _all_tools_completed(self, messages: list, assistant_msg: AIMessage) -> bool:
        """Check if all tool calls in the assistant message have been completed.

        Args:
            messages: List of all messages
            assistant_msg: The assistant message containing tool calls

        Returns:
            True if all tool calls have corresponding ToolMessages
        """
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # Get all tool call IDs from the assistant message
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # Find the index of the assistant message
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        # Get all ToolMessages after the assistant message
        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # Check if all tool calls have been completed
        return tool_call_ids.issubset(completed_tool_ids)

    @staticmethod
    def _read_image_as_data_url(actual_path: str, mime_type: str, expected_size: int) -> str | None:
        """Read image file and return a `data:` URL, or None on failure.

        Trust assumption: ``actual_path`` is set by ``view_image_tool``
        (server-side, validated against the allowed virtual roots at write
        time) and held in LangGraph-controlled state. Client input cannot
        reach this field, so the read scope is trusted. We still re-check
        size at read time to defend against TOCTOU growth and skip files
        exceeding ``_MAX_IMAGE_BYTES``.
        """
        try:
            file_path = Path(actual_path)
            if not file_path.exists() or not file_path.is_file():
                return None
            current_size = file_path.stat().st_size
            if current_size != expected_size:
                # File changed between view and inject - skip.
                return None
            if not isinstance(expected_size, int) or expected_size < 0 or current_size > _MAX_IMAGE_BYTES:
                return None
            with open(file_path, "rb") as f:
                image_bytes = f.read(expected_size + 1)
            if len(image_bytes) != expected_size:
                return None
            base64_data = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:{mime_type};base64,{base64_data}"
        except OSError:
            return None

    def _create_image_details_message(self, state: ViewImageMiddlewareState) -> list[str | dict]:
        """Create a formatted message with all viewed image details.

        Reads image files from disk on-demand and encodes them as base64
        for the model. The base64 data is NOT persisted in state -- only
        lightweight metadata (path, mime_type, size) is stored in
        ``viewed_images``, avoiding large duplicate payloads across every
        checkpoint (see #4138).

        Args:
            state: Current state containing viewed_images

        Returns:
            List of content blocks (text and images) for the HumanMessage
        """
        viewed_images = state.get("viewed_images", {})
        if not viewed_images:
            # Return a properly formatted text block, not a plain string array
            return [{"type": "text", "text": "No images have been viewed."}]

        # Build the message with image information
        content_blocks: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]

        remaining_bytes = _MAX_REQUEST_IMAGE_BYTES
        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            actual_path = image_data.get("actual_path", "")
            expected_size = image_data.get("size", 0)

            # Add text description
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            if not isinstance(expected_size, int) or expected_size < 0 or expected_size > remaining_bytes:
                content_blocks.append({"type": "text", "text": "  (image omitted: this request's image byte limit was reached; view fewer images at a time)"})
                continue

            # Read the image file on-demand and encode as base64 for the model
            if actual_path:
                data_url = self._read_image_as_data_url(actual_path, mime_type, expected_size)
                if data_url:
                    remaining_bytes -= expected_size
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    )
                else:
                    content_blocks.append({"type": "text", "text": "  (file unavailable or changed on disk)"})

        return content_blocks

    def _should_inject_image_message(self, state: ViewImageMiddlewareState) -> bool:
        """Determine if we should inject an image details message.

        Args:
            state: Current state

        Returns:
            True if we should inject the message
        """
        messages = state.get("messages", [])
        if not messages:
            return False

        # Get the last assistant message
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        # Check if it has view_image tool calls
        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # Check if all tools have been completed
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # Check if we've already added an image details message
        # Look for a human message after the last assistant message that contains image details
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                if not msg.additional_kwargs.get("hide_from_ui") or _has_image_header(msg):
                    # Already added, don't add again
                    return False

        return True

    def _inject_image_message(self, state: ViewImageMiddlewareState) -> dict | None:
        """Internal helper to inject image details message.

        Args:
            state: Current state

        Returns:
            State update with additional human message, or None if no update needed
        """
        if not self._should_inject_image_message(state):
            return None

        paths = self._current_image_paths(state)
        # The reference must not contain local filesystem paths or image bytes.
        image_content = [{"type": "text", "text": _IMAGE_HEADERS[0]}]
        image_content.extend({"type": "text", "text": f"\n- **{path}**"} for path in paths)

        # Persist only a lightweight reference. Inline bytes are added to a
        # transient request copy by wrap_model_call / awrap_model_call.
        human_msg = HumanMessage(content=image_content, additional_kwargs={"hide_from_ui": True, _IMAGE_REFERENCE_KEY: paths})

        logger.debug("Recording current image references before LLM call")

        # Return state update with the new message
        return {"messages": [human_msg]}

    @override
    def before_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject image details message before LLM call if view_image tools have completed (sync version).

        This runs before each LLM call, checking if the previous turn included view_image
        tool calls that have all completed. If so, it injects a human message with the image
        details so the LLM can see and analyze the images.

        Args:
            state: Current state
            runtime: Runtime context (unused but required by interface)

        Returns:
            State update with additional human message, or None if no update needed
        """
        return self._inject_image_message(state)

    @override
    async def abefore_model(self, state: ViewImageMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject image details message before LLM call if view_image tools have completed (async version).

        This runs before each LLM call, checking if the previous turn included view_image
        tool calls that have all completed. If so, it injects a human message with the image
        details so the LLM can see and analyze the images.

        Args:
            state: Current state
            runtime: Runtime context (unused but required by interface)

        Returns:
            State update with additional human message, or None if no update needed
        """
        return self._inject_image_message(state)

    def _current_image_paths(self, state: ViewImageMiddlewareState) -> list[str]:
        messages = state.get("messages", [])
        assistant = self._get_last_assistant_message(messages)
        if assistant is None or not self._has_view_image_tool(assistant) or not self._all_tools_completed(messages, assistant):
            return []
        tail = messages[messages.index(assistant) + 1 :]
        if any(isinstance(m, HumanMessage) and not m.additional_kwargs.get("hide_from_ui") for m in tail):
            return []
        results = {m.tool_call_id: m for m in tail if isinstance(m, ToolMessage)}
        viewed = state.get("viewed_images", {})
        paths = []
        for call in assistant.tool_calls:
            result = results.get(call.get("id"))
            # Failed repeat views must not reuse metadata from an earlier view
            # of the same path. The tool reports its failures as Error: text.
            if result is None or result.status == "error" or (isinstance(result.content, str) and result.content.startswith("Error")):
                continue
            args = call.get("args")
            path = args.get("image_path") if isinstance(args, dict) else None
            if call.get("name") == "view_image" and isinstance(path, str) and path in viewed and path not in paths:
                paths.append(path)
        return paths

    def _model_messages(self, request: ModelRequest) -> list:
        """Expand the current receipt only; strip legacy hidden binaries in a copy.

        Historical checkpoints, actual user images, tool messages and text stay
        untouched. Old view_image receipts keep their descriptions; the model
        can call view_image again when it needs those files.
        """
        messages = list(request.messages)
        last_assistant = max((i for i, m in enumerate(messages) if isinstance(m, AIMessage)), default=-1)
        current_paths = self._current_image_paths(request.state)
        expanded = False
        for i in range(len(messages) - 1, -1, -1):
            message = messages[i]
            if not _image_context_message(message):
                continue
            content = message.content
            if isinstance(content, list):
                # Only replace host-injected inline images, not arbitrary user media.
                filtered = [
                    b for b in content if not (isinstance(b, dict) and b.get("type") == "image_url" and isinstance(b.get("image_url"), dict) and isinstance(b["image_url"].get("url"), str) and b["image_url"]["url"].startswith("data:image/"))
                ]
                if len(filtered) != len(content):
                    filtered.append({"type": "text", "text": "(Earlier image bytes are omitted from this request. Use view_image on the recorded path if needed.)"})
                    messages[i] = message.model_copy(update={"content": filtered})
            later_user = any(isinstance(m, HumanMessage) and not m.additional_kwargs.get("hide_from_ui") for m in messages[i + 1 :])
            if expanded or i <= last_assistant or later_user or not current_paths:
                continue
            refs = message.additional_kwargs.get(_IMAGE_REFERENCE_KEY)
            # Legacy receipts have no paths field; scope them to the current real
            # view_image call and trusted tool-owned viewed_images metadata too.
            paths = current_paths if refs is None else [p for p in current_paths if isinstance(refs, list) and p in refs]
            if not paths:
                continue
            image_content = self._create_image_details_message({"viewed_images": {p: request.state["viewed_images"][p] for p in paths}})
            messages[i] = message.model_copy(update={"content": image_content})
            expanded = True
        return messages

    @override
    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelCallResult:
        return handler(request.override(messages=self._model_messages(request)))

    @override
    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        # Image file I/O and base64 encoding stay off the async event loop.
        messages = await asyncio.to_thread(self._model_messages, request)
        return await handler(request.override(messages=messages))
