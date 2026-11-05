"use client";

import {
  ArrowUpRightIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  FileDiffIcon,
} from "lucide-react";
import { useState } from "react";

import { Sheet } from "@/components/ui/sheet";
import { useI18n } from "@/core/i18n/hooks";
import { useWorkspaceChanges } from "@/core/workspace-changes/hooks";
import {
  getChangedFileCount,
  sortWorkspaceChanges,
} from "@/core/workspace-changes/summary";
import type { WorkspaceFileChange } from "@/core/workspace-changes/types";
import { cn } from "@/lib/utils";

import { WorkspaceChangePanel } from "./workspace-change-panel";

const COLLAPSED_FILE_COUNT = 3;

export function WorkspaceChangeBadge({
  threadId,
  runId,
  disabled,
}: {
  threadId: string;
  runId?: string;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<string>();
  const { data, isLoading } = useWorkspaceChanges({
    threadId,
    runId,
    includeFiles: true,
    includeDiff: false,
    enabled: Boolean(runId) && !disabled,
  });

  if (!runId || !data?.available) {
    return null;
  }

  const count = getChangedFileCount(data.summary);
  if (count === 0) {
    return null;
  }

  const files = sortWorkspaceChanges(data.files);
  const expanded = expandedRunId === runId;
  const visibleFiles = expanded ? files : files.slice(0, COLLAPSED_FILE_COUNT);
  const hiddenFileCount = Math.max(0, files.length - COLLAPSED_FILE_COUNT);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <div className="border-border/70 bg-muted/20 mt-3 overflow-hidden rounded-xl border">
        <div className="border-border/70 flex items-center justify-between gap-3 border-b p-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="bg-background/80 flex size-10 shrink-0 items-center justify-center rounded-lg">
              <FileDiffIcon className="text-muted-foreground size-4" />
            </div>
            <div className="min-w-0">
              <div className="text-foreground text-sm font-semibold">
                {t.workspaceChanges.editedTitle(count)}
              </div>
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground mt-0.5 inline-flex items-center gap-1 text-xs font-medium transition-colors"
                onClick={() => setOpen(true)}
              >
                {t.workspaceChanges.viewChanges}
                <ArrowUpRightIcon className="size-3" />
              </button>
            </div>
          </div>
          <SummaryDelta
            additions={data.summary.additions}
            deletions={data.summary.deletions}
            className="hidden text-xs font-semibold sm:inline-flex"
          />
        </div>

        <div className="py-1">
          {isLoading && (
            <div className="text-muted-foreground px-3 py-2 text-xs">
              {t.workspaceChanges.loading}
            </div>
          )}
          {!isLoading &&
            visibleFiles.map((file) => (
              <WorkspaceChangeSummaryRow
                key={`${file.status}:${file.path}`}
                file={file}
              />
            ))}
          {!isLoading && hiddenFileCount > 0 ? (
            <button
              type="button"
              aria-expanded={expanded}
              data-testid="workspace-change-file-toggle"
              className="border-border/70 text-foreground hover:bg-muted/40 flex w-full items-center gap-1 border-t px-3 py-2.5 text-left text-sm font-medium transition-colors"
              onClick={() =>
                setExpandedRunId((current) =>
                  current === runId ? undefined : runId,
                )
              }
            >
              {expanded
                ? t.workspaceChanges.showFewerFiles
                : t.workspaceChanges.showMoreFiles(hiddenFileCount)}
              {expanded ? (
                <ChevronUpIcon aria-hidden="true" className="size-4" />
              ) : (
                <ChevronDownIcon aria-hidden="true" className="size-4" />
              )}
            </button>
          ) : null}
        </div>
      </div>
      <WorkspaceChangePanel
        threadId={threadId}
        runId={runId}
        fallbackSummary={data.summary}
        open={open}
      />
    </Sheet>
  );
}

function WorkspaceChangeSummaryRow({ file }: { file: WorkspaceFileChange }) {
  const pathParts = formatWorkspacePath(file.path);

  return (
    <div
      data-testid="workspace-change-summary-row"
      className="flex items-center justify-between gap-3 px-3 py-2.5"
    >
      <div className="min-w-0 truncate text-sm" title={file.path}>
        {pathParts.dirname && (
          <span className="text-muted-foreground">{pathParts.dirname}/</span>
        )}
        <span className="text-foreground font-medium">
          {pathParts.basename}
        </span>
      </div>
      <SummaryDelta
        additions={file.additions}
        deletions={file.deletions}
        className="text-sm font-semibold"
      />
    </div>
  );
}

function SummaryDelta({
  additions,
  deletions,
  className,
}: {
  additions: number;
  deletions: number;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 tabular-nums",
        className,
      )}
    >
      <span className="text-emerald-500">+{additions}</span>
      <span className="text-red-500">-{deletions}</span>
    </span>
  );
}

function formatWorkspacePath(path: string) {
  const compact = path
    .replace(/^\/mnt\/user-data\/workspace\//, "")
    .replace(/^\/mnt\/user-data\/outputs\//, "outputs/");
  const lastSlash = compact.lastIndexOf("/");
  if (lastSlash < 0) {
    return { dirname: "", basename: compact };
  }
  return {
    dirname: compact.slice(0, lastSlash),
    basename: compact.slice(lastSlash + 1),
  };
}
