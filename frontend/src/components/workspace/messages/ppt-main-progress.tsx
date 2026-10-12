"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import {
  pptProgressCopy,
  pptSummaryValue,
} from "@/core/i18n/ppt-progress-copy";
import { fetchSubtaskSteps } from "@/core/tasks/api";
import {
  confirmationOwner,
  executionNotice,
  officialConfirmation,
  previewPorts,
  sameOwnerVersion,
  type PublicStep,
} from "@/core/tasks/ppt-confirmation";
import type { Subtask } from "@/core/tasks/types";

import { MarkdownContent } from "./markdown-content";
import { createMarkdownLinkComponent } from "./markdown-link";

async function boundedJSON(url: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(
    url,
    { signal, method: "GET", cache: "no-store", redirect: "error" },
    { redirectOnUnauthorized: false },
  );
  if (
    !response.ok ||
    !response.headers.get("content-type")?.includes("application/json") ||
    !response.body
  )
    throw new Error("confirmation_unavailable");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let size = 0,
    text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 256 * 1024) throw new Error("confirmation_size");
      text += decoder.decode(value, { stream: true });
    }
    return JSON.parse(text + decoder.decode()) as unknown;
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

/** Always mounted OUTSIDE the collapsible subtask. This is a read-only mirror;
 * confirmation is submitted only through the existing official protected page.
 */
export function PptMainProgress({
  task,
  threadId,
}: {
  task: Subtask;
  threadId: string;
}) {
  const { locale, t } = useI18n();
  const copy = pptProgressCopy(locale);
  const noticeCode = executionNotice(task);
  const notice = noticeCode ? copy.notices[noticeCode] : null;
  const stepsQuery = useQuery({
    queryKey: ["ppt-main-steps", threadId, task.id],
    queryFn: ({ signal }) =>
      fetchSubtaskSteps(threadId, task.id, task.id, undefined, signal),
    staleTime: task.status === "in_progress" ? 8000 : Infinity,
    retry: 1,
    refetchInterval: (query) =>
      task.status === "in_progress" && query.state.errorUpdateCount < 3
        ? 12000
        : false,
    refetchIntervalInBackground: false,
  });
  const steps = useMemo(() => {
    const byIndex = new Map<number, PublicStep>();
    for (const step of [...(stepsQuery.data ?? []), ...(task.steps ?? [])])
      byIndex.set(step.message_index, step);
    return [...byIndex.values()].sort(
      (a, b) => a.message_index - b.message_index,
    );
  }, [stepsQuery.data, task.steps]);
  const owner = useMemo(() => confirmationOwner(steps), [steps]);
  const ports = useMemo(() => previewPorts(steps), [steps]);
  const base = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/live-preview/`;
  const project = owner?.project,
    port = owner?.port;
  const confirmation = useQuery({
    queryKey: ["ppt-confirmation", threadId, task.id, project, port],
    enabled: Boolean(project && port),
    queryFn: async ({ signal }) => {
      if (!project || !port) throw new Error("no_owner");
      const controller = new AbortController();
      const cancel = () => controller.abort();
      signal.addEventListener("abort", cancel, { once: true });
      if (signal.aborted) controller.abort();
      const timeout = setTimeout(cancel, 15000);
      try {
        const health = await boundedJSON(
          `${base}${port}/api/health`,
          controller.signal,
        );
        const view = officialConfirmation(health, project);
        if (view.state !== "needs_confirmation") return view;
        const recommendation = await boundedJSON(
          `${base}${port}/api/recommendations`,
          controller.signal,
        );
        // Sequential by design: a submission/version change during the read
        // invalidates the old card instead of resurrecting a stale waiting gate.
        const after = officialConfirmation(
          await boundedJSON(`${base}${port}/api/health`, controller.signal),
          project,
        );
        if (!sameOwnerVersion(view, after))
          return after.state === "needs_confirmation"
            ? Promise.reject(new Error("owner_changed"))
            : after;
        return officialConfirmation(health, project, recommendation);
      } finally {
        clearTimeout(timeout);
        signal.removeEventListener("abort", cancel);
      }
    },
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: true,
    refetchInterval: (query) =>
      task.status === "in_progress" && query.state.errorUpdateCount < 3
        ? 8000
        : false,
    refetchIntervalInBackground: false,
  });
  // A failed fresh read may not keep an old actionable waiting state visible.
  const view = confirmation.isError ? undefined : confirmation.data;
  const progress = [...steps]
    .reverse()
    .find((step) => step.kind === "ai" && step.text.trim())?.text;
  const markdownComponents = useMemo(
    () => ({
      a: createMarkdownLinkComponent(threadId, undefined, t.common.preview),
    }),
    [threadId, t.common.preview],
  );
  const waiting = view?.state === "needs_confirmation";
  const stage =
    view?.stage === "stage1"
      ? "Stage 1"
      : view?.stage === "stage2"
        ? "Stage 2"
        : "";
  let title = copy.progress;
  if (waiting) title = copy.confirm(stage);
  else if (view?.state === "preparing")
    title =
      view.confirmedStage === "stage1" ? copy.stage1Received : copy.preparing;
  else if (view?.state === "confirmed") title = copy.confirmed;
  else if (view?.state === "owner_error") title = copy.ownerError;
  return (
    <section
      className="bg-background relative space-y-3 rounded-lg border p-4"
      data-testid="ppt-main-progress"
      aria-label={copy.region}
    >
      {notice && (
        <div
          role={noticeCode === "turn_ended" ? "status" : "alert"}
          data-testid="ppt-execution-notice"
        >
          <h3 className="font-semibold">{notice.title}</h3>
          <p>{notice.detail}</p>
        </div>
      )}
      <h3 className="font-semibold" aria-live="polite">
        {title}
      </h3>
      {waiting && <p>{copy.review}</p>}
      {view?.summary.length ? (
        <dl className="grid gap-2 text-sm">
          {view.summary.map(({ key, value }) => (
            <div key={key} className="min-w-0">
              <dt className="font-medium">
                {copy.labels[key] ?? key.replaceAll("_", " ")}
              </dt>
              <dd className="break-words whitespace-pre-wrap">
                {pptSummaryValue(copy, key, value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {confirmation.isError && <p role="alert">{copy.unavailable}</p>}
      <div className="flex flex-wrap gap-3">
        {ports.map((p) => (
          <a
            key={p}
            className="text-primary underline"
            href={`${base}${p}/`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {p === port ? copy.confirmation : copy.preview}
            {ports.length > 1 ? ` (${p})` : ""}
          </a>
        ))}
        {owner && (
          <button
            type="button"
            className="text-primary underline"
            disabled={confirmation.isFetching}
            onClick={() => {
              void confirmation.refetch();
            }}
          >
            {copy.refresh}
          </button>
        )}
      </div>
      {progress && (
        <div className="min-w-0 border-t pt-3">
          <p className="text-muted-foreground mb-1 text-xs">{copy.latest}</p>
          <MarkdownContent
            content={progress}
            isLoading={false}
            components={markdownComponents}
          />
        </div>
      )}
      {!owner && (
        <p className="text-muted-foreground text-sm">{copy.noOwner}</p>
      )}
    </section>
  );
}
