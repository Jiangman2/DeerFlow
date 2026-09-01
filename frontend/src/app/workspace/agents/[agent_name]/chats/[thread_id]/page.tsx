"use client";

import { BotIcon, PlusSquare } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { AgentWelcome } from "@/components/workspace/agent-welcome";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  notifyThreadChatRouteChange,
  useThreadChat,
} from "@/components/workspace/chats";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { GoalStatus } from "@/components/workspace/goal-status";
import {
  InputBox,
  type InputBoxSubmitOptions,
} from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  SidecarProvider,
  SidecarTrigger,
} from "@/components/workspace/sidecar";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Tooltip } from "@/components/workspace/tooltip";
import { useActiveGoal } from "@/components/workspace/use-active-goal";
import { decodeAgentNameRouteParam, useAgent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildHumanInputResponseText,
  hasOpenHumanInputRequest,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings, useThreadSettings } from "@/core/settings";
import {
  useThreadMetadata,
  useThreadStream,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function AgentChatPage() {
  const { t } = useI18n();
  const router = useRouter();

  const { agent_name: agentNameParam } = useParams<{
    agent_name: string;
  }>();
  const agentName = useMemo(
    () => decodeAgentNameRouteParam(agentNameParam),
    [agentNameParam],
  );
  const agentRouteName = useMemo(
    () => encodeURIComponent(agentName),
    [agentName],
  );

  const { agent } = useAgent(agentName);

  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  // `isNewThread` gates history/token-usage fetches until the backend creates
  // the thread. `isWelcomeMode` controls only the centered welcome layout, so
  // it can flip immediately on submit without triggering eager history loads.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const createdThreadIdRef = useRef<string | null>(null);

  const { showNotification } = useNotification();

  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadStream({
    threadId: isNewThread ? undefined : threadId,
    displayThreadId: threadId,
    context: { ...settings.context, agent_name: agentName },
    isMock,
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      createdThreadIdRef.current = createdThreadId;
      // Keep the official DeerFlow lifecycle used by ordinary chats. A
      // Next.js RSC navigation while the first Agent stream is being created
      // can remount the SDK transport and strand the page on a 404 poll.
      const nextPath = `/workspace/agents/${agentRouteName}/chats/${createdThreadId}`;
      history.replaceState(null, "", nextPath);
      setThreadId(createdThreadId);
      setIsNewThread(false);
      notifyThreadChatRouteChange(nextPath);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages[state.messages.length - 1];
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });

  const hasThreadMessages = thread.messages.length > 0;

  useEffect(() => {
    if (
      !isNewThread &&
      !isMock &&
      threadMetadata.isFetched &&
      threadMetadata.data === null &&
      createdThreadIdRef.current !== threadId &&
      !thread.isLoading &&
      !threadMetadata.isLoading &&
      !threadMetadata.isFetching &&
      !isHistoryLoading &&
      !hasMoreHistory &&
      !hasThreadMessages
    ) {
      router.replace(`/workspace/agents/${agentRouteName}/chats/new`);
    }
  }, [
    agentRouteName,
    hasMoreHistory,
    hasThreadMessages,
    isHistoryLoading,
    isMock,
    isNewThread,
    router,
    threadId,
    thread.isLoading,
    threadMetadata.data,
    threadMetadata.isFetched,
    threadMetadata.isFetching,
    threadMetadata.isLoading,
  ]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage, options?: InputBoxSubmitOptions) => {
      const sendPromise = sendMessage(
        threadId,
        message,
        { agent_name: agentName },
        options,
      );
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [agentName, sendMessage, threadId],
  );

  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      let sent = false;
      await sendMessage(
        threadId,
        {
          text: buildHumanInputResponseText(request, response),
          files: [],
        },
        { agent_name: agentName },
        {
          additionalKwargs: {
            hide_from_ui: true,
            human_input_response: response,
          },
          onSent: () => {
            sent = true;
          },
        },
      );
      return sent;
    },
    [agentName, sendMessage, threadId],
  );

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );

  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  const { activeGoal, hasGoal, setLocalGoal } = useActiveGoal(
    threadId,
    thread.values.goal,
  );
  const hasOpenHumanInputCard = useMemo(
    () =>
      hasOpenHumanInputRequest(
        thread.messages,
        (message) => !isHiddenFromUIMessage(message),
      ),
    [thread.messages],
  );

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <SidecarProvider
        parentThreadId={threadId}
        context={{ ...settings.context, agent_name: agentName }}
        isMock={isMock}
      >
        <ChatBox
          threadId={threadId}
          canHydratePersistedArtifacts={
            !isNewThread && Boolean(threadMetadata.data)
          }
        >
          <div className="relative flex size-full min-h-0 justify-between">
            <header
              className={cn(
                "cnooc-chat-header absolute top-0 right-0 left-0 z-40 flex h-12 min-w-0 shrink-0 items-center gap-1 overflow-hidden px-2 sm:gap-2 sm:px-4",
                isWelcomeMode && "cnooc-chat-header-welcome",
                isWelcomeMode
                  ? "bg-background/0 backdrop-blur-none"
                  : "bg-background/80 shadow-xs backdrop-blur",
              )}
            >
              <SidebarTrigger className="md:hidden" />
              {/* Agent badge */}
              <div className="cnooc-agent-header-badge flex min-w-0 shrink-0 items-center gap-1 rounded-md border p-1 sm:gap-1.5 sm:px-2 sm:py-1">
                <BotIcon className="text-primary h-3.5 w-3.5" />
                <span className="max-w-36 truncate text-xs font-medium sm:max-w-none">
                  {agent?.name ?? agentName}
                </span>
              </div>

              <div
                className={cn(
                  "flex min-w-0 flex-1 items-center text-sm font-medium",
                  isWelcomeMode && "cnooc-welcome-thread-title",
                )}
              >
                <ThreadTitle threadId={threadId} thread={thread} />
              </div>
              <div className="cnooc-header-actions flex min-w-0 shrink-0 items-center gap-0.5 sm:mr-4 sm:gap-1">
                {!isNewThread ? (
                  <Tooltip content={t.agents.newChat}>
                    <Button
                      className="size-8 px-0 sm:h-8 sm:w-auto sm:px-3"
                      data-testid="agent-new-chat-button"
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        router.push(
                          `/workspace/agents/${agentRouteName}/chats/new`,
                        );
                      }}
                    >
                      <PlusSquare />
                      <span className="hidden sm:inline">
                        {t.agents.newChat}
                      </span>
                    </Button>
                  </Tooltip>
                ) : null}
                <TokenUsageIndicator
                  threadId={isNewThread ? undefined : threadId}
                  backendUsage={backendTokenUsage}
                  enabled
                  messages={thread.messages}
                  pendingMessages={pendingUsageMessages}
                  preferences={localSettings.tokenUsage}
                  onPreferencesChange={(preferences) =>
                    setLocalSettings("tokenUsage", preferences)
                  }
                />
                <SidecarTrigger />
                <ExportTrigger threadId={threadId} />
                <ArtifactTrigger />
              </div>
            </header>

            <main
              className={cn(
                "cnooc-chat-main flex min-h-0 max-w-full grow flex-col",
                !isWelcomeMode && "cnooc-chat-main-task",
              )}
            >
              {isWelcomeMode && !hasGoal && !hasTodos && (
                <div className="cnooc-welcome-stage cnooc-agent-welcome-stage">
                  <AgentWelcome agent={agent} agentName={agentName} />
                </div>
              )}
              <div className="flex min-h-0 flex-1 justify-center">
                <MessageList
                  className={cn("size-full", !isWelcomeMode && "pt-10")}
                  testId="main-message-list"
                  threadId={threadId}
                  thread={thread}
                  paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                  hasMoreHistory={hasMoreHistory}
                  loadMoreHistory={loadMoreHistory}
                  isHistoryLoading={isHistoryLoading}
                  tokenUsageInlineMode={tokenUsageInlineMode}
                  canRegenerate={
                    !isNewThread &&
                    !isMock &&
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                    !isUploading &&
                    !thread.isLoading
                  }
                  onRegenerateMessage={handleRegenerate}
                  onSubmitHumanInput={
                    isMock || env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"
                      ? undefined
                      : handleSubmitHumanInput
                  }
                />
              </div>

              <div
                className={cn(
                  "cnooc-composer-dock right-0 bottom-0 left-0 z-30 flex min-w-0 justify-center px-2 sm:px-4",
                  isWelcomeMode
                    ? "cnooc-composer-dock-welcome app-safe-area-bottom absolute pb-3 sm:pb-0"
                    : "relative shrink-0 pb-4",
                )}
              >
                <div
                  className={cn(
                    "cnooc-composer-frame relative w-full",
                    isWelcomeMode
                      ? "max-w-(--container-width-sm)"
                      : "max-w-(--container-width-md)",
                  )}
                >
                  {(hasGoal || hasTodos) && (
                    <div
                      className={cn(
                        "right-0 left-0 z-0",
                        isWelcomeMode ? "absolute -top-4" : "relative",
                      )}
                    >
                      <div
                        className={cn(
                          "right-0 bottom-0 left-0 flex flex-col",
                          isWelcomeMode ? "absolute" : "relative",
                        )}
                      >
                        {activeGoal && <GoalStatus goal={activeGoal} />}
                        {hasTodos && (
                          <TodoList
                            className="bg-background/5"
                            todos={thread.values.todos ?? []}
                            hidden={false}
                          />
                        )}
                      </div>
                    </div>
                  )}

                  <InputBox
                    className={cn(
                      "bg-background/5 w-full",
                      isWelcomeMode && "cnooc-welcome-composer",
                    )}
                    isWelcomeMode={isWelcomeMode}
                    threadId={threadId}
                    autoFocus={isWelcomeMode}
                    status={
                      thread.error
                        ? "error"
                        : thread.isLoading
                          ? "streaming"
                          : "ready"
                    }
                    context={settings.context}
                    extraHeader={undefined}
                    disabled={
                      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                      isUploading ||
                      hasOpenHumanInputCard ||
                      (!isNewThread && isHistoryLoading)
                    }
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onGoalChange={setLocalGoal}
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                  {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                    <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                      {t.common.notAvailableInDemoMode}
                    </div>
                  )}
                </div>
              </div>
              {isWelcomeMode && (
                <footer
                  aria-label="版权信息"
                  className="cnooc-welcome-copyright"
                >
                  Copyright © 2026-2027 中海油研究总院有限责任公司 版权所有
                  <span aria-hidden="true">　</span>
                  地址：北京市朝阳区太阳宫南街6号院
                </footer>
              )}
            </main>
          </div>
        </ChatBox>
      </SidecarProvider>
    </ThreadContext.Provider>
  );
}
