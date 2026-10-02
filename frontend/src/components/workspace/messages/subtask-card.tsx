import {
  CheckCircleIcon,
  ChevronRightIcon,
  ChevronUp,
  ClipboardListIcon,
  Loader2Icon,
  SparklesIcon,
  WrenchIcon,
  XCircleIcon,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
} from "@/components/ai-elements/chain-of-thought";
import { Shimmer } from "@/components/ai-elements/shimmer";
import { Button } from "@/components/ui/button";
import { ShineBorder } from "@/components/ui/shine-border";
import {
  containsLivePreviewURL,
  maskSandboxURLDestinations,
} from "@/core/artifacts/live-preview";
import { useI18n } from "@/core/i18n/hooks";
import { pptRuntimeUsageCopy } from "@/core/i18n/ppt-runtime-usage";
import { hasToolCalls } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import { streamdownPluginsWithWordAnimation } from "@/core/streamdown";
import { SafeStreamdown } from "@/core/streamdown/components";
import { fetchSubtaskSteps } from "@/core/tasks/api";
import { useSubtask, useUpdateSubtask } from "@/core/tasks/context";
import {
  normalizePptUsageObservation,
  type PptUsageCarrier,
} from "@/core/tasks/ppt-usage-observation";
import {
  formatSubtaskTokenUsage,
  resolveSubtaskModelLabel,
} from "@/core/tasks/presentation";
import { stepsForDisplay } from "@/core/tasks/steps";
import type { SubtaskStep } from "@/core/tasks/steps";
import { explainLastToolCall } from "@/core/tools/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";
import { FlipDisplay } from "../flip-display";

import { MarkdownContent, type MarkdownContentProps } from "./markdown-content";
import { createMarkdownLinkComponent } from "./markdown-link";

const STEP_SUMMARY_MAX_CHARS = 180;
const INITIAL_VISIBLE_STEP_COUNT = 80;
const VISIBLE_STEP_INCREMENT = 80;

function compactStepText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function boundedStepSummary(value: string): string {
  const compact = compactStepText(value);
  return compact.length > STEP_SUMMARY_MAX_CHARS
    ? `${compact.slice(0, STEP_SUMMARY_MAX_CHARS - 1).trimEnd()}…`
    : compact;
}

function stringifyToolArgs(args: unknown): string {
  if (typeof args === "string") {
    return args;
  }
  try {
    return JSON.stringify(args ?? {}, null, 2);
  } catch {
    if (args == null) {
      return "";
    }
    switch (typeof args) {
      case "number":
      case "boolean":
      case "bigint":
      case "symbol":
        return String(args);
      default:
        return "[Unserializable tool arguments]";
    }
  }
}

function toolCallHint(step: SubtaskStep): string {
  for (const call of step.tool_calls ?? []) {
    if (!call.args || typeof call.args !== "object") {
      continue;
    }
    const args = call.args as Record<string, unknown>;
    for (const key of ["command", "query", "path", "url", "pattern"]) {
      const value = args[key];
      if (typeof value === "string" && value.trim()) {
        return boundedStepSummary(value);
      }
    }
  }
  return "";
}

function stepSummary(step: SubtaskStep): string {
  if (step.kind === "ai" && step.tool_calls?.length) {
    const names = step.tool_calls
      .map((call) => call.name)
      .filter((name): name is string => Boolean(name));
    const toolNames = [...new Set(names)].join(" + ") || "tool";
    const hint = toolCallHint(step);
    return hint ? `${toolNames} · ${hint}` : toolNames;
  }

  if (step.kind === "tool") {
    const output = boundedStepSummary(step.text);
    return output
      ? `${step.tool_name ?? "tool"} · ${output}`
      : (step.tool_name ?? "tool");
  }

  return boundedStepSummary(step.text);
}

const SubtaskTimelineStep = memo(function SubtaskTimelineStep({
  step,
  isLastWhileRunning,
  markdownComponents,
  rehypePlugins,
  viewDetailsLabel,
  hideDetailsLabel,
  toolInputLabel,
  toolOutputLabel,
  previewLabel,
}: {
  step: SubtaskStep;
  isLastWhileRunning: boolean;
  markdownComponents: MarkdownContentProps["components"];
  rehypePlugins: MarkdownContentProps["rehypePlugins"];
  viewDetailsLabel: string;
  hideDetailsLabel: string;
  toolInputLabel: string;
  toolOutputLabel: string;
  previewLabel: string;
}) {
  const containsLivePreview = containsLivePreviewURL(step.text);
  const [expanded, setExpanded] = useState(containsLivePreview);
  const hasToolInput = step.kind === "ai" && Boolean(step.tool_calls?.length);
  const hasDetails = hasToolInput || Boolean(step.text.trim());
  const summary = maskSandboxURLDestinations(stepSummary(step), previewLabel);
  const icon = isLastWhileRunning ? (
    <Loader2Icon className="size-4 animate-spin" />
  ) : step.kind === "tool" ? (
    <WrenchIcon className="size-4" />
  ) : (
    <SparklesIcon className="size-4" />
  );

  useEffect(() => {
    if (containsLivePreview) {
      setExpanded(true);
    }
  }, [containsLivePreview]);

  return (
    <ChainOfThoughtStep
      className="[contain-intrinsic-size:40px] [content-visibility:auto]"
      icon={icon}
      label={
        <div className="min-w-0">
          <button
            type="button"
            className={cn(
              "hover:bg-muted/60 flex w-full min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors",
              !hasDetails && "cursor-default hover:bg-transparent",
            )}
            disabled={!hasDetails}
            aria-expanded={hasDetails ? expanded : undefined}
            aria-label={`${expanded ? hideDetailsLabel : viewDetailsLabel}: ${summary}`}
            onClick={() => setExpanded((value) => !value)}
          >
            <span className="min-w-0 flex-1 truncate" title={summary}>
              {summary}
            </span>
            {hasDetails && (
              <>
                <span className="text-muted-foreground hidden shrink-0 text-xs sm:inline">
                  {expanded ? hideDetailsLabel : viewDetailsLabel}
                </span>
                <ChevronRightIcon
                  className={cn(
                    "text-muted-foreground size-3.5 shrink-0 transition-transform",
                    expanded && "rotate-90",
                  )}
                />
              </>
            )}
          </button>

          {expanded && (
            <div className="border-border/70 bg-muted/25 mt-2 min-w-0 space-y-3 overflow-hidden rounded-md border p-3">
              {step.kind === "ai" && step.text.trim() && (
                <MarkdownContent
                  content={step.text}
                  isLoading={false}
                  rehypePlugins={rehypePlugins}
                  components={markdownComponents}
                />
              )}
              {hasToolInput && (
                <div className="space-y-2">
                  <div className="text-muted-foreground text-xs font-medium">
                    {toolInputLabel}
                  </div>
                  {(step.tool_calls ?? []).map((call, index) => (
                    <div key={`${call.name ?? "tool"}-${index}`}>
                      <div className="mb-1 text-xs font-medium">
                        {call.name ?? "tool"}
                      </div>
                      <pre className="bg-background max-h-80 overflow-auto rounded-md border p-3 text-xs break-words whitespace-pre-wrap">
                        {stringifyToolArgs(call.args)}
                      </pre>
                    </div>
                  ))}
                </div>
              )}
              {step.kind === "tool" && (
                <div className="space-y-2">
                  <div className="text-muted-foreground text-xs font-medium">
                    {toolOutputLabel}
                  </div>
                  {containsLivePreview ? (
                    <MarkdownContent
                      content={step.text}
                      isLoading={false}
                      rehypePlugins={rehypePlugins}
                      components={markdownComponents}
                    />
                  ) : (
                    <pre className="bg-background max-h-96 overflow-auto rounded-md border p-3 text-xs break-words whitespace-pre-wrap">
                      {step.text}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      }
    />
  );
});

export function SubtaskCard({
  className,
  taskId,
  threadId,
  runId,
  isLoading,
}: {
  className?: string;
  taskId: string;
  threadId?: string;
  runId?: string;
  isLoading: boolean;
}) {
  const { t, locale } = useI18n();
  const [collapsed, setCollapsed] = useState(true);
  const [visibleStepCount, setVisibleStepCount] = useState(
    INITIAL_VISIBLE_STEP_COUNT,
  );
  const rehypePlugins = useRehypeSplitWordsIntoSpans(isLoading);
  const task = useSubtask(taskId)!;
  const { models, tokenUsageEnabled } = useModels();
  const updateSubtask = useUpdateSubtask();
  const observation = tokenUsageEnabled
    ? normalizePptUsageObservation(
        (task as typeof task & PptUsageCarrier).sessionUsageObservation,
      )
    : undefined;
  const observationCopy = pptRuntimeUsageCopy(locale);
  const modelLabel = resolveSubtaskModelLabel(task.modelName, models);
  const tokenLabel = tokenUsageEnabled
    ? formatSubtaskTokenUsage(task.usage)
    : undefined;
  const runtimeUsageLabel = tokenUsageEnabled
    ? tokenLabel
      ? `${tokenLabel} ${t.tokenUsage.label}`
      : task.status === "in_progress"
        ? t.tokenUsage.collecting
        : task.status === "incomplete"
          ? undefined
          : t.tokenUsage.unavailableShort
    : undefined;

  // The card shows the subagent's step timeline (#3779): its reasoning turns
  // (AI text) interleaved with the tools it ran (by name). See stepsForDisplay
  // for what is kept/dropped.
  const displaySteps = useMemo(
    () => stepsForDisplay(task.steps, task.status),
    [task.steps, task.status],
  );
  const hiddenStepCount = Math.max(0, displaySteps.length - visibleStepCount);
  const visibleSteps =
    hiddenStepCount > 0 ? displaySteps.slice(hiddenStepCount) : displaySteps;
  const threadMarkdownComponents = useMemo(
    () => ({
      a: createMarkdownLinkComponent(threadId, undefined, t.common.preview),
    }),
    [t.common.preview, threadId],
  );

  // Backfill step history on expand for historical runs (#3779). Live runs
  // already have steps from SSE, so the `steps.length` guard skips the fetch.
  const stepsCount = task.steps?.length ?? 0;
  const backfilledRef = useRef(false);
  useEffect(() => {
    setVisibleStepCount(INITIAL_VISIBLE_STEP_COUNT);
  }, [taskId]);

  useEffect(() => {
    if (collapsed || backfilledRef.current || stepsCount > 0) {
      return;
    }
    if (!threadId || !runId) {
      return;
    }
    backfilledRef.current = true;
    const controller = new AbortController();
    fetchSubtaskSteps(threadId, runId, taskId, undefined, controller.signal)
      .then((steps) => {
        if (steps.length > 0) {
          updateSubtask({ id: taskId, steps });
        }
      })
      .catch(() => {
        if (controller.signal.aborted) {
          return;
        }
        // Allow a retry on the next expand if the fetch failed.
        backfilledRef.current = false;
      });

    return () => {
      // A route change can unmount this card while its historical event pages
      // are still in flight. Abort them so their late result cannot repopulate
      // the next conversation's scoped task store.
      controller.abort();
    };
  }, [collapsed, stepsCount, threadId, runId, taskId, updateSubtask]);
  const icon = useMemo(() => {
    if (task.status === "completed") {
      return <CheckCircleIcon className="size-3" />;
    } else if (task.status === "failed") {
      return <XCircleIcon className="size-3 text-red-500" />;
    } else if (task.status === "incomplete") {
      return <XCircleIcon className="text-muted-foreground size-3" />;
    } else if (task.status === "in_progress") {
      return <Loader2Icon className="size-3 animate-spin" />;
    }
  }, [task.status]);
  return (
    <ChainOfThought
      className={cn(
        "relative max-w-full min-w-0 gap-2 overflow-hidden rounded-lg border py-0",
        className,
      )}
      open={!collapsed}
    >
      <div
        className={cn(
          "ambilight z-[-1]",
          task.status === "in_progress" ? "enabled" : "",
        )}
      ></div>
      {task.status === "in_progress" && (
        <>
          <ShineBorder
            borderWidth={1.5}
            shineColor={["#1f70c1", "#36a5cf", "#74c8e5"]}
          />
        </>
      )}
      <div className="bg-background/95 flex w-full min-w-0 flex-col rounded-lg">
        <div className="flex w-full min-w-0 items-center justify-between p-0.5">
          <Button
            className="h-auto w-full min-w-0 items-start justify-start overflow-hidden text-left"
            variant="ghost"
            onClick={() => setCollapsed(!collapsed)}
          >
            <div className="flex w-full min-w-0 items-center justify-between gap-3">
              <ChainOfThoughtStep
                className="min-w-0 flex-1 font-normal"
                label={
                  task.status === "in_progress" ? (
                    <span
                      className="block min-w-0 truncate"
                      title={task.description}
                    >
                      <Shimmer duration={3} spread={3}>
                        {task.description}
                      </Shimmer>
                    </span>
                  ) : (
                    <span
                      className="block min-w-0 truncate"
                      title={task.description}
                    >
                      {task.description}
                    </span>
                  )
                }
                icon={<ClipboardListIcon />}
              ></ChainOfThoughtStep>
              <div className="flex max-w-[55%] min-w-0 shrink-0 items-center gap-1 overflow-hidden">
                <div
                  className={cn(
                    "text-muted-foreground flex min-w-0 items-center gap-1 overflow-hidden text-xs font-normal",
                    task.status === "failed" ? "text-red-500 opacity-67" : "",
                  )}
                >
                  {modelLabel && (
                    <span className="max-w-32 truncate" title={modelLabel}>
                      {modelLabel}
                    </span>
                  )}
                  {runtimeUsageLabel && (
                    <span
                      className="max-w-28 truncate"
                      title={runtimeUsageLabel}
                    >
                      {runtimeUsageLabel}
                    </span>
                  )}
                  {collapsed && (
                    <>
                      {icon}
                      <FlipDisplay
                        className="max-w-[420px] truncate pb-1"
                        uniqueKey={task.latestMessage?.id ?? ""}
                      >
                        {task.status === "in_progress" &&
                        task.latestMessage &&
                        hasToolCalls(task.latestMessage)
                          ? explainLastToolCall(task.latestMessage, t)
                          : t.subtasks[task.status]}
                      </FlipDisplay>
                    </>
                  )}
                </div>
                <ChevronUp
                  className={cn(
                    "text-muted-foreground size-4 shrink-0",
                    !collapsed ? "" : "rotate-180",
                  )}
                />
              </div>
            </div>
          </Button>
        </div>
        {observation && (
          <div className="text-muted-foreground space-y-1 border-t px-4 py-2 text-xs">
            <p>{observationCopy.note}</p>
            <p>
              {observationCopy.parent}:{" "}
              {observation.parent
                ? observation.parent.usage.total_tokens.toLocaleString(locale)
                : observationCopy.unknown}
            </p>
            {observation.workers.map((worker, index) => (
              <p key={worker.session_id_hash}>
                {observationCopy.worker} {index + 1}:{" "}
                {worker.usage.total_tokens.toLocaleString(locale)}
              </p>
            ))}
            <p>
              {observationCopy.coverage(
                observation.workers.length,
                observation.worker_count,
              )}
            </p>
            {observation.partial && <p>{observationCopy.partial}</p>}
          </div>
        )}
        <ChainOfThoughtContent className="max-h-[65vh] overflow-y-auto overscroll-contain px-4 pr-2 pb-4">
          {task.prompt && (
            <ChainOfThoughtStep
              label={
                <SafeStreamdown
                  {...streamdownPluginsWithWordAnimation}
                  components={{ a: CitationLink }}
                >
                  {task.prompt}
                </SafeStreamdown>
              }
            ></ChainOfThoughtStep>
          )}
          {hiddenStepCount > 0 && (
            <ChainOfThoughtStep
              label={
                <Button
                  className="h-auto px-2 py-1 text-xs"
                  variant="ghost"
                  onClick={() =>
                    setVisibleStepCount(
                      (count) => count + VISIBLE_STEP_INCREMENT,
                    )
                  }
                >
                  {t.toolCalls.moreSteps(hiddenStepCount)}
                </Button>
              }
            />
          )}
          {visibleSteps.map((step, i) => {
            const isLastWhileRunning =
              task.status === "in_progress" && i === visibleSteps.length - 1;
            return (
              <SubtaskTimelineStep
                key={`${step.message_index}-${i}`}
                step={step}
                isLastWhileRunning={isLastWhileRunning}
                markdownComponents={threadMarkdownComponents}
                rehypePlugins={rehypePlugins}
                viewDetailsLabel={t.subtasks.viewStepDetails}
                hideDetailsLabel={t.subtasks.hideStepDetails}
                toolInputLabel={t.subtasks.toolInput}
                toolOutputLabel={t.subtasks.toolOutput}
                previewLabel={t.common.preview}
              />
            );
          })}
          {task.status === "completed" && (
            <>
              <ChainOfThoughtStep
                label={t.subtasks.completed}
                icon={<CheckCircleIcon className="size-4" />}
              ></ChainOfThoughtStep>
              <ChainOfThoughtStep
                label={
                  task.result ? (
                    <MarkdownContent
                      content={task.result}
                      isLoading={false}
                      rehypePlugins={rehypePlugins}
                      components={threadMarkdownComponents}
                    />
                  ) : null
                }
              ></ChainOfThoughtStep>
            </>
          )}
          {task.status === "failed" && (
            <ChainOfThoughtStep
              label={<div className="text-red-500">{task.error}</div>}
              icon={<XCircleIcon className="size-4 text-red-500" />}
            ></ChainOfThoughtStep>
          )}
          {task.status === "incomplete" && (
            <ChainOfThoughtStep
              label={
                <div className="text-muted-foreground">
                  {t.subtasks.incomplete}
                </div>
              }
              icon={<XCircleIcon className="text-muted-foreground size-4" />}
            ></ChainOfThoughtStep>
          )}
        </ChainOfThoughtContent>
      </div>
    </ChainOfThought>
  );
}
