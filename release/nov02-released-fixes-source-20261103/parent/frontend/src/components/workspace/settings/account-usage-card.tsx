"use client";

import { RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAccountUsage } from "@/core/console/account-usage";
import { useI18n } from "@/core/i18n/hooks";

export function AccountUsageCard({ compact = false }: { compact?: boolean }) {
  const usage = useAccountUsage();
  const { t, locale } = useI18n();
  const labels = t.settings.account;
  const number = (value: number) => value.toLocaleString(locale);
  const total = usage.isError
    ? "—"
    : usage.data
      ? number(usage.data.total_tokens)
      : "…";

  return (
    <div
      data-testid="account-token-usage"
      className={
        compact
          ? "bg-muted/60 rounded-lg px-3 py-2.5"
          : "bg-muted/30 rounded-xl border p-4"
      }
    >
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span className="text-muted-foreground text-xs">
          {labels.totalTokens}
        </span>
        <span
          className={
            compact
              ? "text-sm font-semibold tabular-nums"
              : "text-xl font-semibold tabular-nums"
          }
        >
          {total}
        </span>
      </div>
      {usage.isError ? (
        <div
          role="status"
          className="text-muted-foreground mt-2 flex items-center justify-between gap-2 text-xs"
        >
          <span>{labels.usageUnavailable}</span>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={labels.retryUsage}
            onClick={() => void usage.refetch()}
          >
            <RotateCwIcon className="size-3.5" />
          </Button>
        </div>
      ) : null}
      {!compact && usage.data && !usage.isError ? (
        <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <div>
            <dt className="text-muted-foreground text-xs">
              {labels.inputTokens}
            </dt>
            <dd className="mt-1 font-medium tabular-nums">
              {number(usage.data.input_tokens)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground text-xs">
              {labels.outputTokens}
            </dt>
            <dd className="mt-1 font-medium tabular-nums">
              {number(usage.data.output_tokens)}
            </dd>
          </div>
          {usage.data.unclassified_tokens > 0 ? (
            <div className="col-span-2 flex flex-wrap justify-between gap-2 border-t pt-3 text-xs">
              <dt className="text-muted-foreground">
                {labels.unclassifiedTokens}
              </dt>
              <dd className="tabular-nums">
                {number(usage.data.unclassified_tokens)}
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}
      {!compact ? (
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          {labels.usageScope}
        </p>
      ) : null}
    </div>
  );
}
