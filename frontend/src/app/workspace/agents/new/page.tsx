"use client";

import {
  ArrowLeftIcon,
  BotIcon,
  CheckIcon,
  CheckCircleIcon,
  ChevronsUpDownIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  ModelSelector,
  ModelSelectorContent,
  ModelSelectorInput,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorName,
  ModelSelectorTrigger,
} from "@/components/ai-elements/model-selector";
import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import type { Agent } from "@/core/agents";
import { checkAgentName, getAgent } from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useThreadStream } from "@/core/threads/hooks";
import { uuid } from "@/core/utils/uuid";
import { cn } from "@/lib/utils";

type Step = "name" | "chat";
type BootstrapStatus = "idle" | "pending" | "starting" | "started" | "failed";

const NAME_RE = /^[\p{L}\p{N}-]+$/u;

export default function NewAgentPage() {
  const { t } = useI18n();
  const router = useRouter();

  // ── Step 1: name form ──────────────────────────────────────────────────────
  const [step, setStep] = useState<Step>("name");
  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState("");
  const [isCheckingName, setIsCheckingName] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const { models } = useModels();
  const [selectedModelName, setSelectedModelName] = useState<string>();
  const [bootstrapStatus, setBootstrapStatus] =
    useState<BootstrapStatus>("idle");
  const bootstrapStatusRef = useRef<BootstrapStatus>("idle");
  // ── Step 2: chat ───────────────────────────────────────────────────────────

  // Stable thread ID — all turns belong to the same thread
  const threadId = useMemo(() => uuid(), []);

  const [thread, sendMessage] = useThreadStream({
    threadId: step === "chat" ? threadId : undefined,
    context: {
      mode: "flash",
      is_bootstrap: true,
    },
    onStart: (id) => {
      void fetch("/api/threads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ thread_id: id }),
      }).catch(() => undefined);
    },
    onToolEnd({ name }) {
      if (name !== "setup_agent" || !agentName) return;
      getAgent(agentName)
        .then((fetched) => setAgent(fetched))
        .catch(() => {
          // agent write may not be flushed yet — ignore silently
        });
    },
  });

  // ── Handlers ───────────────────────────────────────────────────────────────

  const updateBootstrapStatus = useCallback((status: BootstrapStatus) => {
    bootstrapStatusRef.current = status;
    setBootstrapStatus(status);
  }, []);

  useEffect(() => {
    if (!selectedModelName && models.length > 0) {
      setSelectedModelName(models[0]?.name);
    }
  }, [models, selectedModelName]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return models.find((model) => model.name === selectedModelName) ?? models[0];
  }, [models, selectedModelName]);

  const handleConfirmName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) return;
    if (!NAME_RE.test(trimmed)) {
      setNameError(t.agents.nameStepInvalidError);
      return;
    }
    setNameError("");
    setIsCheckingName(true);
    try {
      const result = await checkAgentName(trimmed);
      if (!result.available) {
        setNameError(t.agents.nameStepAlreadyExistsError);
        return;
      }
    } catch {
      setNameError(t.agents.nameStepCheckError);
      return;
    } finally {
      setIsCheckingName(false);
    }
    setAgent(null);
    setAgentName(trimmed);
    updateBootstrapStatus("pending");
    setStep("chat");
  }, [
    nameInput,
    t.agents.nameStepInvalidError,
    t.agents.nameStepAlreadyExistsError,
    t.agents.nameStepCheckError,
    updateBootstrapStatus,
  ]);

  const triggerBootstrap = useCallback(async () => {
    if (!agentName) {
      return;
    }
    if (
      bootstrapStatusRef.current === "starting" ||
      bootstrapStatusRef.current === "started"
    ) {
      return;
    }

    updateBootstrapStatus("starting");

    try {
      await sendMessage(
        threadId,
        {
          text: t.agents.nameStepBootstrapMessage.replace("{name}", agentName),
          files: [],
        },
        {
          agent_name: agentName,
          model_name: selectedModel?.name,
        },
      );
      updateBootstrapStatus("started");
    } catch {
      updateBootstrapStatus("failed");
      toast.error(t.agents.nameStepCheckError);
    }
  }, [
    agentName,
    sendMessage,
    selectedModel?.name,
    t.agents.nameStepBootstrapMessage,
    t.agents.nameStepCheckError,
    threadId,
    updateBootstrapStatus,
  ]);

  useEffect(() => {
    if (step !== "chat" || !agentName || bootstrapStatus !== "pending") {
      return;
    }

    // Kick off bootstrap after the chat view has mounted, then retry once if
    // the browser delays the first effect tick or stream initialization.
    const kickoffTimer = window.setTimeout(() => {
      void triggerBootstrap();
    }, 80);
    const retryTimer = window.setTimeout(() => {
      if (bootstrapStatusRef.current === "pending") {
        void triggerBootstrap();
      }
    }, 1500);

    return () => {
      window.clearTimeout(kickoffTimer);
      window.clearTimeout(retryTimer);
    };
  }, [
    agentName,
    bootstrapStatus,
    step,
    triggerBootstrap,
  ]);

  const handleNameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void handleConfirmName();
    }
  };

  const handleChatSubmit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || thread.isLoading) return;
      await sendMessage(
        threadId,
        { text: trimmed, files: [] },
        {
          agent_name: agentName,
          model_name: selectedModel?.name,
        },
      );
    },
    [thread.isLoading, sendMessage, threadId, agentName, selectedModel?.name],
  );

  // ── Shared header ──────────────────────────────────────────────────────────

  const header = (
    <header className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => router.push("/workspace/agents")}
      >
        <ArrowLeftIcon className="h-4 w-4" />
      </Button>
      <h1 className="text-sm font-semibold">{t.agents.createPageTitle}</h1>
    </header>
  );

  // ── Step 1: name form ──────────────────────────────────────────────────────

  if (step === "name") {
    return (
      <div className="flex size-full flex-col">
        {header}
        <main className="flex flex-1 flex-col items-center justify-center px-4">
          <div className="w-full max-w-sm space-y-8">
            <div className="space-y-3 text-center">
              <div className="bg-[rgba(88,134,189,0.10)] mx-auto flex h-14 w-14 items-center justify-center rounded-full">
                <BotIcon className="text-primary h-7 w-7" />
              </div>
              <div className="space-y-1">
                <h2 className="text-xl font-semibold">
                  {t.agents.nameStepTitle}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {t.agents.nameStepHint}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <Input
                autoFocus
                placeholder={t.agents.nameStepPlaceholder}
                value={nameInput}
                onChange={(e) => {
                  setNameInput(e.target.value);
                  setNameError("");
                }}
                onKeyDown={handleNameKeyDown}
                className={cn(nameError && "border-destructive")}
              />
              {nameError && (
                <p className="text-destructive text-sm">{nameError}</p>
              )}
              {selectedModel && (
                <div className="bg-muted/40 flex items-center justify-between rounded-lg border px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-muted-foreground text-xs">
                      {t.agents.builderModelLabel}
                    </p>
                    <p className="truncate text-sm font-medium">
                      {selectedModel.display_name}
                    </p>
                  </div>
                  <ModelSelector
                    open={modelDialogOpen}
                    onOpenChange={setModelDialogOpen}
                  >
                    <ModelSelectorTrigger asChild>
                      <Button variant="outline" size="sm">
                        {t.agents.changeBuilderModel}
                        <ChevronsUpDownIcon className="ml-2 h-4 w-4 opacity-60" />
                      </Button>
                    </ModelSelectorTrigger>
                    <ModelSelectorContent title={t.agents.builderModelDialogTitle}>
                      <ModelSelectorInput placeholder={t.inputBox.searchModels} />
                      <ModelSelectorList>
                        {models.map((model) => (
                          <ModelSelectorItem
                            key={model.name}
                            value={`${model.display_name} ${model.name}`}
                            onSelect={() => {
                              setSelectedModelName(model.name);
                              setModelDialogOpen(false);
                            }}
                          >
                            <ModelSelectorName>
                              {model.display_name}
                            </ModelSelectorName>
                            {model.name === selectedModel.name ? (
                              <CheckIcon className="size-4" />
                            ) : null}
                          </ModelSelectorItem>
                        ))}
                      </ModelSelectorList>
                    </ModelSelectorContent>
                  </ModelSelector>
                </div>
              )}
              <Button
                className="w-full"
                onClick={() => void handleConfirmName()}
                disabled={!nameInput.trim() || isCheckingName}
              >
                {t.agents.nameStepContinue}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  // ── Step 2: chat ───────────────────────────────────────────────────────────

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ArtifactsProvider>
        <div className="flex size-full flex-col">
          {header}

          <main className="flex min-h-0 flex-1 flex-col">
            {/* ── Message area ── */}
            <div className="flex min-h-0 flex-1 justify-center">
              <div className="flex size-full flex-col">
                <div className="bg-muted/30 border-b px-4 py-2">
                  <div className="mx-auto flex w-full max-w-(--container-width-md) items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-muted-foreground text-xs">
                        {t.agents.currentBuilderModel}
                      </p>
                      <p className="truncate text-sm font-medium">
                        {selectedModel?.display_name ?? t.common.loading}
                      </p>
                    </div>
                    <ModelSelector
                      open={modelDialogOpen}
                      onOpenChange={setModelDialogOpen}
                    >
                      <ModelSelectorTrigger asChild>
                        <Button variant="outline" size="sm" disabled={thread.isLoading}>
                          {t.agents.changeBuilderModel}
                          <ChevronsUpDownIcon className="ml-2 h-4 w-4 opacity-60" />
                        </Button>
                      </ModelSelectorTrigger>
                      <ModelSelectorContent title={t.agents.builderModelDialogTitle}>
                        <ModelSelectorInput placeholder={t.inputBox.searchModels} />
                        <ModelSelectorList>
                          {models.map((model) => (
                            <ModelSelectorItem
                              key={model.name}
                              value={`${model.display_name} ${model.name}`}
                              onSelect={() => {
                                setSelectedModelName(model.name);
                                setModelDialogOpen(false);
                              }}
                            >
                              <ModelSelectorName>
                                {model.display_name}
                              </ModelSelectorName>
                              {model.name === selectedModel?.name ? (
                                <CheckIcon className="size-4" />
                              ) : null}
                            </ModelSelectorItem>
                          ))}
                        </ModelSelectorList>
                      </ModelSelectorContent>
                    </ModelSelector>
                  </div>
                </div>
                <MessageList
                  className="size-full pt-10"
                  threadId={threadId}
                  thread={thread}
                />
              </div>
            </div>

            {/* ── Bottom action area ── */}
            <div className="bg-background flex shrink-0 justify-center border-t px-4 py-4">
              <div className="w-full max-w-(--container-width-md)">
                {agent ? (
                  // ✅ Success card
                  <div className="flex flex-col items-center gap-4 rounded-2xl border py-8 text-center">
                    <CheckCircleIcon className="text-primary h-10 w-10" />
                    <p className="font-semibold">{t.agents.agentCreated}</p>
                    <div className="flex gap-2">
                      <Button
                        onClick={() =>
                          router.push(
                            `/workspace/agents/${agentName}/chats/new`,
                          )
                        }
                      >
                        {t.agents.startChatting}
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => router.push("/workspace/agents")}
                      >
                        {t.agents.backToGallery}
                      </Button>
                    </div>
                  </div>
                ) : (
                  // 📝 Normal input
                  <PromptInput
                    onSubmit={({ text }) => void handleChatSubmit(text)}
                  >
                    <PromptInputTextarea
                      autoFocus
                      placeholder={t.agents.createPageSubtitle}
                      disabled={thread.isLoading}
                    />
                    <PromptInputFooter className="justify-end">
                      <PromptInputSubmit disabled={thread.isLoading} />
                    </PromptInputFooter>
                  </PromptInput>
                )}
              </div>
            </div>
          </main>
        </div>
      </ArtifactsProvider>
    </ThreadContext.Provider>
  );
}
