import type { Message } from "@langchain/langgraph-sdk";

import type { AgentThread } from "./types";

export function pathOfThread(threadId: string) {
  return `/workspace/chats/${threadId}`;
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part.type === "text") {
        return part.text;
      }
    }
  }
  return null;
}

export function titleOfThread(thread: AgentThread) {
  const raw = thread.values?.title?.trim();
  if (raw && !["untitled", "未命名"].includes(raw.toLowerCase())) {
    return raw;
  }

  const messages = thread.values?.messages ?? [];
  const firstHuman = messages.find((m) => m.type === "human");
  const text = firstHuman ? textOfMessage(firstHuman)?.trim() : null;
  if (text) {
    const firstLine = text.split(/\r?\n/)[0]?.trim();
    if (firstLine) {
      return firstLine.length > 30 ? `${firstLine.slice(0, 30)}…` : firstLine;
    }
  }
  return "新对话";
}
