"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { usePptConfirmationEnabled } from "@/core/features/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { pptConfirmationMessages } from "@/core/i18n/ppt-confirmation";
import {
  fetchConfirmationState,
  openConfirmationPage,
  stableConfirmationEntry,
} from "@/core/ppt-confirmation/api";

export function PptConfirmationState({
  threadId,
  taskId,
  entry = false,
}: {
  threadId: string;
  taskId: string;
  entry?: boolean;
}) {
  const { locale } = useI18n();
  const copy = pptConfirmationMessages(locale);
  const available = usePptConfirmationEnabled(true);
  const [opening, setOpening] = useState(false);
  const [openFailed, setOpenFailed] = useState(false);
  const query = useQuery({
    queryKey: ["ppt-durable-confirmation", threadId, taskId],
    enabled: entry || available,
    queryFn: ({ signal }) => fetchConfirmationState(threadId, taskId, signal),
    retry: false,
    staleTime: 0,
    gcTime: 0,
    refetchInterval: (q) =>
      q.state.data?.run_active && q.state.errorUpdateCount < 3 ? 5000 : false,
    refetchIntervalInBackground: false,
  });
  // Legacy cards remain unchanged until both server availability and explicit
  // task admission exist. The stable page still reads history after disable.
  if (!entry && (!available || !query.data?.admitted)) return null;
  const state = query.isError ? undefined : query.data;
  const gate = state?.gate;
  let description = copy.preparing;
  if (query.isPending) description = copy.loading;
  else if (query.isError) description = copy.unavailableState;
  else if (!state?.admitted) description = copy.legacy;
  else if (!state.run_active || state.cancelled) description = copy.inactive;
  else if (gate?.state === "waiting_user") description = copy.waiting;
  else if (gate?.state === "confirmed") description = copy.confirmed;
  else if (gate?.state === "continuing") description = copy.continuing;
  else if (gate?.state.includes("pending") || gate?.state.includes("unknown"))
    description = copy.uncertain;
  const automatic =
    state?.auto_enabled &&
    gate?.state === "waiting_user" &&
    gate.due_at !== null &&
    gate.auto_block_reason === null;
  async function open() {
    setOpening(true);
    setOpenFailed(false);
    try {
      window.location.assign(await openConfirmationPage(threadId, taskId));
    } catch {
      setOpenFailed(true);
    } finally {
      setOpening(false);
      void query.refetch();
    }
  }
  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-lg border p-4"
      aria-label={copy.title}
      data-testid="ppt-durable-confirmation"
    >
      <h3 className="font-semibold">{copy.title}</h3>
      <p role="status">{description}</p>
      {state?.run_active && gate?.state === "waiting_user" && (
        <p>{automatic ? copy.automaticWait : copy.manualWait}</p>
      )}
      {openFailed && <p role="alert">{copy.openFailed}</p>}
      <div className="flex flex-wrap items-center gap-3">
        {!entry && (
          <a
            className="text-primary underline"
            href={stableConfirmationEntry(threadId, taskId)}
            target="_blank"
            rel="noopener noreferrer"
          >
            {copy.entry}
          </a>
        )}
        {entry && state?.can_open && (
          <Button
            type="button"
            disabled={opening || gate?.state !== "waiting_user"}
            onClick={() => void open()}
          >
            {opening ? copy.opening : copy.takeover}
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={query.isFetching || opening}
          onClick={() => void query.refetch()}
        >
          {copy.refresh}
        </Button>
        {entry && (
          <a
            className="text-primary underline"
            href={`/workspace/chats/${encodeURIComponent(threadId)}`}
          >
            {copy.back}
          </a>
        )}
      </div>
    </section>
  );
}
