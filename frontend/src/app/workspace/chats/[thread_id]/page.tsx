"use client";

import { MenuIcon } from "lucide-react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { useSidebar } from "@/components/ui/sidebar";
import { AnnouncementNotificationButton } from "@/components/workspace/announcement-banner";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import { BrowserTrigger } from "@/components/workspace/browser-view";
import {
  ChatBox,
  notifyThreadChatRouteChange,
  useSpecificChatMode,
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
import type { AgentMode } from "@/components/workspace/mode-hover-guide";
import {
  ModeSelector,
  reasoningEffortForMode,
  resolveAgentMode,
} from "@/components/workspace/mode-selector";
import {
  SidecarProvider,
  SidecarTrigger,
} from "@/components/workspace/sidecar";
import { ThreadScheduledTasksLink } from "@/components/workspace/thread-scheduled-tasks-link";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { useActiveGoal } from "@/components/workspace/use-active-goal";
import { Welcome } from "@/components/workspace/welcome";
import { useBrowserControlEnabled } from "@/core/features";
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
  useBranchThread,
  useThreadMetadata,
  useThreadStream,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { textOfMessage } from "@/core/threads/utils";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAdminReview = searchParams.get("admin_review") === "1";
  const {
    threadId,
    setThreadId,
    isNewThread,
    setIsNewThread,
    isMock,
    isStaticDemo,
  } = useThreadChat();
  // `isNewThread` tracks whether the backend has the thread yet — gates the
  // SDK's history fetch (see issue #2746).  `isWelcomeMode` is the visual
  // welcome layout (centered input, hero, quick actions); we flip it to false
  // the moment the user submits so the UI animates immediately, even though
  // `isNewThread` stays true until the backend actually creates the thread.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings(threadId);
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { enabled: browserControlEnabled } = useBrowserControlEnabled();
  const { models, tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock || isStaticDemo ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock && !isStaticDemo },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
    isStaticDemo,
  });
  const branchThread = useBranchThread();
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const selectedModel = useMemo(
    () =>
      models.find((model) => model.name === settings.context.model_name) ??
      models[0],
    [models, settings.context.model_name],
  );
  const supportsThinking = selectedModel?.supports_thinking ?? false;
  const handleModeSelect = useCallback(
    (mode: AgentMode) => {
      setSettings("context", {
        ...settings.context,
        mode: resolveAgentMode(mode, supportsThinking),
        reasoning_effort: reasoningEffortForMode(mode),
      });
    },
    [setSettings, settings.context, supportsThinking],
  );
  const [isMounted, setIsMounted] = useState(false);
  const createdThreadIdRef = useRef<string | null>(null);
  useSpecificChatMode();

  useEffect(() => {
    setIsMounted(true);
  }, []);

  // Keep welcome layout in sync when navigating between threads (sidebar
  // clicks, "new chat" button).  Submitting in /chats/new flips the layout
  // via onSend below — `isNewThread` stays true until onStart, so this effect
  // is harmless during the submit transition.
  useEffect(() => {
    setIsWelcomeMode(isNewThread);
  }, [isNewThread]);

  const { showNotification } = useNotification();

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
    isNewThread,
    context: settings.context,
    isMock,
    isStaticDemo,
    // onSend only animates the UI; do NOT flip `isNewThread` here — the
    // LangGraph SDK eagerly fetches /history the moment it receives a
    // thread id and assumes the thread exists on the backend (issue #2746).
    onSend: () => {
      setIsWelcomeMode(false);
    },
    onStart: (createdThreadId) => {
      // Keep the official DeerFlow lifecycle: first-run thread creation only
      // replaces the address bar. A Next Router/RSC navigation here remounts
      // the live stream while it is being established and can leave the page
      // polling a 404 state forever. Notify the thread-local providers so the
      // old task/artifact surface is still cleared without remounting the chat.
      const nextPath = `/workspace/chats/${createdThreadId}`;
      createdThreadIdRef.current = createdThreadId;
      history.replaceState(null, "", nextPath);
      setThreadId(createdThreadId);
      setIsNewThread(false);
      notifyThreadChatRouteChange(nextPath);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
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
      !isStaticDemo &&
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
      router.replace("/workspace/chats/new");
    }
  }, [
    hasMoreHistory,
    hasThreadMessages,
    isHistoryLoading,
    isMock,
    isStaticDemo,
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
      const sendPromise = sendMessage(threadId, message, undefined, options);
      if (message.files.length > 0) {
        return sendPromise;
      }
      void sendPromise;
    },
    [sendMessage, threadId],
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
        undefined,
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
    [sendMessage, threadId],
  );
  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );
  const handleBranchTurn = useCallback(
    async (messageId: string, messageIds: string[]) => {
      if (isNewThread || isMock || isStaticDemo) {
        return;
      }

      try {
        const response = await branchThread.mutateAsync({
          threadId,
          messageId,
          messageIds,
        });
        toast.success(t.conversation.branchCreated);
        router.push(`/workspace/chats/${response.thread_id}`);
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t.conversation.branchFailed,
        );
      }
    },
    [branchThread, isMock, isNewThread, isStaticDemo, router, t, threadId],
  );

  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  const browserEnabled =
    !isAdminReview &&
    !isNewThread &&
    !isMock &&
    !isStaticDemo &&
    browserControlEnabled;
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
        context={settings.context}
        isMock={isMock}
      >
        <ChatBox
          threadId={threadId}
          browserEnabled={browserEnabled}
          canHydratePersistedArtifacts={
            !isNewThread && (isStaticDemo || Boolean(threadMetadata.data))
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
              <MobileSidebarTrigger />
              {isWelcomeMode && (
                <>
                  <div className="cnooc-desktop-brandline">
                    <Image
                      alt=""
                      aria-hidden="true"
                      className="cnooc-brandline-mark"
                      height={128}
                      priority
                      src="/images/cnooc-haineng-mark.png"
                      unoptimized
                      width={128}
                    />
                    <strong>智启海油&nbsp; 赋能未来</strong>
                    <span aria-hidden="true">|</span>
                    <span>海能 AI</span>
                  </div>
                  <div className="cnooc-mobile-product-title">
                    <strong>中海油勘探开发智能体</strong>
                    <ModeSelector
                      className="cnooc-mobile-mode-pill"
                      disabled={isAdminReview}
                      mode={settings.context.mode}
                      supportsThinking={supportsThinking}
                      variant="mobile-header"
                      onSelect={handleModeSelect}
                    />
                  </div>
                </>
              )}
              <div
                className={cn(
                  "flex min-w-0 flex-1 items-center text-sm font-medium",
                  isWelcomeMode && "cnooc-welcome-thread-title",
                )}
              >
                <ThreadTitle threadId={threadId} thread={thread} />
              </div>
              <div className="cnooc-header-actions flex min-w-0 shrink-0 items-center gap-0.5 sm:gap-2">
                {!isAdminReview && !isNewThread && (
                  <ThreadScheduledTasksLink threadId={threadId} />
                )}
                <TokenUsageIndicator
                  className="cnooc-token-usage-link"
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
                {isWelcomeMode && <AnnouncementNotificationButton />}
                {!isAdminReview && <SidecarTrigger />}
                {browserEnabled && <BrowserTrigger />}
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
                <div className="cnooc-welcome-stage">
                  <Welcome mode={settings.context.mode} />
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
                    !isAdminReview &&
                    !isNewThread &&
                    !isMock &&
                    !isStaticDemo &&
                    !isUploading &&
                    !thread.isLoading
                  }
                  onRegenerateMessage={handleRegenerate}
                  onSubmitHumanInput={
                    isAdminReview || isMock || isStaticDemo
                      ? undefined
                      : handleSubmitHumanInput
                  }
                  canBranch={
                    !isAdminReview &&
                    !isNewThread &&
                    !isMock &&
                    !isStaticDemo &&
                    !isUploading &&
                    !thread.isLoading &&
                    !branchThread.isPending
                  }
                  onBranchTurn={handleBranchTurn}
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
                  {isMounted ? (
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
                        isAdminReview ||
                        isMock ||
                        isStaticDemo ||
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
                  ) : (
                    <div
                      aria-hidden="true"
                      className={cn(
                        "bg-background/5 h-32 w-full rounded-2xl",
                        isWelcomeMode && "cnooc-welcome-composer",
                      )}
                    />
                  )}
                  {isAdminReview && (
                    <div className="text-muted-foreground w-full translate-y-3 text-center text-xs">
                      管理员只读查看：不能继续、修改或删除该用户的会话
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

function MobileSidebarTrigger() {
  const { toggleSidebar } = useSidebar();

  return (
    <button
      aria-label="打开导航菜单"
      className="cnooc-mobile-menu-trigger hidden items-center justify-center md:hidden"
      type="button"
      onClick={toggleSidebar}
    >
      <MenuIcon aria-hidden="true" />
    </button>
  );
}
