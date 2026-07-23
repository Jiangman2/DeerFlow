"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  RefreshCwIcon,
  ThumbsUpIcon,
  UsersIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  useDashboardSummary,
  type DashboardFilters,
  type ExternalAgentClickItem,
  type FeedbackSummary,
  type RecentFailureItem,
  type UserActivityPoint,
  type UserUsageItem,
} from "@/core/dashboard";
import { getDashboardPage } from "@/core/dashboard/pagination";
import { useObservabilityAnalytics } from "@/core/observability";
import type { TraceSummary } from "@/core/observability";
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

function UserRanking({ items }: { items: UserUsageItem[] }) {
  const pageSize = 5;
  const [page, setPage] = useState(0);
  if (items.length === 0) return <EmptyState label="暂无用户使用数据" />;
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const pageItems = items.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
  return (
    <div className="space-y-3">
      <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
        {pageItems.map((item, index) => (
          <div
            key={item.user_id}
            className="flex items-center gap-3 rounded-lg border border-slate-200/80 bg-white/70 px-3 py-3"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
              {currentPage * pageSize + index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-slate-800">
                {item.email ?? item.user_id}
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {item.threads} 个会话 · {compactNumber(item.total_tokens)} Token
              </div>
            </div>
            <div className="text-right">
              <div className="text-lg font-semibold text-slate-900">
                {item.runs}
              </div>
              <div className="text-xs text-slate-500">次运行</div>
            </div>
          </div>
        ))}
      </div>
      <RankingPagination
        page={currentPage}
        totalPages={totalPages}
        onPageChange={setPage}
      />
    </div>
  );
}

function RankingPagination({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-end gap-2 border-t pt-3 text-xs text-slate-500">
      <span>
        {page + 1} / {totalPages}
      </span>
      <Button
        size="icon"
        variant="outline"
        className="size-7"
        disabled={page === 0}
        onClick={() => onPageChange(page - 1)}
        aria-label="上一页"
      >
        <ChevronLeftIcon className="size-3.5" />
      </Button>
      <Button
        size="icon"
        variant="outline"
        className="size-7"
        disabled={page >= totalPages - 1}
        onClick={() => onPageChange(page + 1)}
        aria-label="下一页"
      >
        <ChevronRightIcon className="size-3.5" />
      </Button>
    </div>
  );
}

function ExternalAgentClicks({ items }: { items: ExternalAgentClickItem[] }) {
  const names = [
    "专业文档解析专家",
    "地震数据分辨率提升工具",
    "专业报告校审专家",
    "自动数采专家",
    "前期研究报告编写专家",
  ];
  const counts = new Map(items.map((item) => [item.name, item]));
  const max = Math.max(1, ...items.map((item) => item.clicks));
  return (
    <div className="space-y-3">
      {names.map((name) => {
        const item = counts.get(name);
        const clicks = item?.clicks ?? 0;
        return (
          <div key={name} className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate text-slate-700">{name}</span>
              <span className="shrink-0 font-semibold text-cyan-700">
                {clicks} 次
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                style={{
                  width: `${Math.max(clicks ? 8 : 0, (clicks / max) * 100)}%`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RunRanking({ title, rows }: { title: string; rows: TraceSummary[] }) {
  const pageSize = 5;
  const [page, setPage] = useState(0);
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, totalPages - 1);
  const pageRows = rows.slice(
    currentPage * pageSize,
    (currentPage + 1) * pageSize,
  );
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
          <div className="space-y-3">
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {pageRows.map((run) => (
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
                    <div className="mt-1">
                      {compactNumber(run.tokens)} Token
                    </div>
                    <Link
                      className="mt-2 inline-flex items-center gap-1 text-cyan-700 hover:text-cyan-900"
                      href={`/workspace/chats/${run.thread_id}`}
                    >
                      查看原对话 <ExternalLinkIcon className="size-3" />
                    </Link>
                  </div>
                </div>
              ))}
            </div>
            <RankingPagination
              page={currentPage}
              totalPages={totalPages}
              onPageChange={setPage}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function RecentFailures({ rows }: { rows: RecentFailureItem[] }) {
  const [page, setPage] = useState(0);
  const {
    currentPage,
    totalPages,
    rows: pageRows,
  } = getDashboardPage(rows, page);
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
          <div className="space-y-3">
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {pageRows.map((row) => (
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
            <RankingPagination
              page={currentPage}
              totalPages={totalPages}
              onPageChange={setPage}
            />
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
        <div className="grid grid-cols-3 gap-3">
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
        </div>
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
  const summary = summaryQuery.data;

  const windowLabel = useMemo(() => {
    if (!summary) return "加载中";
    return `${shortTime(summary.window.start_at)} - ${shortTime(summary.window.end_at)}`;
  }, [summary]);
  return (
    <main className="min-h-screen w-full max-w-full overflow-x-hidden bg-[radial-gradient(circle_at_top_left,#e0f2fe_0,transparent_28%),linear-gradient(to_bottom,#f8fafc,#eef2f7)] px-4 py-6 text-slate-950 sm:px-6">
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
                className="h-8 w-[220px] min-w-[220px] shrink-0"
                type="datetime-local"
                value={customStart}
                onChange={(event) => setCustomStart(event.target.value)}
              />
              <span className="text-xs text-slate-400">至</span>
              <Input
                aria-label="结束时间"
                className="h-8 w-[220px] min-w-[220px] shrink-0"
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
              }}
              disabled={summaryQuery.isFetching || analyticsQuery.isFetching}
            >
              <RefreshCwIcon
                className={cn(
                  "size-4",
                  (summaryQuery.isFetching || analyticsQuery.isFetching) &&
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
            detail={`累计创建会话 ${compactNumber(summary?.overview.threads ?? 0)}，运行中 ${compactNumber(summary?.overview.running_runs ?? 0)}`}
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

        <div className="min-w-0">
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
        </div>

        <FeedbackPanel summary={summary?.feedback} />

        <div className="grid min-w-0 gap-4 xl:grid-cols-2">
          <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <UsersIcon className="size-4 text-cyan-700" />
                用户使用排行
              </CardTitle>
            </CardHeader>
            <CardContent>
              <UserRanking items={summary?.user_usage ?? []} />
            </CardContent>
          </Card>
          <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ExternalLinkIcon className="size-4 text-cyan-700" />
                外部智能体点击
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ExternalAgentClicks
                items={summary?.external_agent_clicks ?? []}
              />
            </CardContent>
          </Card>
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
        </div>
      </div>
    </main>
  );
}
