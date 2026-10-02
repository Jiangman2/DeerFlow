import type { Message } from "@langchain/langgraph-sdk";
import {
  BookOpenTextIcon,
  ChevronUp,
  CoinsIcon,
  FolderOpenIcon,
  GlobeIcon,
  LightbulbIcon,
  ListTodoIcon,
  MessageCircleQuestionMarkIcon,
  MessageSquareTextIcon,
  NotebookPenIcon,
  SearchIcon,
  SquareTerminalIcon,
  WrenchIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtSearchResult,
  ChainOfThoughtSearchResults,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { CodeBlock } from "@/components/ai-elements/code-block";
import { Button } from "@/components/ui/button";
import { isSandboxInternalURL } from "@/core/artifacts/live-preview";
import { useI18n } from "@/core/i18n/hooks";
import {
  toolDisplayRecord,
  toolDisplayText,
  toolWebHref,
} from "@/core/messages/tool-display";
import { formatTokenCount } from "@/core/messages/usage";
import type { TokenDebugStep } from "@/core/messages/usage-model";
import {
  extractContentFromMessage,
  extractReasoningContentFromMessage,
  extractTextFromMessage,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { extractTitleFromMarkdown } from "@/core/utils/markdown";
import { env } from "@/env";
import { cn } from "@/lib/utils";

import { useArtifacts } from "../artifacts";
import { FlipDisplay } from "../flip-display";
import { Tooltip } from "../tooltip";

import { MarkdownContent } from "./markdown-content";
import { createMarkdownLinkComponent } from "./markdown-link";
import { ToolResultLink } from "./tool-result-link";

export function MessageGroup({
  className,
  messages,
  isLoading = false,
  threadId,
  tokenDebugSteps = [],
  showTokenDebugSummaries = false,
}: {
  className?: string;
  messages: Message[];
  isLoading?: boolean;
  threadId?: string;
  tokenDebugSteps?: TokenDebugStep[];
  showTokenDebugSummaries?: boolean;
}) {
  const { t } = useI18n();
  const [showAbove, setShowAbove] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const [showLastThinking, setShowLastThinking] = useState(
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
  );
  const allSteps = useMemo(() => convertToSteps(messages), [messages]);
  const clarificationContextSteps = useMemo(
    () => allSteps.filter(
      (step): step is CoTAssistantTextStep =>
        step.type === "assistantText" && Boolean(step.clarificationContext),
    ),
    [allSteps],
  );
  const steps = useMemo(
    () => allSteps.filter(
      (step) => step.type !== "assistantText" || !step.clarificationContext,
    ),
    [allSteps],
  );
  const debugStepByMessageId = useMemo(
    () =>
      new Map(
        tokenDebugSteps.map(
          (step) => [step.messageId || step.id, step] as const,
        ),
      ),
    [tokenDebugSteps],
  );
  const toolCallCountByMessageId = useMemo(() => {
    const counts = new Map<string, number>();

    for (const step of steps) {
      if (step.type !== "toolCall" || !step.messageId) {
        continue;
      }

      counts.set(step.messageId, (counts.get(step.messageId) ?? 0) + 1);
    }

    return counts;
  }, [steps]);
  const lastToolCallStep = useMemo(() => {
    const filteredSteps = steps.filter((step) => step.type === "toolCall");
    return filteredSteps[filteredSteps.length - 1];
  }, [steps]);
  const aboveLastToolCallSteps = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(0, index);
    }
    return [];
  }, [lastToolCallStep, steps]);
  const afterLastToolCallAssistantTextSteps = useMemo(() => {
    if (!lastToolCallStep) {
      return [];
    }
    const index = steps.indexOf(lastToolCallStep);
    return steps
      .slice(index + 1)
      .filter((step) => step.type === "assistantText");
  }, [lastToolCallStep, steps]);
  const collapsibleAboveLastToolCallSteps = useMemo(
    () =>
      aboveLastToolCallSteps.filter((step) => step.type !== "assistantText"),
    [aboveLastToolCallSteps],
  );
  const lastReasoningStep = useMemo(() => {
    if (lastToolCallStep) {
      const index = steps.indexOf(lastToolCallStep);
      return steps.slice(index + 1).find((step) => step.type === "reasoning");
    } else {
      const filteredSteps = steps.filter((step) => step.type === "reasoning");
      return filteredSteps[filteredSteps.length - 1];
    }
  }, [lastToolCallStep, steps]);
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const threadMarkdownComponents = useMemo(
    () => ({
      a: createMarkdownLinkComponent(threadId, undefined, t.common.preview),
    }),
    [t.common.preview, threadId],
  );
  const firstEligibleDebugSummaryStepIndexByMessageId = useMemo(() => {
    const firstIndices = new Map<string, number>();

    if (!showTokenDebugSummaries) {
      return firstIndices;
    }

    for (const [index, step] of steps.entries()) {
      const messageId = step.messageId;
      if (!messageId || firstIndices.has(messageId)) {
        continue;
      }

      const debugStep = debugStepByMessageId.get(messageId);
      if (!debugStep) {
        continue;
      }

      const toolCallCount = toolCallCountByMessageId.get(messageId) ?? 0;
      if (!debugStep.sharedAttribution && toolCallCount > 0) {
        continue;
      }
      if (
        !debugStep.sharedAttribution &&
        toolCallCount === 0 &&
        debugStep.label === t.common.thinking &&
        debugStep.secondaryLabels.length === 0
      ) {
        continue;
      }

      firstIndices.set(messageId, index);
    }

    return firstIndices;
  }, [
    debugStepByMessageId,
    showTokenDebugSummaries,
    steps,
    t.common.thinking,
    toolCallCountByMessageId,
  ]);

  const renderDebugSummary = (
    messageId: string | undefined,
    stepIndex: number,
  ) => {
    if (!showTokenDebugSummaries || !messageId) {
      return null;
    }

    const debugStep = debugStepByMessageId.get(messageId);
    if (!debugStep) {
      return null;
    }
    if (
      firstEligibleDebugSummaryStepIndexByMessageId.get(messageId) !== stepIndex
    ) {
      return null;
    }

    return (
      <ChainOfThoughtStep
        key={`token-debug-${messageId}`}
        icon={CoinsIcon}
        label={
          <DebugStepLabel
            label={debugStep.label}
            token={formatDebugToken(debugStep, t)}
          />
        }
        description={
          debugStep.sharedAttribution
            ? t.tokenUsage.sharedAttribution
            : undefined
        }
      >
        {debugStep.secondaryLabels.length > 0 && (
          <ChainOfThoughtSearchResults>
            {debugStep.secondaryLabels.map((label, index) => (
              <ChainOfThoughtSearchResult
                key={`${debugStep.id}-${index}-${label}`}
              >
                {label}
              </ChainOfThoughtSearchResult>
            ))}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  };

  const renderToolCall = (step: CoTToolCallStep) => {
    const debugStep =
      showTokenDebugSummaries && step.messageId
        ? debugStepByMessageId.get(step.messageId)
        : undefined;

    return (
      <ToolCall
        key={step.id}
        {...step}
        threadId={threadId}
        tokenDebugStep={
          debugStep && !debugStep.sharedAttribution ? debugStep : undefined
        }
      />
    );
  };

  const renderAssistantText = (step: CoTAssistantTextStep) => (
    <ChainOfThoughtStep
      key={step.id}
      icon={MessageSquareTextIcon}
      label={
        <MarkdownContent
          content={step.content}
          isLoading={isLoading}
          rehypePlugins={rehypePlugins}
          components={threadMarkdownComponents}
        />
      }
    ></ChainOfThoughtStep>
  );

  const renderStep = (step: CoTStep) => {
    const stepIndex = steps.indexOf(step);
    if (step.type === "assistantText") {
      return [
        renderDebugSummary(step.messageId, stepIndex),
        renderAssistantText(step),
      ];
    }
    if (step.type === "reasoning") {
      return [
        renderDebugSummary(step.messageId, stepIndex),
        <ChainOfThoughtStep
          key={step.id}
          label={
            <MarkdownContent
              content={step.reasoning ?? ""}
              isLoading={isLoading}
              rehypePlugins={rehypePlugins}
              components={threadMarkdownComponents}
            />
          }
        ></ChainOfThoughtStep>,
      ];
    }

    return [
      renderDebugSummary(step.messageId, stepIndex),
      renderToolCall(step),
    ];
  };

  const lastReasoningDebugStep =
    showTokenDebugSummaries && lastReasoningStep?.messageId
      ? debugStepByMessageId.get(lastReasoningStep.messageId)
      : undefined;

  return (
    <>
      {clarificationContextSteps.map((step) => (
        <MarkdownContent
          key={step.id}
          className="mb-2 min-w-0"
          content={step.content}
          isLoading={isLoading}
          rehypePlugins={rehypePlugins}
          components={threadMarkdownComponents}
        />
      ))}
      <ChainOfThought
        className={cn("w-full gap-2 rounded-lg border p-0.5", className)}
        open={true}
      >
        {collapsibleAboveLastToolCallSteps.length > 0 && (
          <Button
            key="above"
            className="w-full items-start justify-start text-left"
            variant="ghost"
            onClick={() => setShowAbove(!showAbove)}
          >
            <ChainOfThoughtStep
              label={
                <span className="opacity-60">
                  {showAbove
                    ? t.toolCalls.lessSteps
                    : t.toolCalls.moreSteps(
                      collapsibleAboveLastToolCallSteps.length,
                    )}
                </span>
              }
              icon={
                <ChevronUp
                  className={cn(
                    "size-4 opacity-60 transition-transform duration-200",
                    showAbove ? "rotate-180" : "",
                  )}
                />
              }
            ></ChainOfThoughtStep>
          </Button>
        )}
        {(lastToolCallStep ??
          steps.some((step) => step.type === "assistantText")) && (
            <ChainOfThoughtContent className="px-4 pb-2">
              {(lastToolCallStep
                ? showAbove
                  ? aboveLastToolCallSteps
                  : aboveLastToolCallSteps.filter(
                    (step) => step.type === "assistantText",
                  )
                : steps.filter((step) => step.type === "assistantText")
              ).flatMap(renderStep)}
              {lastToolCallStep && (
                <>
                  {renderDebugSummary(
                    lastToolCallStep.messageId,
                    steps.indexOf(lastToolCallStep),
                  )}
                  <FlipDisplay uniqueKey={lastToolCallStep.id ?? ""}>
                    {renderToolCall(lastToolCallStep)}
                  </FlipDisplay>
                  {afterLastToolCallAssistantTextSteps.flatMap(renderStep)}
                </>
              )}
            </ChainOfThoughtContent>
          )}
        {lastReasoningStep && (
          <>
            {renderDebugSummary(
              lastReasoningStep.messageId,
              steps.indexOf(lastReasoningStep),
            )}
            <Button
              key={lastReasoningStep.id}
              className="w-full items-start justify-start text-left"
              variant="ghost"
              onClick={() => setShowLastThinking(!showLastThinking)}
            >
              <div className="flex w-full items-center justify-between">
                <ChainOfThoughtStep
                  className="font-normal"
                  label={
                    <DebugStepLabel
                      label={t.common.thinking}
                      token={shouldInlineThinkingToken({
                        debugStep: lastReasoningDebugStep,
                        toolCallCount: lastReasoningStep.messageId
                          ? (toolCallCountByMessageId.get(
                            lastReasoningStep.messageId,
                          ) ?? 0)
                          : 0,
                        enabled: showTokenDebugSummaries,
                        thinkingLabel: t.common.thinking,
                        t,
                      })}
                    />
                  }
                  icon={LightbulbIcon}
                ></ChainOfThoughtStep>
                <div>
                  <ChevronUp
                    className={cn(
                      "text-muted-foreground size-4",
                      showLastThinking ? "" : "rotate-180",
                    )}
                  />
                </div>
              </div>
            </Button>
            {showLastThinking && (
              <ChainOfThoughtContent className="px-4 pb-2">
                <ChainOfThoughtStep
                  key={lastReasoningStep.id}
                  label={
                    <MarkdownContent
                      content={lastReasoningStep.reasoning ?? ""}
                      isLoading={isLoading}
                      rehypePlugins={rehypePlugins}
                      components={threadMarkdownComponents}
                    />
                  }
                ></ChainOfThoughtStep>
              </ChainOfThoughtContent>
            )}
          </>
        )}
      </ChainOfThought>
    </>
  );
}

function formatDebugToken(
  debugStep: TokenDebugStep,
  t: ReturnType<typeof useI18n>["t"],
) {
  return debugStep.usage
    ? `${formatTokenCount(debugStep.usage.totalTokens)} ${t.tokenUsage.label}`
    : t.tokenUsage.unavailableShort;
}

function shouldInlineThinkingToken({
  debugStep,
  toolCallCount,
  enabled,
  thinkingLabel,
  t,
}: {
  debugStep?: TokenDebugStep;
  toolCallCount: number;
  enabled: boolean;
  thinkingLabel: string;
  t: ReturnType<typeof useI18n>["t"];
}) {
  if (
    !enabled ||
    !debugStep ||
    debugStep.sharedAttribution ||
    toolCallCount > 0 ||
    debugStep.label !== thinkingLabel
  ) {
    return null;
  }

  return formatDebugToken(debugStep, t);
}

function DebugStepLabel({
  label,
  token,
}: {
  label: React.ReactNode;
  token?: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">{label}</div>
      {token ? (
        <div className="text-muted-foreground shrink-0 font-mono text-[11px]">
          {token}
        </div>
      ) : null}
    </div>
  );
}

function ToolCall({
  id,
  messageId,
  name,
  args,
  result,
  threadId,
  tokenDebugStep,
}: {
  id?: string;
  messageId?: string;
  name: string;
  args: Record<string, unknown>;
  result?: string | Record<string, unknown>;
  threadId?: string;
  tokenDebugStep?: TokenDebugStep;
}) {
  const { t } = useI18n();
  const { openArtifactPanel, select } = useArtifacts();
  const tokenLabel = tokenDebugStep
    ? formatDebugToken(tokenDebugStep, t)
    : null;
  const resolveLabel = (fallback: React.ReactNode) =>
    tokenDebugStep ? (
      <DebugStepLabel label={tokenDebugStep.label} token={tokenLabel} />
    ) : (
      fallback
    );

  if (name === "web_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedInfo;
    const query = toolDisplayText(args, "query");
    if (query) {
      label = t.toolCalls.searchOnWebFor(query);
    }
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(label)}
        icon={SearchIcon}
      >
        {Array.isArray(result) && (
          <ChainOfThoughtSearchResults>
            {result.map((raw: unknown, index: number) => {
              const item = toolDisplayRecord(raw);
              const title = toolDisplayText(item, "title");
              const url = toolDisplayText(item, "url");
              if (!title && !url) return null;
              return (
                <ChainOfThoughtSearchResult key={`${url ?? "source"}-${index}`}>
                  <ToolResultLink href={url} threadId={threadId} previewLabel={t.common.preview}>
                    {title ?? url}
                  </ToolResultLink>
                </ChainOfThoughtSearchResult>
              );
            })}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "image_search") {
    let label: React.ReactNode = t.toolCalls.searchForRelatedImages;
    const query = toolDisplayText(args, "query");
    if (query) {
      label = t.toolCalls.searchForRelatedImagesFor(query);
    }
    const results = toolDisplayRecord(result).results;
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(label)}
        icon={SearchIcon}
      >
        {Array.isArray(results) && (
          <ChainOfThoughtSearchResults>
            {results.map((raw: unknown, index: number) => {
              const item = toolDisplayRecord(raw);
              const title = toolDisplayText(item, "title") ?? "";
              const thumbnail = toolWebHref(item.thumbnail_url);
              if (!thumbnail || isSandboxInternalURL(thumbnail)) return null;
              return (
                <Tooltip key={`${thumbnail}-${index}`} content={title}>
                  <ToolResultLink
                    className="size-24 overflow-hidden rounded-lg object-cover"
                    href={item.source_url}
                    threadId={threadId}
                    previewLabel={t.common.preview}
                  >
                    <div className="bg-accent size-24">
                      <img
                        className="size-full object-cover"
                        src={thumbnail}
                        alt={title}
                        width={100}
                        height={100}
                      />
                    </div>
                  </ToolResultLink>
                </Tooltip>
              );
            })}
          </ChainOfThoughtSearchResults>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "web_fetch") {
    const url = toolDisplayText(args, "url");
    let title = url;
    if (typeof result === "string") {
      const potentialTitle = extractTitleFromMarkdown(result);
      if (potentialTitle && potentialTitle.toLowerCase() !== "untitled") {
        title = potentialTitle;
      }
    }
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(t.toolCalls.viewWebPage)}
        icon={GlobeIcon}
      >
        <ChainOfThoughtSearchResult>
          {url && (
            <ToolResultLink
              href={url}
              threadId={threadId}
              previewLabel={t.common.preview}
              className="cursor-pointer"
            >
              {title}
            </ToolResultLink>
          )}
        </ChainOfThoughtSearchResult>
      </ChainOfThoughtStep>
    );
  } else if (name === "ls") {
    let description = toolDisplayText(args, "description") ?? "";
    if (description.length === 0) {
      description = t.toolCalls.listFolder;
    }
    const path = toolDisplayText(args, "path");
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={FolderOpenIcon}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "read_file") {
    let description = toolDisplayText(args, "description") ?? "";
    if (description.length === 0) {
      description = t.toolCalls.readFile;
    }
    const path = toolDisplayText(args, "path");
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={BookOpenTextIcon}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "write_file" || name === "str_replace") {
    let description = toolDisplayText(args, "description") ?? "";
    if (description.length === 0) {
      description = t.toolCalls.writeFile;
    }
    const path = toolDisplayText(args, "path");
    return (
      <ChainOfThoughtStep
        key={id}
        className="cursor-pointer"
        label={resolveLabel(description)}
        icon={NotebookPenIcon}
        onClick={() => {
          if (!path) return;
          select(
            new URL(
              `write-file:${path}?message_id=${messageId}&tool_call_id=${id}`,
            ).toString(),
          );
          openArtifactPanel();
        }}
      >
        {path && (
          <ChainOfThoughtSearchResult className="cursor-pointer">
            {path}
          </ChainOfThoughtSearchResult>
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "bash") {
    const description = toolDisplayText(args, "description");
    if (!description) {
      return (
        <ChainOfThoughtStep
          key={id}
          label={resolveLabel(t.toolCalls.executeCommand)}
          icon={SquareTerminalIcon}
        />
      );
    }
    const command = toolDisplayText(args, "command");
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description)}
        icon={SquareTerminalIcon}
      >
        {command && (
          <CodeBlock
            className="mx-0 cursor-pointer border-none px-0"
            showLineNumbers={false}
            language="bash"
            code={command}
          />
        )}
      </ChainOfThoughtStep>
    );
  } else if (name === "ask_clarification") {
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(t.toolCalls.needYourHelp)}
        icon={MessageCircleQuestionMarkIcon}
      ></ChainOfThoughtStep>
    );
  } else if (name === "write_todos") {
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(t.toolCalls.writeTodos)}
        icon={ListTodoIcon}
      ></ChainOfThoughtStep>
    );
  } else {
    const description = toolDisplayText(args, "description");
    return (
      <ChainOfThoughtStep
        key={id}
        label={resolveLabel(description ?? t.toolCalls.useTool(name))}
        icon={WrenchIcon}
      ></ChainOfThoughtStep>
    );
  }
}

interface GenericCoTStep<T extends string = string> {
  id?: string;
  messageId?: string;
  type: T;
}

interface CoTReasoningStep extends GenericCoTStep<"reasoning"> {
  reasoning: string | null;
}

interface CoTToolCallStep extends GenericCoTStep<"toolCall"> {
  name: string;
  args: Record<string, unknown>;
  result?: string;
}

interface CoTAssistantTextStep extends GenericCoTStep<"assistantText"> {
  content: string;
  clarificationContext?: boolean;
}

type CoTStep = CoTAssistantTextStep | CoTReasoningStep | CoTToolCallStep;

function indexToolCallResults(messages: Message[]) {
  const toolCallResults = new Map<string, string>();

  for (const message of messages) {
    if (message.type !== "tool" || !message.tool_call_id) {
      continue;
    }

    const toolCallId = message.tool_call_id;
    if (!toolCallResults.has(toolCallId)) {
      const result = extractTextFromMessage(message);
      if (result) {
        toolCallResults.set(toolCallId, result);
      }
    }
  }

  return toolCallResults;
}
function convertToSteps(messages: Message[]): CoTStep[] {
  const steps: CoTStep[] = [];
  const toolCallResults = indexToolCallResults(messages);
  for (const [messageIndex, message] of messages.entries()) {
    if (message.type === "ai") {
      const content = extractContentFromMessage(message);
      if (content) {
        steps.push({
          id: `${message.id ?? `ai-${messageIndex}`}-content`,
          messageId: message.id,
          type: "assistantText",
          content,
          clarificationContext: message.tool_calls?.some(
            (call) => call.name === "ask_clarification",
          ),
        });
      }
      const reasoning = extractReasoningContentFromMessage(message);
      if (reasoning) {
        const step: CoTReasoningStep = {
          id: message.id,
          messageId: message.id,
          type: "reasoning",
          reasoning,
        };
        steps.push(step);
      }
      for (const tool_call of message.tool_calls ?? []) {
        if (tool_call.name === "task") {
          continue;
        }
        const step: CoTToolCallStep = {
          id: tool_call.id,
          messageId: message.id,
          type: "toolCall",
          name: tool_call.name,
          args: toolDisplayRecord(tool_call.args),
        };
        const toolCallId = tool_call.id;
        if (toolCallId) {
          const toolCallResult = toolCallResults.get(toolCallId);
          if (toolCallResult) {
            try {
              const json = JSON.parse(toolCallResult);
              step.result = json;
            } catch {
              step.result = toolCallResult;
            }
          }
        }
        steps.push(step);
      }
    }
  }
  return steps;
}
