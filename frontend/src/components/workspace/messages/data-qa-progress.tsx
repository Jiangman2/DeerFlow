"use client";

import { DatabaseIcon, LoaderCircleIcon } from "lucide-react";
import { useContext } from "react";

import { ChainOfThoughtStep } from "@/components/ai-elements/chain-of-thought";
import { dataQAProgressCopy } from "@/core/i18n/data-qa-progress";
import { useI18n } from "@/core/i18n/hooks";
import {
  dataQAResultStage,
  isDataQARunning,
  type DataQAProgress,
} from "@/core/threads/data-qa-progress";

import { ThreadContext } from "./context";

export function DataQAProgressView({
  progress,
  result,
  isLoading,
}: {
  progress?: DataQAProgress;
  result?: unknown;
  isLoading: boolean;
}) {
  const { locale } = useI18n();
  const copy = dataQAProgressCopy(locale);
  const observed = progress?.stages.at(-1);
  const resultStage = dataQAResultStage(result);
  const stage =
    resultStage ??
    (observed && (!isDataQARunning(observed) || isLoading)
      ? observed
      : isLoading
        ? "waiting"
        : "stopped");
  const active = isDataQARunning(stage);
  return (
    <ChainOfThoughtStep
      icon={
        active ? (
          <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" />
        ) : (
          DatabaseIcon
        )
      }
      status={active ? "active" : "complete"}
      label={
        <span role="status" aria-live="polite">
          {copy.stages[stage]}
        </span>
      }
    >
      {progress && progress.stages.length > 1 && (
        <details className="min-w-0 text-xs break-words">
          <summary className="cursor-pointer py-2">{copy.details}</summary>
          <ol className="space-y-1 pl-3">
            {progress.stages.map((step) => (
              <li key={step}>{copy.stages[step]}</li>
            ))}
          </ol>
        </details>
      )}
    </ChainOfThoughtStep>
  );
}

export function DataQAProgressStep({
  id,
  threadId,
  result,
  isLoading,
}: {
  id?: string;
  threadId?: string;
  result?: unknown;
  isLoading: boolean;
}) {
  const context = useContext(ThreadContext);
  const progress =
    context?.thread.dataQAProgress?.[JSON.stringify([threadId, id])];
  return (
    <DataQAProgressView
      progress={progress}
      result={result}
      isLoading={isLoading}
    />
  );
}
