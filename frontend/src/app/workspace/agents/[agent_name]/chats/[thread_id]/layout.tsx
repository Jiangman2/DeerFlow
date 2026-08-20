"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { useThreadChat } from "@/components/workspace/chats";
import { SubtasksProvider } from "@/core/tasks/context";

export default function AgentChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { threadId, isNewThread } = useThreadChat();
  const scopeKey = isNewThread ? "new" : (threadId ?? "unknown");

  return (
    <SubtasksProvider scopeKey={scopeKey}>
      <ArtifactsProvider scopeKey={scopeKey}>
        <PromptInputProvider>{children}</PromptInputProvider>
      </ArtifactsProvider>
    </SubtasksProvider>
  );
}
