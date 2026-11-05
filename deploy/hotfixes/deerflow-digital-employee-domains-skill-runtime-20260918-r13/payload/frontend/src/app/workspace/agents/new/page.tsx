"use client";

import {
  ArrowLeftIcon,
  BotIcon,
  CheckIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ChevronsUpDownIcon,
  InfoIcon,
  MoreHorizontalIcon,
  SaveIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { AgentNameIcon } from "@/components/workspace/agents/agent-name-icon";
import { BusinessDomainPicker } from "@/components/workspace/agents/business-domain-picker";
import { TagEditor } from "@/components/workspace/agents/tag-editor";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import {
  EXPERT_AVATAR_OPTIONS,
  saveExpertAvatarSelection,
  type Agent,
  useDigitalEmployeeCategories,
} from "@/core/agents";
import {
  AgentNameCheckError,
  AgentsApiDisabledError,
  checkAgentName,
  getAgent,
  updateAgent,
} from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import {
  buildHumanInputResponseText,
  hasOpenHumanInputRequest,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { useThreadStream } from "@/core/threads/hooks";
import { uuid } from "@/core/utils/uuid";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

type Step = "name" | "chat";
type SetupAgentStatus = "idle" | "requested" | "completed";

const NAME_RE = /^[\p{L}\p{N}-]+$/u;
const SAVE_HINT_STORAGE_KEY = "platform.agent-create.save-hint-seen";
const AGENT_READ_RETRY_DELAYS_MS = [200, 500, 1_000, 2_000];
const AVATAR_PREVIEW_COUNT = 6;

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function getAgentWithRetry(agentName: string) {
  for (const delay of [0, ...AGENT_READ_RETRY_DELAYS_MS]) {
    if (delay > 0) {
      await wait(delay);
    }

    try {
      return await getAgent(agentName);
    } catch {
      // Retry until the write settles or the attempts are exhausted.
    }
  }

  return null;
}

export default function NewAgentPage() {
  const { t } = useI18n();
  const router = useRouter();

  const [step, setStep] = useState<Step>("name");
  const [nameInput, setNameInput] = useState("");
  const [descriptionInput, setDescriptionInput] = useState("");
  const [categoryId, setCategoryId] = useState("comprehensive-management");
  const [tags, setTags] = useState<string[]>([]);
  const [selectedAvatarId, setSelectedAvatarId] = useState(
    EXPERT_AVATAR_OPTIONS[0]!.id,
  );
  const [showAllAvatars, setShowAllAvatars] = useState(false);
  const [nameError, setNameError] = useState("");
  const [isCheckingName, setIsCheckingName] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [showSaveHint, setShowSaveHint] = useState(false);
  const [modelDialogOpen, setModelDialogOpen] = useState(false);
  const [selectedModelName, setSelectedModelName] = useState<string>();
  const { models } = useModels();
  const { categories } = useDigitalEmployeeCategories();
  const [setupAgentStatus, setSetupAgentStatus] =
    useState<SetupAgentStatus>("idle");

  const threadId = useMemo(() => uuid(), []);

  const { thread, sendMessage } = useThreadStream({
    threadId: undefined,
    context: {
      mode: "flash",
      is_bootstrap: true,
      model_name: selectedModelName,
    },
    onFinish() {
      if (!agent && setupAgentStatus === "requested") {
        setSetupAgentStatus("idle");
      }
    },
    onToolEnd({ name }) {
      if (name !== "setup_agent" || !agentName) return;
      setSetupAgentStatus("completed");
      void getAgentWithRetry(agentName).then(async (fetched) => {
        if (fetched) {
          try {
            setAgent(
              await updateAgent(agentName, {
                avatar_id: selectedAvatarId,
                display_name: nameInput.trim(),
                description: descriptionInput.trim() || fetched.description,
                category_id: categoryId || null,
                tags,
                model: selectedModel?.name ?? null,
              }),
            );
          } catch {
            setAgent(fetched);
          }
          return;
        }

        toast.error(t.agents.agentCreatedPendingRefresh);
      });
    },
  });
  const hasOpenHumanInputCard = useMemo(
    () =>
      hasOpenHumanInputRequest(
        thread.messages,
        (message) => !isHiddenFromUIMessage(message),
      ),
    [thread.messages],
  );

  useEffect(() => {
    if (!selectedModelName && models.length > 0) {
      setSelectedModelName(models[0]?.name);
    }
  }, [models, selectedModelName]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return (
      models.find((model) => model.name === selectedModelName) ?? models[0]
    );
  }, [models, selectedModelName]);

  useEffect(() => {
    if (typeof window === "undefined" || step !== "chat") {
      return;
    }
    if (window.localStorage.getItem(SAVE_HINT_STORAGE_KEY) === "1") {
      return;
    }
    setShowSaveHint(true);
    window.localStorage.setItem(SAVE_HINT_STORAGE_KEY, "1");
  }, [step]);

  const handleConfirmName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) return;
    if (trimmed.length > 64 || !NAME_RE.test(trimmed)) {
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
    } catch (err) {
      if (err instanceof AgentsApiDisabledError) {
        setNameError(t.agents.nameStepApiDisabledError);
      } else if (
        err instanceof AgentNameCheckError &&
        err.reason === "backend_unreachable"
      ) {
        setNameError(t.agents.nameStepNetworkError);
      } else if (
        err instanceof AgentNameCheckError &&
        err.reason === "request_failed"
      ) {
        // Surface the backend-provided detail (e.g. validation error) when
        // one is present, wrapped in a localised prefix so zh-CN users
        // don't see a bare English string next to the surrounding Chinese
        // UI. Falls back to the generic localised fallback when the backend
        // sent no detail — `err.message` is unreliable for this branch
        // because `checkAgentName` substitutes a generated fallback string
        // ("Failed to check agent name: ${statusText}") when `detail` is
        // missing, so testing `err.message` would always be truthy and the
        // generated fallback would leak through.
        setNameError(
          err.detail
            ? t.agents.nameStepCheckErrorWithDetail.replace(
                "{detail}",
                err.detail,
              )
            : t.agents.nameStepCheckError,
        );
      } else {
        setNameError(t.agents.nameStepCheckError);
      }
      return;
    } finally {
      setIsCheckingName(false);
    }

    saveExpertAvatarSelection(trimmed, selectedAvatarId);
    setAgentName(trimmed);
    setStep("chat");
    const selectedCategory = categories.find(
      (category) => category.id === categoryId,
    );
    const initialProfile = [
      descriptionInput.trim()
        ? t.agents.initialDescription.replace(
            "{value}",
            descriptionInput.trim(),
          )
        : null,
      selectedCategory
        ? t.agents.initialCategory.replace("{value}", selectedCategory.name)
        : null,
      tags.length
        ? t.agents.initialTags.replace("{value}", tags.join("、"))
        : null,
    ]
      .filter(Boolean)
      .join("\n");
    await sendMessage(
      threadId,
      {
        text: `${t.agents.nameStepBootstrapMessage.replace("{name}", trimmed)}${initialProfile ? `\n\n${initialProfile}` : ""}`,
        files: [],
      },
      { agent_name: trimmed, model_name: selectedModel?.name },
    );
  }, [
    categories,
    categoryId,
    descriptionInput,
    nameInput,
    sendMessage,
    selectedModel?.name,
    selectedAvatarId,
    t.agents.nameStepAlreadyExistsError,
    t.agents.nameStepApiDisabledError,
    t.agents.nameStepNetworkError,
    t.agents.nameStepBootstrapMessage,
    t.agents.nameStepCheckError,
    t.agents.nameStepCheckErrorWithDetail,
    t.agents.nameStepInvalidError,
    t.agents.initialCategory,
    t.agents.initialDescription,
    t.agents.initialTags,
    tags,
    threadId,
  ]);

  const handleNameKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !isIMEComposing(e)) {
      e.preventDefault();
      void handleConfirmName();
    }
  };

  const handleChatSubmit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || thread.isLoading || hasOpenHumanInputCard) return;
      await sendMessage(
        threadId,
        { text: trimmed, files: [] },
        { agent_name: agentName, model_name: selectedModel?.name },
      );
    },
    [
      agentName,
      hasOpenHumanInputCard,
      selectedModel?.name,
      sendMessage,
      thread.isLoading,
      threadId,
    ],
  );

  const handleSubmitHumanInput = useCallback(
    async (request: HumanInputRequest, response: HumanInputResponse) => {
      if (!agentName) {
        return false;
      }

      let sent = false;
      await sendMessage(
        threadId,
        {
          text: buildHumanInputResponseText(request, response),
          files: [],
        },
        { agent_name: agentName, model_name: selectedModel?.name },
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
    [agentName, selectedModel?.name, sendMessage, threadId],
  );

  const handleSaveAgent = useCallback(async () => {
    if (
      !agentName ||
      agent ||
      thread.isLoading ||
      setupAgentStatus !== "idle"
    ) {
      return;
    }

    setSetupAgentStatus("requested");
    setShowSaveHint(false);
    try {
      await sendMessage(
        threadId,
        { text: t.agents.saveCommandMessage, files: [] },
        { agent_name: agentName, model_name: selectedModel?.name },
        { additionalKwargs: { hide_from_ui: true } },
      );
      toast.success(t.agents.saveRequested);
    } catch (error) {
      setSetupAgentStatus("idle");
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }, [
    agent,
    agentName,
    sendMessage,
    selectedModel?.name,
    setupAgentStatus,
    t.agents.saveCommandMessage,
    t.agents.saveRequested,
    thread.isLoading,
    threadId,
  ]);

  const modelSelector = selectedModel ? (
    <ModelSelector open={modelDialogOpen} onOpenChange={setModelDialogOpen}>
      <ModelSelectorTrigger asChild>
        <Button variant="outline" size="sm" disabled={thread.isLoading}>
          {t.agents.changeBuilderModel}
          <ChevronsUpDownIcon data-icon="inline-end" className="opacity-60" />
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
              <ModelSelectorName>{model.display_name}</ModelSelectorName>
              {model.name === selectedModel.name ? (
                <CheckIcon className="size-4" />
              ) : null}
            </ModelSelectorItem>
          ))}
        </ModelSelectorList>
      </ModelSelectorContent>
    </ModelSelector>
  ) : null;

  const header = (
    <header className="cnooc-agent-builder-header flex min-w-0 shrink-0 items-center justify-between gap-3 border-b px-3 py-2 sm:px-4 sm:py-3">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => router.push("/workspace/agents")}
        >
          <ArrowLeftIcon aria-hidden="true" />
        </Button>
        <h1 className="text-sm font-semibold">{t.agents.createPageTitle}</h1>
      </div>

      {step === "chat" ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label={t.agents.more}>
              <MoreHorizontalIcon className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onSelect={() => void handleSaveAgent()}
              disabled={[
                Boolean(agent),
                thread.isLoading,
                setupAgentStatus !== "idle",
              ].some(Boolean)}
            >
              <SaveIcon className="h-4 w-4" />
              {setupAgentStatus === "requested"
                ? t.agents.saving
                : t.agents.save}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </header>
  );

  if (step === "name") {
    const previewAvatars = EXPERT_AVATAR_OPTIONS.slice(0, AVATAR_PREVIEW_COUNT);
    const selectedAvatar = EXPERT_AVATAR_OPTIONS.find(
      (option) => option.id === selectedAvatarId,
    );
    const visibleAvatars = showAllAvatars
      ? EXPERT_AVATAR_OPTIONS
      : selectedAvatar &&
          !previewAvatars.some((option) => option.id === selectedAvatar.id)
        ? [...previewAvatars.slice(0, -1), selectedAvatar]
        : previewAvatars;

    return (
      <div className="cnooc-agent-builder-page flex size-full flex-col">
        {header}
        <main className="cnooc-agent-builder-name-stage flex min-h-0 flex-1 flex-col items-center justify-center overflow-hidden px-3 py-3 sm:px-4 sm:py-5">
          <div
            data-testid="agent-create-dialog"
            className="cnooc-agent-builder-name-card bg-card flex h-full max-h-[48rem] min-h-0 w-full max-w-3xl flex-col overflow-hidden rounded-2xl border shadow-sm"
          >
            <div className="flex shrink-0 flex-col gap-3 px-4 pt-4 pb-3 text-center sm:px-8 sm:pt-6 sm:pb-4">
              <AgentNameIcon />
              <div className="flex flex-col gap-1">
                <h2 className="text-xl font-semibold">
                  {t.agents.nameStepTitle}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {t.agents.nameStepHint}
                </p>
              </div>
            </div>

            <div
              data-testid="agent-create-scroll-region"
              className="app-scroll-region min-h-0 flex-1 overflow-y-auto border-y px-4 py-4 sm:px-8"
            >
              <div className="flex flex-col gap-6">
                <section
                  className="flex flex-col gap-3"
                  aria-labelledby="expert-avatar-label"
                  data-testid="expert-avatar-picker"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <h3
                        id="expert-avatar-label"
                        className="text-sm font-medium"
                      >
                        {t.agents.avatarPickerLabel}
                      </h3>
                      <p className="text-muted-foreground mt-1 text-xs">
                        {t.agents.avatarPickerHint}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-expanded={showAllAvatars}
                      aria-controls="expert-avatar-options"
                      className="text-primary shrink-0 px-2 text-xs"
                      onClick={() => setShowAllAvatars((current) => !current)}
                    >
                      {showAllAvatars
                        ? t.agents.collapseAvatars
                        : t.agents.expandAvatars.replace(
                            "{count}",
                            String(EXPERT_AVATAR_OPTIONS.length),
                          )}
                      {showAllAvatars ? (
                        <ChevronUpIcon aria-hidden="true" />
                      ) : (
                        <ChevronDownIcon aria-hidden="true" />
                      )}
                    </Button>
                  </div>
                  <div
                    id="expert-avatar-options"
                    className="grid grid-cols-3 gap-2 sm:grid-cols-6"
                  >
                    {visibleAvatars.map((option) => {
                      const selected = option.id === selectedAvatarId;
                      return (
                        <Button
                          key={option.id}
                          type="button"
                          variant="outline"
                          aria-label={option.label}
                          aria-pressed={selected}
                          data-testid="expert-avatar-option"
                          className={cn(
                            "relative h-auto min-w-0 flex-col gap-1 overflow-hidden rounded-xl px-1 py-2",
                            selected && "border-primary ring-primary ring-2",
                          )}
                          onClick={() => setSelectedAvatarId(option.id)}
                        >
                          <Avatar className="bg-muted size-14 rounded-full border-2 border-white shadow-sm ring-1 ring-blue-100 sm:size-16">
                            <AvatarImage
                              src={option.src}
                              alt=""
                              className="rounded-full object-cover"
                            />
                            <AvatarFallback className="rounded-full">
                              <BotIcon aria-hidden="true" />
                            </AvatarFallback>
                          </Avatar>
                          <span className="w-full truncate text-[11px] font-normal">
                            {option.label}
                          </span>
                          {selected ? (
                            <CheckCircleIcon className="text-primary-foreground fill-primary absolute top-1 right-1" />
                          ) : null}
                        </Button>
                      );
                    })}
                  </div>
                </section>

                <div className="flex flex-col gap-3">
                  <Input
                    autoFocus
                    maxLength={64}
                    placeholder={t.agents.nameStepPlaceholder}
                    value={nameInput}
                    onChange={(e) => {
                      setNameInput(e.target.value);
                      setNameError("");
                    }}
                    onKeyDown={handleNameKeyDown}
                    className={cn(nameError && "border-destructive")}
                  />
                  <p className="text-muted-foreground text-right text-xs">
                    {nameInput.length}/64
                  </p>
                  {nameError ? (
                    <p className="text-destructive text-sm">{nameError}</p>
                  ) : null}
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="grid gap-1.5 sm:col-span-2">
                      <label className="text-left text-sm font-medium">
                        {t.agents.category}
                      </label>
                      <BusinessDomainPicker
                        categories={categories}
                        value={categoryId}
                        onValueChange={setCategoryId}
                      />
                    </div>
                    <div className="grid gap-1.5 sm:col-span-2">
                      <label className="text-left text-sm font-medium">
                        {t.agents.descriptionLabel}
                      </label>
                      <Textarea
                        maxLength={500}
                        placeholder={t.agents.descriptionPlaceholder}
                        rows={2}
                        value={descriptionInput}
                        onChange={(event) =>
                          setDescriptionInput(event.target.value)
                        }
                      />
                      <p className="text-muted-foreground text-right text-xs">
                        {descriptionInput.length}/500
                      </p>
                    </div>
                    <div className="grid gap-1.5 sm:col-span-2">
                      <label className="text-left text-sm font-medium">
                        {t.agents.tags}
                      </label>
                      <TagEditor
                        value={tags}
                        onChange={setTags}
                        suggestionContext={{
                          name: nameInput,
                          description: descriptionInput,
                          categoryId,
                        }}
                      />
                    </div>
                  </div>
                  {selectedModel ? (
                    <div className="bg-muted/40 flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
                      <div className="min-w-0">
                        <p className="text-muted-foreground text-xs">
                          {t.agents.builderModelLabel}
                        </p>
                        <p className="truncate text-sm font-medium">
                          {selectedModel.display_name}
                        </p>
                      </div>
                      {modelSelector}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>

            <div className="bg-card shrink-0 px-4 py-3 sm:px-8 sm:py-4">
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

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ArtifactsProvider>
        <div className="cnooc-agent-builder-page flex size-full flex-col">
          {header}

          <main className="cnooc-agent-builder-main flex min-h-0 flex-1 flex-col">
            {selectedModel ? (
              <div className="cnooc-agent-builder-model-strip border-b px-3 py-2 sm:px-4">
                <div className="mx-auto flex w-full max-w-(--container-width-md) items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-muted-foreground text-xs">
                      {t.agents.currentBuilderModel}
                    </p>
                    <p className="truncate text-sm font-medium">
                      {selectedModel.display_name}
                    </p>
                  </div>
                  {modelSelector}
                </div>
              </div>
            ) : null}
            {showSaveHint ? (
              <div className="cnooc-agent-builder-hint px-3 pt-3 sm:px-4 sm:pt-4">
                <div className="mx-auto w-full max-w-(--container-width-md)">
                  <Alert className="cnooc-agent-builder-hint-card">
                    <InfoIcon className="h-4 w-4" />
                    <AlertDescription>{t.agents.saveHint}</AlertDescription>
                  </Alert>
                </div>
              </div>
            ) : null}

            <div className="cnooc-agent-builder-messages flex min-h-0 flex-1 justify-center">
              <MessageList
                className={cn("size-full", showSaveHint ? "pt-4" : "pt-10")}
                threadId={threadId}
                thread={thread}
                onSubmitHumanInput={
                  agentName ? handleSubmitHumanInput : undefined
                }
              />
            </div>

            <div className="cnooc-agent-builder-dock app-safe-area-bottom flex shrink-0 justify-center px-3 py-3 sm:px-4 sm:py-4">
              <div className="cnooc-agent-builder-dock-inner w-full max-w-(--container-width-md)">
                {agent ? (
                  <div className="cnooc-agent-builder-complete flex flex-col items-center gap-4 rounded-2xl border py-8 text-center">
                    <CheckCircleIcon className="text-primary h-10 w-10" />
                    <p className="font-semibold">{t.agents.agentCreated}</p>
                    <div className="flex gap-2">
                      <Button
                        onClick={() =>
                          router.push(
                            `/workspace/agents/${encodeURIComponent(
                              agentName,
                            )}/chats/new`,
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
                  <PromptInput
                    data-mobile-composer="true"
                    data-testid="agent-builder-composer"
                    className="cnooc-agent-builder-prompt"
                    disabled={thread.isLoading || hasOpenHumanInputCard}
                    onSubmit={({ text }) => void handleChatSubmit(text)}
                  >
                    <PromptInputTextarea
                      autoFocus
                      className="cnooc-agent-builder-textarea"
                      placeholder={t.agents.createPageSubtitle}
                      disabled={thread.isLoading || hasOpenHumanInputCard}
                    />
                    <PromptInputFooter className="cnooc-agent-builder-prompt-footer justify-end">
                      <PromptInputSubmit
                        className="cnooc-agent-builder-submit"
                        disabled={thread.isLoading || hasOpenHumanInputCard}
                      />
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
