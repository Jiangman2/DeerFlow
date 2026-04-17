import type { BaseStream } from "@langchain/langgraph-sdk";
import { useEffect } from "react";

import { useI18n } from "@/core/i18n/hooks";
import type { AgentThreadState } from "@/core/threads";
import { textOfMessage } from "@/core/threads/utils";

import { useThreadChat } from "./chats";
import { FlipDisplay } from "./flip-display";

export function ThreadTitle({
  threadId,
  thread,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const { t } = useI18n();
  const { isNewThread } = useThreadChat();

  const rawTitle = thread.values?.title?.trim();
  const isPlaceholderTitle =
    rawTitle && ["untitled", "未命名"].includes(rawTitle.toLowerCase());
  const firstHuman = thread.values?.messages?.find((m) => m.type === "human");
  const draftTitle = firstHuman ? textOfMessage(firstHuman)?.trim() : null;
  const derivedTitle = (() => {
    if (rawTitle && !isPlaceholderTitle) return rawTitle;
    if (draftTitle) {
      const firstLine = draftTitle.split(/\r?\n/)[0]?.trim();
      if (firstLine) return firstLine.length > 30 ? `${firstLine.slice(0, 30)}…` : firstLine;
    }
    return t.pages.newChat;
  })();

  useEffect(() => {
    if (thread.isThreadLoading) {
      document.title = `Loading... - ${t.pages.appName}`;
    } else {
      document.title = `${derivedTitle} - ${t.pages.appName}`;
    }
  }, [
    isNewThread,
    t.pages.newChat,
    t.pages.appName,
    thread.isThreadLoading,
    derivedTitle,
  ]);

  return (
    <FlipDisplay uniqueKey={threadId}>
      {derivedTitle}
    </FlipDisplay>
  );
}
