import {
  CalendarClockIcon,
  ListFilterIcon,
  LoaderCircleIcon,
  TriangleAlertIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

/** Only used when there are no visible rows; an unavailable query is not an empty list. */
export function ScheduledTaskListFeedback({
  hasData,
  failed,
  fetching,
  filtered,
  onRetry,
  onClear,
  onCreate,
}: {
  hasData: boolean;
  failed: boolean;
  fetching: boolean;
  filtered: boolean;
  onRetry: () => void;
  onClear: () => void;
  onCreate: () => void;
}) {
  const { t } = useI18n();
  const st = t.scheduledTasks;
  const state = !hasData
    ? failed
      ? "error"
      : "loading"
    : filtered
      ? "filtered"
      : "empty";
  const Icon =
    state === "loading"
      ? LoaderCircleIcon
      : state === "error"
        ? TriangleAlertIcon
        : state === "filtered"
          ? ListFilterIcon
          : CalendarClockIcon;
  const title =
    state === "loading"
      ? st.detail.loading
      : state === "error"
        ? st.detail.loadFailed
        : state === "filtered"
          ? st.detail.noMatchesTitle
          : st.detail.emptyTitle;
  const description =
    state === "loading"
      ? null
      : state === "error"
        ? st.detail.loadFailureDescription
        : state === "filtered"
          ? st.detail.noMatchesDescription
          : st.detail.emptyDescription;
  return (
    <div
      data-testid="scheduled-task-list"
      className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto rounded-md border border-dashed bg-white/75 p-5 text-center shadow-sm sm:p-10"
    >
      <div className="max-w-md" role={state === "error" ? "alert" : "status"}>
        <div className="mx-auto flex size-12 items-center justify-center rounded-md bg-blue-50 text-blue-700">
          <Icon
            aria-hidden="true"
            className={
              state === "loading" ? "size-6 motion-safe:animate-spin" : "size-6"
            }
          />
        </div>
        <h2 className="mt-4 text-base font-semibold sm:text-lg">{title}</h2>
        {description && (
          <p className="text-muted-foreground mt-2 text-sm leading-6">
            {description}
          </p>
        )}
        {state !== "loading" && (
          <Button
            className="mt-5"
            variant={state === "empty" ? "default" : "outline"}
            disabled={state === "error" && fetching}
            onClick={
              state === "error"
                ? onRetry
                : state === "filtered"
                  ? onClear
                  : onCreate
            }
          >
            {state === "error"
              ? st.detail.retry
              : state === "filtered"
                ? st.filters.clear
                : st.create.title}
          </Button>
        )}
      </div>
    </div>
  );
}
