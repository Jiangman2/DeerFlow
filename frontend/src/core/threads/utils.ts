import type { Message } from "@langchain/langgraph-sdk";

import { agentChatPath, parseAgentSource } from "../agents/routing";

import type { AgentThread, AgentThreadContext } from "./types";

export const THREAD_PINNED_METADATA_KEY = "deerflow_pinned";
export const THREAD_ARCHIVED_METADATA_KEY = "deerflow_archived";

export type ChannelThreadSource = {
  type: "im_channel";
  provider: string;
  label: string;
};

type ThreadRouteTarget =
  | string
  | {
      thread_id: string;
      context?: Pick<AgentThreadContext, "agent_name" | "agent_source"> | null;
      metadata?: Record<string, unknown> | null;
    };

export function pathOfThread(
  thread: ThreadRouteTarget,
  context?: Pick<AgentThreadContext, "agent_name" | "agent_source"> | null,
) {
  const threadId = typeof thread === "string" ? thread : thread.thread_id;
  let agentName: string | undefined;
  const source =
    typeof thread === "string"
      ? context?.agent_source
      : thread.context?.agent_name
        ? thread.context.agent_source
        : thread.metadata?.agent_source;
  if (typeof thread === "string") {
    agentName = context?.agent_name;
  } else {
    agentName = thread.context?.agent_name;
    if (!agentName) {
      const metaAgent = thread.metadata?.agent_name;
      if (typeof metaAgent === "string") {
        agentName = metaAgent;
      }
    }
  }

  if (!agentName) return `/workspace/chats/${threadId}`;
  try {
    return agentChatPath(agentName, threadId, parseAgentSource(source));
  } catch {
    // Keep the history link usable, but the Agent page must reject this
    // explicit invalid origin rather than run a same-name fallback.
    return `${agentChatPath(agentName, threadId)}?source=invalid`;
  }
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    // Flat join ("") for single-line consumers (input box, titles); the rendered
    // body uses extractContentFromMessage, which joins multi-part content with "\n".
    const text = message.content
      .map((part) =>
        typeof part === "string" ? part : part.type === "text" ? part.text : "",
      )
      .join("");
    return text.length > 0 ? text : null;
  }
  return null;
}

export function titleOfThread(thread: AgentThread) {
  return thread.values?.title ?? "Untitled";
}

export function isThreadPinned(thread: Pick<AgentThread, "metadata">) {
  return thread.metadata?.[THREAD_PINNED_METADATA_KEY] === true;
}

export function isThreadArchived(thread: Pick<AgentThread, "metadata">) {
  return thread.metadata?.[THREAD_ARCHIVED_METADATA_KEY] === true;
}

export function sortPinnedThreads<T extends Pick<AgentThread, "metadata">>(
  threads: readonly T[],
) {
  return threads
    .map((thread, index) => ({ thread, index }))
    .sort((left, right) => {
      const pinnedDifference =
        Number(isThreadPinned(right.thread)) -
        Number(isThreadPinned(left.thread));
      return pinnedDifference || left.index - right.index;
    })
    .map(({ thread }) => thread);
}

const CHANNEL_PROVIDER_LABELS: Record<string, string> = {
  dingtalk: "DingTalk",
  discord: "Discord",
  feishu: "海能 Work",
  slack: "Slack",
  telegram: "Telegram",
  wechat: "WeChat",
  wecom: "WeCom",
};

function labelOfChannelProvider(provider: string) {
  return CHANNEL_PROVIDER_LABELS[provider] ?? provider;
}

export function channelSourceOfThread(
  thread: Pick<AgentThread, "metadata">,
): ChannelThreadSource | null {
  const source = thread.metadata?.channel_source;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return null;
  }

  if (Reflect.get(source, "type") !== "im_channel") {
    return null;
  }

  const provider = Reflect.get(source, "provider");
  if (typeof provider !== "string" || provider.trim().length === 0) {
    return null;
  }

  const normalizedProvider = provider.trim().toLowerCase();
  return {
    type: "im_channel",
    provider: normalizedProvider,
    label: labelOfChannelProvider(normalizedProvider),
  };
}
