import type { AIMessage, Message } from "@langchain/langgraph-sdk";

interface GenericMessageGroup<T = string> {
  type: T;
  id: string | undefined;
  messages: Message[];
}

interface HumanMessageGroup extends GenericMessageGroup<"human"> {}

interface AssistantProcessingGroup extends GenericMessageGroup<"assistant:processing"> {}

interface AssistantMessageGroup extends GenericMessageGroup<"assistant"> {}

interface AssistantPresentFilesGroup extends GenericMessageGroup<"assistant:present-files"> {}

interface AssistantClarificationGroup extends GenericMessageGroup<"assistant:clarification"> {}

interface AssistantSubagentGroup extends GenericMessageGroup<"assistant:subagent"> {}

export type MessageGroup =
  | HumanMessageGroup
  | AssistantProcessingGroup
  | AssistantMessageGroup
  | AssistantPresentFilesGroup
  | AssistantClarificationGroup
  | AssistantSubagentGroup;

const HIDDEN_CONTROL_MESSAGE_NAMES = new Set([
  "summary",
  "loop_warning",
  "todo_reminder",
  "todo_completion_reminder",
]);

export function getMessageGroups(messages: Message[]): MessageGroup[] {
  if (messages.length === 0) {
    return [];
  }

  const groups: MessageGroup[] = [];

  // Returns the last group if it can still accept tool messages
  // (i.e. it's an in-flight processing group, not a terminal human/assistant group).
  function lastOpenGroup() {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.type !== "human" &&
      last.type !== "assistant" &&
      last.type !== "assistant:clarification"
    ) {
      return last;
    }
    return null;
  }

  for (const message of messages) {
    if (isHiddenFromUIMessage(message)) {
      continue;
    }

    if (message.type === "human") {
      groups.push({ id: message.id, type: "human", messages: [message] });
      continue;
    }

    if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        const toolCallId = Reflect.get(message, "tool_call_id");
        let streamedGroupIndex = -1;
        if (typeof toolCallId === "string") {
          for (let index = groups.length - 1; index >= 0; index--) {
            const group = groups[index];
            if (
              group?.type === "assistant:clarification" &&
              group.messages.some(
                (candidate) =>
                  candidate.type === "ai" &&
                  candidate.tool_calls?.some(
                    (toolCall) =>
                      toolCall.name === "ask_clarification" &&
                      toolCall.id === toolCallId,
                  ),
              )
            ) {
              streamedGroupIndex = index;
              break;
            }
          }
        }

        if (streamedGroupIndex >= 0) {
          const streamedGroup = groups[streamedGroupIndex];
          if (streamedGroup) {
            groups[streamedGroupIndex] = {
              id: message.id,
              type: "assistant:clarification",
              // Put the authoritative ToolMessage first so card extraction
              // uses its structured artifact, while retaining the AI call for
              // token accounting and diagnostics.
              messages: [message, ...streamedGroup.messages],
            };
          }
          continue;
        }

        // Add to the preceding processing group to preserve tool-call association,
        // then also open a standalone clarification group for prominent display.
        lastOpenGroup()?.messages.push(message);
        groups.push({
          id: message.id,
          type: "assistant:clarification",
          messages: [message],
        });
      } else {
        const open = lastOpenGroup();
        if (open) {
          open.messages.push(message);
        } else {
          // Fallback for orphan tool messages — LangGraph `messages-tuple` can
          // emit tool-result events out of order or replay them from subagent
          // state (e.g. bash subagent under LocalSandboxProvider with
          // allow_host_bash). When that happens, the tool message arrives after
          // a terminal group and lastOpenGroup() returns null. Previously we
          // dropped the message with console.error, silently hiding the tool
          // result from the UI. Attach to the most recent group instead so the
          // user can still see what the agent did.
          const lastGroup = groups[groups.length - 1];
          if (lastGroup) {
            lastGroup.messages.push(message);
          } else {
            // groups is empty (shouldn't happen — the outer for loop is guarded
            // by `messages.length === 0 -> return []`), but keep the diagnostic
            // just in case.
            groups.push({
              id: message.id,
              type: "assistant:processing",
              messages: [message],
            });
          }
        }
      }
      continue;
    }

    if (message.type === "ai") {
      // A message with answer content and no tool calls becomes its own
      // assistant bubble below, which already renders the message's
      // reasoning_content inside the bubble's <Reasoning> collapsible. Such a
      // message must NOT also feed the processing group, or the ChainOfThought
      // panel above the bubble paints the identical reasoning a second time
      // (#3868). Intermediate reasoning (no content) and tool-calling steps
      // still belong in the processing group.
      const becomesAssistantBubble =
        hasContent(message) && !hasToolCalls(message);

      const hasClarificationCall = message.tool_calls?.some(
        (toolCall) => toolCall.name === "ask_clarification",
      );

      if (hasClarificationCall) {
        groups.push({
          id: message.id,
          type: "assistant:clarification",
          messages: [message],
        });
      } else if (hasPresentFiles(message)) {
        groups.push({
          id: message.id,
          type: "assistant:present-files",
          messages: [message],
        });
      } else if (hasSubagent(message)) {
        groups.push({
          id: message.id,
          type: "assistant:subagent",
          messages: [message],
        });
      } else if (
        !becomesAssistantBubble &&
        (hasReasoning(message) || hasToolCalls(message))
      ) {
        const lastGroup = groups[groups.length - 1];
        // Accumulate consecutive intermediate AI messages into one processing group.
        if (lastGroup?.type !== "assistant:processing") {
          groups.push({
            id: message.id,
            type: "assistant:processing",
            messages: [message],
          });
        } else {
          lastGroup.messages.push(message);
        }
      }

      if (becomesAssistantBubble) {
        groups.push({ id: message.id, type: "assistant", messages: [message] });
      }
    }
  }

  return groups;
}

export function getBranchableAssistantGroupIds(
  groups: MessageGroup[],
  isCurrentTurnLoading: boolean,
): Set<string> {
  // Hidden messages were already removed by getMessageGroups, matching the
  // backend's branch checkpoint visibility rules. Within each visible human
  // turn, branching is exposed only when the final AI-bearing group is a
  // terminal assistant text group. Processing, present-files, and subagent
  // groups do not render assistant actions.
  const branchableGroupIds = new Set<string>();
  let lastAIGroup: MessageGroup | null = null;

  const completeTurn = () => {
    if (lastAIGroup?.type === "assistant" && lastAIGroup.id) {
      branchableGroupIds.add(lastAIGroup.id);
    }
    lastAIGroup = null;
  };

  for (const group of groups) {
    if (group.type === "human") {
      completeTurn();
      continue;
    }

    if (group.messages.some((message) => message.type === "ai")) {
      lastAIGroup = group;
    }
  }

  if (!isCurrentTurnLoading) {
    completeTurn();
  }

  return branchableGroupIds;
}

export function groupMessages<T>(
  messages: Message[],
  mapper: (group: MessageGroup) => T,
): T[] {
  return getMessageGroups(messages)
    .map(mapper)
    .filter((result) => result !== undefined && result !== null) as T[];
}

export function getAssistantTurnUsageMessages(groups: MessageGroup[]) {
  const usageMessagesByGroupIndex: Array<Message[] | null> = Array.from(
    { length: groups.length },
    () => null,
  );

  let turnStartIndex: number | null = null;

  for (const [index, group] of groups.entries()) {
    if (group.type === "human") {
      turnStartIndex = null;
      continue;
    }

    turnStartIndex ??= index;

    const nextGroup = groups[index + 1];
    const isTurnEnd = !nextGroup || nextGroup.type === "human";

    if (!isTurnEnd) {
      continue;
    }

    usageMessagesByGroupIndex[index] = groups
      .slice(turnStartIndex, index + 1)
      .flatMap((currentGroup) => currentGroup.messages)
      .filter((message) => message.type === "ai");

    turnStartIndex = null;
  }

  return usageMessagesByGroupIndex;
}

type MessageMetadataLookup = (
  message: Message,
  index: number,
) => { streamMetadata?: Record<string, unknown> } | undefined;

export type StreamingMessageLookup = {
  ids: ReadonlySet<string>;
  messages: ReadonlySet<Message>;
};

export function getStreamingMessageLookup(
  messages: Message[],
  isStreaming: boolean,
  getMessagesMetadata?: MessageMetadataLookup,
): StreamingMessageLookup {
  const streamingMessageIds = new Set<string>();
  const streamingMessages = new Set<Message>();

  if (!isStreaming) {
    return {
      ids: streamingMessageIds,
      messages: streamingMessages,
    };
  }

  messages.forEach((message, index) => {
    if (!getMessagesMetadata?.(message, index)?.streamMetadata) {
      return;
    }

    if (typeof message.id === "string" && message.id.length > 0) {
      streamingMessageIds.add(message.id);
    }
    streamingMessages.add(message);
  });

  return {
    ids: streamingMessageIds,
    messages: streamingMessages,
  };
}

export function isAssistantMessageGroupStreaming(
  groupMessages: Message[],
  streamingMessages: StreamingMessageLookup,
) {
  return groupMessages.some((message) => {
    if (message.type !== "ai") {
      return false;
    }

    return (
      (typeof message.id === "string" &&
        message.id.length > 0 &&
        streamingMessages.ids.has(message.id)) ||
      streamingMessages.messages.has(message)
    );
  });
}

export function getAssistantTurnCopyData(
  messages: Message[],
  { isStreaming = false }: { isStreaming?: boolean } = {},
) {
  if (isStreaming) {
    return null;
  }

  return (
    [...messages]
      .reverse()
      .filter((message) => message.type === "ai")
      .map((message) => {
        const content = extractContentFromMessage(message);
        return content ?? extractReasoningContentFromMessage(message) ?? "";
      })
      .find((content) => content.length > 0) ?? null
  );
}

export function getMessageCopyData(message: Message) {
  const content = extractContentFromMessage(message);
  if (message.type === "human") {
    return stripUploadedFilesTag(content);
  }
  if (content.length > 0) {
    return content;
  }
  return extractReasoningContentFromMessage(message) ?? "";
}

export function extractTextFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) =>
        typeof content === "string"
          ? content
          : content.type === "text"
            ? content.text
            : "",
      )
      .join("\n")
      .trim();
  }
  return "";
}

const THINK_OPEN_TAG = "<think>";
const THINK_TAG_RE = /<think>\s*([\s\S]*?)\s*<\/think>/g;

interface InlineReasoningSplit {
  content: string;
  reasoning: string | null;
}

function splitInlineReasoning(content: string): InlineReasoningSplit {
  const reasoningParts: string[] = [];

  // First pass: strip every fully closed `<think>...</think>` pair and
  // collect its body as reasoning.
  let cleaned = content.replace(THINK_TAG_RE, (_, reasoning: string) => {
    const normalized = reasoning.trim();
    if (normalized) {
      reasoningParts.push(normalized);
    }
    return "";
  });

  // Streaming-safe pass: a `<think>` opener whose `</think>` has not arrived
  // yet means the rest of the chunk is reasoning in flight. Route it into the
  // reasoning slot instead of letting it render as message content (the
  // raw-HTML markdown pipeline would otherwise paint the inner text on
  // screen until the closing tag lands).
  //
  // Skip when the opener sits right after a backtick — that is the model
  // talking about `<think>` literally inside markdown inline code, not
  // actually streaming reasoning.
  const openTagIndex = cleaned.indexOf(THINK_OPEN_TAG);
  if (openTagIndex !== -1 && cleaned[openTagIndex - 1] !== "`") {
    const tail = cleaned.slice(openTagIndex + THINK_OPEN_TAG.length).trim();
    if (tail) {
      reasoningParts.push(tail);
    }
    cleaned = cleaned.slice(0, openTagIndex);
  }

  return {
    content: cleaned.trim(),
    reasoning: reasoningParts.length > 0 ? reasoningParts.join("\n\n") : null,
  };
}

// The split is re-derived on every render: `hasContent`, `hasReasoning`,
// `extractContentFromMessage` and `extractReasoningContentFromMessage` all run
// over the whole message list on each stream chunk, so an unmemoized scan costs
// O(total content) per chunk — quadratic across a long run. Cache per message
// object, keyed by the exact content string it was derived from so a message
// whose `content` is reassigned recomputes instead of serving a stale split.
const inlineReasoningCache = new WeakMap<
  object,
  { content: string; split: InlineReasoningSplit }
>();

function splitInlineReasoningFromAIMessage(message: Message) {
  if (message.type !== "ai" || typeof message.content !== "string") {
    return null;
  }
  const content = message.content;
  const cached = inlineReasoningCache.get(message);
  if (cached?.content === content) {
    return cached.split;
  }
  const split = splitInlineReasoning(content);
  inlineReasoningCache.set(message, { content, split });
  return split;
}

export function extractContentFromMessage(message: Message) {
  if (typeof message.content === "string") {
    return (
      splitInlineReasoningFromAIMessage(message)?.content ??
      message.content.trim()
    );
  }
  if (Array.isArray(message.content)) {
    return message.content
      .map((content) => {
        if (typeof content === "string") {
          return content;
        }
        switch (content.type) {
          case "text":
            return content.text;
          case "image_url":
            const imageURL = extractURLFromImageURLContent(content.image_url);
            return `![image](${imageURL})`;
          default:
            return "";
        }
      })
      .join("\n")
      .trim();
  }
  return "";
}

export function extractReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai") {
    return null;
  }
  if (
    message.additional_kwargs &&
    "reasoning_content" in message.additional_kwargs
  ) {
    return message.additional_kwargs.reasoning_content as string | null;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    if (part && typeof part === "object" && "thinking" in part) {
      return part.thinking as string;
    }
  }
  if (typeof message.content === "string") {
    return splitInlineReasoningFromAIMessage(message)?.reasoning ?? null;
  }
  return null;
}

export function removeReasoningContentFromMessage(message: Message) {
  if (message.type !== "ai" || !message.additional_kwargs) {
    return;
  }
  delete message.additional_kwargs.reasoning_content;
}

export function extractURLFromImageURLContent(
  content:
    | string
    | {
        url: string;
      },
) {
  if (typeof content === "string") {
    return content;
  }
  return content.url;
}

export function hasContent(message: Message) {
  if (typeof message.content === "string") {
    return (
      (
        splitInlineReasoningFromAIMessage(message)?.content ??
        message.content.trim()
      ).length > 0
    );
  }
  if (Array.isArray(message.content)) {
    return message.content.length > 0;
  }
  return false;
}

export function hasReasoning(message: Message) {
  if (message.type !== "ai") {
    return false;
  }
  if (typeof message.additional_kwargs?.reasoning_content === "string") {
    return true;
  }
  if (Array.isArray(message.content)) {
    const part = message.content[0];
    // Compatible with the Anthropic gateway
    return (part as unknown as { type: "thinking" })?.type === "thinking";
  }
  if (typeof message.content === "string") {
    return (
      (splitInlineReasoningFromAIMessage(message)?.reasoning ?? null) !== null
    );
  }
  return false;
}

export function hasToolCalls(message: Message) {
  return (
    message.type === "ai" && message.tool_calls && message.tool_calls.length > 0
  );
}

export function hasPresentFiles(message: Message) {
  return (
    message.type === "ai" &&
    message.tool_calls?.some((toolCall) => toolCall.name === "present_files")
  );
}

export function isClarificationToolMessage(message: Message) {
  return message.type === "tool" && message.name === "ask_clarification";
}

export function extractPresentFilesFromMessage(message: Message) {
  if (message.type !== "ai" || !hasPresentFiles(message)) {
    return [];
  }
  const files: string[] = [];
  for (const toolCall of message.tool_calls ?? []) {
    if (
      toolCall.name === "present_files" &&
      Array.isArray(toolCall.args.filepaths)
    ) {
      files.push(...(toolCall.args.filepaths as string[]));
    }
  }
  return files;
}

/**
 * Return only files whose matching present_files tool call has completed
 * successfully. An AI tool call is merely an intent; rendering its arguments
 * before the ToolMessage arrives creates phantom cards for blocked or failed
 * publications and lets users open an empty artifact panel.
 */
export function extractSuccessfullyPresentedFiles(messages: Message[]) {
  const successfulToolCalls = new Map<string, string[] | null>();

  for (const message of messages) {
    if (
      message.type === "tool" &&
      message.name === "present_files" &&
      typeof message.tool_call_id === "string" &&
      Reflect.get(message, "status") !== "error"
    ) {
      const artifact = Reflect.get(message, "artifact") as
        | { presented_files?: unknown }
        | undefined;
      const presentedFiles = Array.isArray(artifact?.presented_files)
        ? artifact.presented_files.filter(
            (filepath): filepath is string => typeof filepath === "string",
          )
        : null;
      successfulToolCalls.set(message.tool_call_id, presentedFiles);
    }
  }

  const files: string[] = [];
  for (const message of messages) {
    if (message.type !== "ai") {
      continue;
    }
    for (const toolCall of message.tool_calls ?? []) {
      if (
        toolCall.name !== "present_files" ||
        typeof toolCall.id !== "string"
      ) {
        continue;
      }
      const persistedFiles = successfulToolCalls.get(toolCall.id);
      if (persistedFiles === undefined) {
        continue;
      }
      const presentedFiles =
        persistedFiles ??
        (Array.isArray(toolCall.args.filepaths) ? toolCall.args.filepaths : []);
      for (const filepath of presentedFiles) {
        if (typeof filepath === "string" && !files.includes(filepath)) {
          files.push(filepath);
        }
      }
    }
  }

  return files;
}

export function hasSubagent(message: AIMessage) {
  for (const toolCall of message.tool_calls ?? []) {
    if (toolCall.name === "task") {
      return true;
    }
  }
  return false;
}

export function findToolCallResult(toolCallId: string, messages: Message[]) {
  for (const message of messages) {
    if (message.type === "tool" && message.tool_call_id === toolCallId) {
      const content = extractTextFromMessage(message);
      if (content) {
        return content;
      }
    }
  }
  return undefined;
}

export function isHiddenFromUIMessage(message: Message) {
  if (message.additional_kwargs?.hide_from_ui === true) {
    return true;
  }
  if (
    typeof message.name === "string" &&
    HIDDEN_CONTROL_MESSAGE_NAMES.has(message.name)
  ) {
    return true;
  }
  // Only the enterprise summary shim and the human activation envelope need
  // message text. Tool/system messages can return before content extraction;
  // this keeps the official per-stream grouping optimization while preserving
  // the enterprise-only hidden summary contract.
  if (message.type === "ai") {
    const content = extractTextFromMessage(message);
    return (
      content.trimStart().startsWith("<analysis>") &&
      content.includes("</analysis>") &&
      content.includes("<summary>") &&
      content.trimEnd().endsWith("</summary>")
    );
  }
  if (message.type !== "human") {
    return false;
  }
  const content = extractTextFromMessage(message);
  return (
    content.includes("<slash_skill_activation>") &&
    stripUploadedFilesTag(content).length === 0
  );
}

/**
 * Represents a file stored in message additional_kwargs.files.
 * Used for optimistic UI (uploading state) and structured file metadata.
 */
export interface FileInMessage {
  filename: string;
  size: number; // bytes
  path?: string; // virtual path, may not be set during upload
  status?: "uploading" | "uploaded";
}

/**
 * Strip backend-injected human context tags from message content.
 * Kept under its historical name because callers use it for uploaded-file
 * display cleanup.
 */
export function stripUploadedFilesTag(content: string): string {
  return content
    .replace(/<(uploaded_files|slash_skill_activation)>[\s\S]*?<\/\1>/g, "")
    .trim();
}

/**
 * Tag names that backend middlewares wrap around internal payloads before
 * letting them ride along inside LangGraph message ``content``.
 *
 * These markers are *not* user copy — they come from:
 *
 * - ``UploadsMiddleware`` → ``<uploaded_files>``
 * - ``SkillActivationMiddleware`` → ``<slash_skill_activation>``
 * - ``DynamicContextMiddleware`` → ``<system-reminder>`` (carrying
 *   ``<memory>`` / ``<current_date>`` inside)
 * - ``TodoListMiddleware`` / ``LoopDetectionMiddleware`` style reminders
 *   live in ``hide_from_ui`` HumanMessages, but their inner payload uses
 *   the same tag vocabulary.
 *
 * The primary export filter is {@link isHiddenFromUIMessage}. This list is
 * the defence-in-depth strip for any message that — by middleware bug,
 * provider quirk, or merge-conflict regression — slips through without
 * its ``hide_from_ui`` flag set.
 */
export const INTERNAL_MARKER_TAGS = [
  "uploaded_files",
  "slash_skill_activation",
  "system-reminder",
  "memory",
  "current_date",
] as const;

const INTERNAL_MARKER_RE = new RegExp(
  `<(${INTERNAL_MARKER_TAGS.join("|")})>[\\s\\S]*?</\\1>`,
  "g",
);

/**
 * Strip every known backend-injected marker from message content.
 *
 * Intended for the chat export path where a marker leaking through is a
 * privacy regression. UI render paths should keep using
 * {@link stripUploadedFilesTag} — they receive ``hide_from_ui`` messages
 * via a separate filter and the narrower function avoids stripping content
 * a user might legitimately type into a meta-discussion (e.g. asking the
 * model about its own ``<memory>`` system).
 */
export function stripInternalMarkers(content: string): string {
  return content.replace(INTERNAL_MARKER_RE, "").trim();
}

export function parseUploadedFiles(content: string): FileInMessage[] {
  // Match <uploaded_files>...</uploaded_files> tag
  const uploadedFilesRegex = /<uploaded_files>([\s\S]*?)<\/uploaded_files>/;
  // eslint-disable-next-line @typescript-eslint/prefer-regexp-exec
  const match = content.match(uploadedFilesRegex);

  if (!match) {
    return [];
  }

  const uploadedFilesContent = match[1];

  // Check if it's "No files have been uploaded yet."
  if (uploadedFilesContent?.includes("No files have been uploaded yet.")) {
    return [];
  }

  // Check if the backend reported no new files were uploaded in this message
  if (uploadedFilesContent?.includes("(empty)")) {
    return [];
  }

  // Parse file list
  // Format: - filename (size)\n  Path: /path/to/file
  const fileRegex = /- ([^\n(]+)\s*\(([^)]+)\)\s*\n\s*Path:\s*([^\n]+)/g;
  const files: FileInMessage[] = [];
  let fileMatch;

  while ((fileMatch = fileRegex.exec(uploadedFilesContent ?? "")) !== null) {
    files.push({
      filename: fileMatch[1].trim(),
      size: parseInt(fileMatch[2].trim(), 10) ?? 0,
      path: fileMatch[3].trim(),
    });
  }

  return files;
}
