"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  BotIcon,
  ClockIcon,
  ExternalLinkIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  ThumbsUpIcon,
  UsersIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useDashboardSummary } from "@/core/dashboard";
import type {
  AgentUsageItem,
  DashboardFilters,
  FeedbackSummary,
  ModelUsageItem,
  RecentFailureItem,
  UserActivityPoint,
} from "@/core/dashboard";
import {
  useObservabilityAnalytics,
  useObservabilityAuditLogs,
} from "@/core/observability";
import type { AuditLog, TraceSummary } from "@/core/observability";
import { cn } from "@/lib/utils";

function compactNumber(value: number): string {
  return Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value);
}

function percent(value: number): string {
  return `${Math.round(value * 1000) / 10}%`;
}

function duration(ms: number): string {
  if (!ms) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 100) / 10}s`;
  return `${Math.round(ms / 6000) / 10}m`;
}

function shortTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "-";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function toDateTimeLocal(value: Date): string {
  const offsetMs = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offsetMs).toISOString().slice(0, 16);
}

function isoFromDateTimeLocal(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return undefined;
  return date.toISOString();
}

function hoursBetween(startAt?: string, endAt?: string): number | null {
  if (!startAt || !endAt) return null;
  const start = new Date(startAt).valueOf();
  const end = new Date(endAt).valueOf();
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return null;
  return Math.max(1, Math.ceil((end - start) / 3_600_000));
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "slate",
}: {
  label: string;
  value: string;
  detail: string;
  icon: typeof ActivityIcon;
  tone?: "slate" | "green" | "amber" | "red";
}) {
  const toneClass = {
    slate: "bg-slate-50 text-slate-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    red: "bg-red-50 text-red-700",
  }[tone];

  return (
    <Card className="min-w-0 gap-4 rounded-lg py-5 shadow-none">
      <CardHeader className="flex-row items-center justify-between gap-3 px-5">
        <CardTitle className="text-sm font-medium text-slate-600">
          {label}
        </CardTitle>
        <span className={cn("rounded-md p-2", toneClass)}>
          <Icon className="size-4" />
        </span>
      </CardHeader>
      <CardContent className="px-5">
        <div className="text-2xl font-semibold break-words text-slate-950">
          {value}
        </div>
        <div className="mt-2 text-xs text-slate-500">{detail}</div>
      </CardContent>
    </Card>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex min-h-28 items-center justify-center rounded-lg border border-dashed text-sm text-slate-500">
      {label}
    </div>
  );
}

function ActivityTrend({ items }: { items: UserActivityPoint[] }) {
  const max = Math.max(1, ...items.map((item) => item.runs));
  if (items.length === 0) return <EmptyState label="暂无活跃数据" />;

  return (
    <div className="flex h-56 min-w-0 items-end gap-1 overflow-hidden border-b border-l border-slate-200 px-2 pt-4 sm:gap-2">
      {items.map((item) => {
        const height = Math.max(6, Math.round((item.runs / max) * 100));
        return (
          <div
            key={item.date}
            className="flex min-w-0 flex-1 flex-col items-center gap-2"
          >
            <div className="flex w-full min-w-0 items-end justify-center gap-1">
              <div
                className="w-full max-w-3 min-w-[2px] rounded-t bg-slate-800"
                style={{ height: `${height}%`, minHeight: item.runs ? 8 : 2 }}
                title={`${item.runs} 次运行`}
              />
              <div
                className="w-full max-w-3 min-w-[2px] rounded-t bg-emerald-500"
                style={{
                  height: `${Math.max(4, Math.round((item.active_users / Math.max(1, max)) * 100))}%`,
                  minHeight: item.active_users ? 8 : 2,
                }}
                title={`${item.active_users} 个活跃用户`}
              />
            </div>
            <div className="w-full truncate text-center text-xs text-slate-500">
              {shortDate(item.date)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AgentRanking({ items }: { items: AgentUsageItem[] }) {
  if (items.length === 0) return <EmptyState label="暂无智能体使用数据" />;

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_40px_40px_56px] gap-2 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500 sm:grid-cols-[minmax(0,1fr)_84px_84px_96px] sm:gap-3 sm:px-4">
        <div>智能体</div>
        <div className="text-right">运行</div>
        <div className="text-right">用户</div>
        <div className="text-right">成功率</div>
      </div>
      {items.map((item) => (
        <div
          key={item.assistant_id ?? item.name}
          className="grid min-w-0 grid-cols-[minmax(0,1fr)_40px_40px_56px] gap-2 border-t px-3 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_84px_84px_96px] sm:gap-3 sm:px-4"
        >
          <div className="min-w-0">
            <div
              className="truncate font-medium text-slate-800"
              title={item.name}
            >
              {item.name}
            </div>
            <div className="truncate text-xs text-slate-500">
              平均耗时 {duration(item.avg_duration_ms)}
            </div>
          </div>
          <div className="text-right text-slate-700">{item.runs}</div>
          <div className="text-right text-slate-700">{item.users}</div>
          <div className="text-right text-slate-700">
            {percent(item.success_rate)}
          </div>
        </div>
      ))}
    </div>
  );
}

function ModelRanking({ items }: { items: ModelUsageItem[] }) {
  if (items.length === 0) return <EmptyState label="暂无模型调用数据" />;
  return (
    <div className="space-y-3">
      {items.slice(0, 8).map((item) => (
        <div
          key={item.model_name}
          className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-lg border px-3 py-3 text-sm"
        >
          <div className="min-w-0">
            <div className="truncate font-medium text-slate-800">
              {item.model_name}
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {item.llm_calls} 次调用 · {compactNumber(item.total_tokens)} Token
            </div>
          </div>
          <div className="text-right text-xs text-slate-500">
            <div>{percent(item.success_rate)} 成功</div>
            <div className="mt-1">平均 {duration(item.avg_duration_ms)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function RunRanking({ title, rows }: { title: string; rows: TraceSummary[] }) {
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ClockIcon className="size-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState label="当前时间范围内暂无数据" />
        ) : (
          <div className="space-y-2">
            {rows.slice(0, 8).map((run) => (
              <div
                key={run.run_id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-lg border px-3 py-3 text-sm"
              >
                <div className="min-w-0">
                  <div
                    className="truncate font-medium text-slate-800"
                    title={run.run_id}
                  >
                    {run.run_id}
                  </div>
                  <div className="mt-1 truncate text-xs text-slate-500">
                    {run.model ?? "未记录模型"} · {run.agent ?? "默认智能体"}
                  </div>
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div>{duration(run.duration_ms)}</div>
                  <div className="mt-1">{compactNumber(run.tokens)} Token</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RecentFailures({ rows }: { rows: RecentFailureItem[] }) {
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangleIcon className="size-4" />
          近期失败运行
        </CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState label="当前时间范围内没有失败运行" />
        ) : (
          <div className="space-y-2">
            {rows.slice(0, 10).map((row) => (
              <div
                key={row.run_id}
                className="rounded-lg border border-red-100 bg-red-50/30 p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate font-medium text-slate-800">
                    {row.run_id}
                  </span>
                  <Link
                    className="shrink-0 text-xs text-red-700 hover:text-red-900"
                    href={row.chat_url}
                  >
                    查看原对话 <ExternalLinkIcon className="inline size-3" />
                  </Link>
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-red-700">
                  {row.error ?? row.status}
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  {shortTime(row.updated_at)}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ManagementAudit({ rows }: { rows: AuditLog[] }) {
  const managementRows = rows.filter((row) => row.is_management).slice(0, 10);
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheckIcon className="size-4" />
          管理操作审计
        </CardTitle>
      </CardHeader>
      <CardContent>
        {managementRows.length === 0 ? (
          <EmptyState label="当前时间范围内暂无管理操作" />
        ) : (
          <div className="overflow-hidden rounded-lg border text-sm">
            {managementRows.map((row) => (
              <div
                key={row.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-b px-3 py-3 last:border-b-0"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium text-slate-800">
                    {row.action}
                  </div>
                  <div className="mt-1 truncate text-xs text-slate-500">
                    {row.user_email ?? row.user_id ?? "系统"} · {row.method}{" "}
                    {row.path}
                  </div>
                </div>
                <div className="text-right text-xs text-slate-500">
                  <div
                    className={
                      row.result === "success"
                        ? "text-emerald-700"
                        : "text-red-700"
                    }
                  >
                    {row.result}
                  </div>
                  <div className="mt-1">{shortTime(row.created_at)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function FeedbackPanel({ summary }: { summary?: FeedbackSummary }) {
  const total = summary?.total ?? 0;
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ThumbsUpIcon className="size-4" />
          用户反馈
        </CardTitle>
      </CardHeader>
      <CardContent className="min-w-0 space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">反馈数</div>
            <div className="mt-2 text-2xl font-semibold">
              {compactNumber(total)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">好评</div>
            <div className="mt-2 text-2xl font-semibold text-emerald-700">
              {compactNumber(summary?.positive ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">差评</div>
            <div className="mt-2 text-2xl font-semibold text-red-700">
              {compactNumber(summary?.negative ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">满意率</div>
            <div className="mt-2 text-2xl font-semibold">
              {percent(summary?.satisfaction_rate ?? 0)}
            </div>
          </div>
        </div>
        {(summary?.by_feature.length ?? 0) > 0 ? (
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-700">功能满意度</div>
            {summary?.by_feature.slice(0, 5).map((item) => (
              <div
                key={item.name}
                className="grid min-w-0 grid-cols-[minmax(0,1fr)_48px_48px_56px] gap-2 rounded-lg border px-3 py-2 text-sm sm:grid-cols-[minmax(0,1fr)_64px_64px_72px]"
              >
                <div className="truncate text-slate-700" title={item.name}>
                  {item.name}
                </div>
                <div className="text-right text-emerald-700">
                  好 {item.positive}
                </div>
                <div className="text-right text-red-700">
                  差 {item.negative}
                </div>
                <div className="text-right text-slate-500">
                  {percent(item.satisfaction_rate)}
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {(summary?.recent_negative.length ?? 0) > 0 ? (
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-700">近期负反馈</div>
            {summary?.recent_negative.map((item) => (
              <div
                key={`${item.run_id}-${item.created_at}-negative`}
                className="rounded-lg border border-red-100 bg-red-50/40 p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="rounded bg-red-100 px-2 py-1 text-xs font-medium text-red-700">
                    待复盘
                  </span>
                  <Link
                    href={item.diagnostics_url}
                    className="inline-flex items-center gap-1 text-xs text-red-700 hover:text-red-900"
                  >
                    查看诊断
                    <ExternalLinkIcon className="size-3" />
                  </Link>
                </div>
                <div className="mt-2 line-clamp-2 text-slate-700">
                  {item.comment ?? item.run_id}
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {shortTime(item.created_at)}
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {(summary?.recent_comments.length ?? 0) > 0 ? (
          <div className="space-y-2">
            <div className="text-sm font-medium text-slate-700">近期反馈</div>
            {summary?.recent_comments.map((item) => (
              <div
                key={`${item.run_id}-${item.created_at}`}
                className="rounded-lg border p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-3">
                  <span
                    className={cn(
                      "rounded px-2 py-1 text-xs font-medium",
                      item.rating > 0
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-red-50 text-red-700",
                    )}
                  >
                    {item.rating > 0 ? "好评" : "差评"}
                  </span>
                  <span className="text-xs text-slate-500">
                    {shortTime(item.created_at)}
                  </span>
                </div>
                <div className="mt-2 line-clamp-2 text-slate-600">
                  {item.comment}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState label="暂无文字反馈" />
        )}
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const [hours, setHours] = useState(24);
  const [rangeMode, setRangeMode] = useState<"preset" | "custom">("preset");
  const [customStart, setCustomStart] = useState(() =>
    toDateTimeLocal(new Date(Date.now() - 24 * 3_600_000)),
  );
  const [customEnd, setCustomEnd] = useState(() => toDateTimeLocal(new Date()));
  const draftStart = isoFromDateTimeLocal(customStart);
  const draftEnd = isoFromDateTimeLocal(customEnd);
  const draftHours = hoursBetween(draftStart, draftEnd);
  const canApplyCustomRange = draftHours !== null && draftHours <= 24 * 90;
  const rangeStart = rangeMode === "custom" ? draftStart : undefined;
  const rangeEnd = rangeMode === "custom" ? draftEnd : undefined;
  const activeHours = rangeMode === "custom" ? (draftHours ?? hours) : hours;
  const filters = useMemo<DashboardFilters>(
    () => ({
      hours: activeHours,
      start_at: rangeStart,
      end_at: rangeEnd,
    }),
    [activeHours, rangeEnd, rangeStart],
  );
  const summaryQuery = useDashboardSummary(filters);
  const analyticsQuery = useObservabilityAnalytics(
    activeHours,
    activeHours > 48 ? "day" : "hour",
    rangeStart,
    rangeEnd,
  );
  const auditQuery = useObservabilityAuditLogs({
    hours: activeHours,
    start_at: rangeStart,
    end_at: rangeEnd,
    limit: 100,
    operation_type: "management",
  });
  const summary = summaryQuery.data;

  const windowLabel = useMemo(() => {
    if (!summary) return "加载中";
    return `${shortTime(summary.window.start_at)} - ${shortTime(summary.window.end_at)}`;
  }, [summary]);
  return (
    <main className="min-h-screen w-full max-w-full overflow-x-hidden bg-slate-50/60 px-4 py-6 text-slate-950 sm:px-6">
      <div className="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-6">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-normal">业务看板</h1>
            <p className="mt-1 max-w-full text-sm break-words text-slate-500">
              {windowLabel}，展示业务价值、用户活跃和运行质量。
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {[1, 24, 168].map((value) => (
              <Button
                key={value}
                size="sm"
                variant={
                  rangeMode === "preset" && hours === value
                    ? "default"
                    : "outline"
                }
                onClick={() => {
                  setRangeMode("preset");
                  setHours(value);
                }}
              >
                {value === 1 ? "1小时" : value === 168 ? "7天" : "24小时"}
              </Button>
            ))}
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-2 py-1">
              <Input
                aria-label="开始时间"
                className="h-8 w-[168px]"
                type="datetime-local"
                value={customStart}
                onChange={(event) => setCustomStart(event.target.value)}
              />
              <span className="text-xs text-slate-400">至</span>
              <Input
                aria-label="结束时间"
                className="h-8 w-[168px]"
                type="datetime-local"
                value={customEnd}
                onChange={(event) => setCustomEnd(event.target.value)}
              />
              <Button
                size="sm"
                variant={rangeMode === "custom" ? "default" : "outline"}
                disabled={!canApplyCustomRange}
                onClick={() => setRangeMode("custom")}
              >
                应用区间
              </Button>
            </div>
            <span className="text-xs text-slate-500">
              {rangeMode === "custom"
                ? windowLabel
                : hours === 1
                  ? "最近 1 小时"
                  : hours === 168
                    ? "最近 7 天"
                    : "最近 24 小时"}
            </span>
            <Button
              size="sm"
              variant="outline"
              className="bg-white"
              onClick={() => {
                void summaryQuery.refetch();
                void analyticsQuery.refetch();
                void auditQuery.refetch();
              }}
              disabled={
                summaryQuery.isFetching ||
                analyticsQuery.isFetching ||
                auditQuery.isFetching
              }
            >
              <RefreshCwIcon
                className={cn(
                  "size-4",
                  (summaryQuery.isFetching ||
                    analyticsQuery.isFetching ||
                    auditQuery.isFetching) &&
                    "animate-spin",
                )}
              />
              刷新
            </Button>
          </div>
        </div>

        {summaryQuery.isError ? (
          <Card className="rounded-lg border-red-200 bg-red-50 py-5 text-red-700 shadow-none">
            <CardContent>
              看板数据加载失败：{summaryQuery.error.message}
            </CardContent>
          </Card>
        ) : null}

        <div className="grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            label="活跃用户"
            value={compactNumber(summary?.overview.active_users ?? 0)}
            detail={`总用户 ${compactNumber(summary?.overview.total_users ?? 0)}，新增 ${compactNumber(summary?.overview.new_users ?? 0)}`}
            icon={UsersIcon}
            tone="green"
          />
          <MetricCard
            label="任务运行"
            value={compactNumber(summary?.overview.runs ?? 0)}
            detail={`会话 ${compactNumber(summary?.overview.threads ?? 0)}，运行中 ${compactNumber(summary?.overview.running_runs ?? 0)}`}
            icon={ActivityIcon}
          />
          <MetricCard
            label="成功率"
            value={percent(summary?.overview.success_rate ?? 0)}
            detail={`成功 ${compactNumber(summary?.overview.successful_runs ?? 0)}，失败 ${compactNumber(summary?.overview.failed_runs ?? 0)}`}
            icon={ZapIcon}
            tone={(summary?.overview.failed_runs ?? 0) > 0 ? "amber" : "green"}
          />
          <MetricCard
            label="Token 与模型调用"
            value={compactNumber(summary?.overview.total_tokens ?? 0)}
            detail={`${compactNumber(summary?.overview.llm_calls ?? 0)} 次 LLM 调用，平均耗时 ${duration(summary?.overview.avg_run_duration_ms ?? 0)}`}
            icon={BarChart3Icon}
          />
        </div>

        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <BarChart3Icon className="size-4" />
                用户活跃趋势
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ActivityTrend items={summary?.user_activity ?? []} />
              <div className="mt-3 flex gap-4 text-xs text-slate-500">
                <span className="inline-flex items-center gap-1">
                  <span className="size-2 rounded-full bg-slate-800" />
                  运行次数
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="size-2 rounded-full bg-emerald-500" />
                  活跃用户
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <BarChart3Icon className="size-4" />
                模型使用
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ModelRanking items={summary?.model_usage ?? []} />
            </CardContent>
          </Card>
        </div>

        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <BotIcon className="size-4" />
                智能体排行
              </CardTitle>
            </CardHeader>
            <CardContent>
              <AgentRanking items={summary?.agent_usage ?? []} />
            </CardContent>
          </Card>

          <FeedbackPanel summary={summary?.feedback} />
        </div>

        <div className="border-t border-slate-200 pt-2">
          <h2 className="text-xl font-semibold text-slate-950">运行与审计</h2>
          <p className="mt-1 text-sm text-slate-500">
            以下数据直接来自运行记录、Token 统计、失败记录和管理审计日志。
          </p>
        </div>

        <div className="grid min-w-0 gap-4 xl:grid-cols-2">
          <RunRanking
            title="慢运行排行"
            rows={analyticsQuery.data?.slow_runs ?? []}
          />
          <RunRanking
            title="高 Token 运行排行"
            rows={analyticsQuery.data?.high_token_runs ?? []}
          />
          <RecentFailures rows={summary?.recent_failures ?? []} />
          <ManagementAudit rows={auditQuery.data ?? []} />
        </div>
      </div>
    </main>
  );
}
