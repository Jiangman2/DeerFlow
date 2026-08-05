"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  BookOpenIcon,
  CalendarClockIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClockIcon,
  ExternalLinkIcon,
  FileTextIcon,
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
  useDashboardFailures,
  useDashboardUsers,
  useDashboardUserSessions,
  activityBarPercent,
  activityChartMinWidth,
  activityPeriodLabel,
  activitySharedMax,
  dashboardUserPrimaryLabel,
  dashboardUserSecondaryParts,
  dashboardBarStyle,
  type DashboardFilters,
  type ExternalAgentClickItem,
  type ExternalPlatformClickSummary,
  type FeedbackSummary,
  type KnowledgeUsageSummary,
  type RecentFailureItem,
  type ScheduledTaskUsageSummary,
  type UserActivityPoint,
  type UserUsageItem,
} from "@/core/dashboard";
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

function ActivityTrend({
  items,
  bucket,
}: {
  items: UserActivityPoint[];
  bucket: string;
}) {
  const sharedMax = activitySharedMax(items);
  if (items.length === 0) return <EmptyState label="暂无活跃数据" />;

  return (
    <div className="min-w-0 overflow-x-auto pb-2">
      <div
        className="grid h-56 border-b border-l border-slate-200 px-3 pt-3"
        style={{
          minWidth: activityChartMinWidth(items.length),
          gridTemplateColumns: `repeat(${items.length}, minmax(36px, 1fr))`,
        }}
      >
        {items.map((item) => (
          <div
            key={item.date}
            className="flex h-full min-w-0 flex-col items-center justify-end gap-2"
          >
            <div className="flex h-44 w-full items-end justify-center gap-1">
              <div
                className="w-2.5 rounded-t bg-slate-800"
                style={{
                  height: item.threads
                    ? `${activityBarPercent(item.threads, sharedMax)}%`
                    : 2,
                }}
                title={`${item.threads} 个创建会话`}
              />
              <div
                className="w-2.5 rounded-t bg-emerald-500"
                style={{
                  height: item.active_users
                    ? `${activityBarPercent(item.active_users, sharedMax)}%`
                    : 2,
                }}
                title={`${item.active_users} 个活跃用户`}
              />
            </div>
            <div
              className="w-full truncate text-center text-[11px] text-slate-500"
              title={activityPeriodLabel(item, bucket)}
            >
              {activityPeriodLabel(item, bucket)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RegisteredUserSessions() {
  const pageSize = 5;
  const [page, setPage] = useState(0);
  const usersQuery = useDashboardUsers(pageSize, page * pageSize);
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);
  const [sessionPage, setSessionPage] = useState(0);
  const sessionsQuery = useDashboardUserSessions(
    expandedUserId,
    pageSize,
    sessionPage * pageSize,
  );
  const pageUsers = usersQuery.data?.items ?? [];
  const totalPages = Math.max(
    1,
    Math.ceil((usersQuery.data?.total ?? 0) / pageSize),
  );
  const currentPage = Math.min(page, totalPages - 1);
  const sessionTotalPages = Math.max(
    1,
    Math.ceil((sessionsQuery.data?.total ?? 0) / pageSize),
  );

  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <UsersIcon className="size-4" />
            注册用户现存会话
          </span>
          <span className="text-xs font-normal text-slate-500">
            当前全量 · 共 {usersQuery.data?.total ?? 0} 个注册用户
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {usersQuery.isError ? (
          <EmptyState label={`用户会话加载失败：${usersQuery.error.message}`} />
        ) : pageUsers.length === 0 ? (
          <EmptyState
            label={usersQuery.isLoading ? "正在加载…" : "暂无注册用户"}
          />
        ) : (
          <div className="space-y-3">
            <div className="max-h-[34rem] space-y-2 overflow-y-auto pr-1">
              {pageUsers.map((user) => {
                const expanded = expandedUserId === user.user_id;
                return (
                  <div
                    key={user.user_id}
                    className="rounded-lg border bg-white"
                  >
                    <button
                      type="button"
                      className="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-4 p-3 text-left"
                      onClick={() => {
                        setSessionPage(0);
                        setExpandedUserId(expanded ? null : user.user_id);
                      }}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-medium text-slate-800">
                          {dashboardUserPrimaryLabel(user)}
                        </span>
                        <span
                          className="mt-1 block truncate text-xs text-slate-500"
                          title={dashboardUserSecondaryParts(user).join(" · ")}
                        >
                          {dashboardUserSecondaryParts(user).join(" · ")}
                        </span>
                        <span className="mt-1 block text-xs text-slate-500">
                          {user.system_role === "admin" ? "管理员" : "普通用户"}
                          {user.is_disabled ? " · 已停用" : ""}
                          {" · "}
                          现存会话 {user.existing_threads} · 最后活动{" "}
                          {user.last_active_at
                            ? shortTime(user.last_active_at)
                            : "暂无"}
                        </span>
                      </span>
                      <span className="text-right text-xs text-slate-500">
                        <span className="block font-medium text-slate-700">
                          {user.runs} 次运行
                        </span>
                        <span className="mt-1 block">
                          成功 {user.successful_runs} · 失败 {user.failed_runs}{" "}
                          · 运行中 {user.running_runs}
                        </span>
                      </span>
                    </button>
                    {expanded ? (
                      <div className="border-t bg-slate-50/60 p-3">
                        {sessionsQuery.isLoading ? (
                          <div className="py-3 text-center text-xs text-slate-500">
                            正在加载现存会话…
                          </div>
                        ) : sessionsQuery.isError ? (
                          <div className="py-3 text-center text-xs text-red-600">
                            会话加载失败：{sessionsQuery.error.message}
                          </div>
                        ) : (sessionsQuery.data?.items.length ?? 0) === 0 ? (
                          <div className="py-3 text-center text-xs text-slate-500">
                            该用户当前没有未删除会话
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {sessionsQuery.data?.items.map((session) => (
                              <div
                                key={session.thread_id}
                                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-md border bg-white px-3 py-2"
                              >
                                <span className="min-w-0">
                                  {session.can_open && session.chat_url ? (
                                    <Link
                                      className="block truncate text-sm font-medium text-cyan-700 hover:text-cyan-900"
                                      href={session.chat_url}
                                    >
                                      {session.title ?? "未命名会话"}
                                    </Link>
                                  ) : (
                                    <span
                                      className="block truncate text-sm font-medium text-slate-700"
                                      title="出于会话隐私与权限隔离，仅会话所有者可以打开内容"
                                    >
                                      {session.title ?? "未命名会话"}
                                    </span>
                                  )}
                                  <span
                                    className="mt-1 block truncate text-xs text-slate-400"
                                    title={session.thread_id}
                                  >
                                    {session.thread_id}
                                  </span>
                                  {!session.can_open ? (
                                    <span className="mt-1 block text-xs text-slate-400">
                                      他人会话仅展示运行统计
                                    </span>
                                  ) : null}
                                </span>
                                <span className="text-right text-xs text-slate-500">
                                  <span className="block">
                                    {session.runs} 次运行
                                  </span>
                                  <span className="mt-1 block">
                                    成功 {session.successful_runs} · 失败{" "}
                                    {session.failed_runs} · 运行中{" "}
                                    {session.running_runs}
                                  </span>
                                  <span className="mt-1 block text-slate-400">
                                    状态 {session.status} · 创建{" "}
                                    {shortTime(session.created_at)}
                                  </span>
                                  <span className="mt-1 block text-slate-400">
                                    最近运行{" "}
                                    {session.last_run_at
                                      ? shortTime(session.last_run_at)
                                      : "暂无"}
                                  </span>
                                </span>
                              </div>
                            ))}
                            <RankingPagination
                              page={Math.min(
                                sessionPage,
                                sessionTotalPages - 1,
                              )}
                              totalPages={sessionTotalPages}
                              onPageChange={setSessionPage}
                            />
                          </div>
                        )}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <RankingPagination
              page={currentPage}
              totalPages={totalPages}
              onPageChange={(nextPage) => {
                setPage(nextPage);
                setExpandedUserId(null);
                setSessionPage(0);
              }}
            />
            <p className="text-xs text-slate-500">
              这里只统计仍存在的会话及其中的运行；用户已删除的会话不会混入当前会话明细。
            </p>
          </div>
        )}
      </CardContent>
    </Card>
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
                {dashboardUserPrimaryLabel(item)}
              </div>
              <div
                className="mt-1 truncate text-xs text-slate-500"
                title={dashboardUserSecondaryParts(item).join(" · ")}
              >
                {dashboardUserSecondaryParts(item).join(" · ")}
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
    "地震数据提频专家",
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
                className="h-full rounded-full"
                style={dashboardBarStyle(clicks, max)}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

const EXTERNAL_TREND_COLORS = [
  "#0369a1",
  "#0891b2",
  "#0d9488",
  "#2563eb",
  "#4f46e5",
];

function ExternalPlatformClicks({
  summary,
}: {
  summary?: ExternalPlatformClickSummary;
}) {
  const [scope, setScope] = useState<"today" | "all">("today");
  if (!summary) {
    return (
      <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
        <CardContent className="pt-6">
          <EmptyState label="正在加载外部智能体点击统计" />
        </CardContent>
      </Card>
    );
  }

  const isToday = scope === "today";
  const maxTrend = Math.max(1, ...Object.values(summary.trend.series).flat());
  const sourceDetail = (
    item: ExternalPlatformClickSummary["agents"][number],
    field: "today_users" | "total_users" | "today_clicks" | "total_clicks",
  ) =>
    item.by_source
      .map((source) => `${source.source.replace("平台", "")} ${source[field]}`)
      .join(" · ");

  return (
    <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
      <CardHeader className="gap-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-3 text-base">
          <span className="flex items-center gap-2">
            <ExternalLinkIcon className="size-4 text-cyan-700" />
            外部智能体点击（海能+协同）
          </span>
          <span className="flex items-center gap-1 rounded-lg border border-slate-200 bg-slate-50 p-1">
            <Button
              size="sm"
              className="h-7 px-3"
              variant={isToday ? "default" : "ghost"}
              onClick={() => setScope("today")}
            >
              今日
            </Button>
            <Button
              size="sm"
              className="h-7 px-3"
              variant={!isToday ? "default" : "ghost"}
              onClick={() => setScope("all")}
            >
              全部
            </Button>
          </span>
        </CardTitle>
        <p className="text-xs leading-5 text-slate-500">
          今日按北京时间统计；全部为历史累计。下方趋势固定展示最近 30
          个自然日，不受页面顶部时间范围影响。同一来源内用户去重，不同来源分别计数。
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">智能体</th>
                <th className="px-4 py-3 text-right font-medium">
                  {isToday ? "今日活跃用户" : "累计用户"}
                </th>
                <th className="px-4 py-3 text-right font-medium">
                  {isToday ? "今日点击" : "累计点击"}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {summary.agents.map((item) => {
                const userField = isToday ? "today_users" : "total_users";
                const clickField = isToday ? "today_clicks" : "total_clicks";
                return (
                  <tr key={item.name} className="bg-white">
                    <td className="px-4 py-3 font-medium text-slate-800">
                      {item.name}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="font-semibold text-cyan-700">
                        {item[userField]}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400">
                        {sourceDetail(item, userField)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="font-semibold text-slate-800">
                        {item[clickField]}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400">
                        {sourceDetail(item, clickField)}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">
                近 30 天每日活跃用户
              </h3>
              <p className="mt-1 text-xs text-slate-400">
                每行采用同一刻度，便于比较不同智能体的活跃程度。
              </p>
            </div>
            <span className="text-xs text-slate-400">
              {summary.trend.dates[0]} 至{" "}
              {summary.trend.dates[summary.trend.dates.length - 1]}
            </span>
          </div>
          <div className="space-y-4 overflow-x-auto pb-2">
            {summary.agents.map((item, agentIndex) => {
              const values = summary.trend.series[item.name] ?? [];
              return (
                <div key={item.name} className="min-w-[760px]">
                  <div className="mb-1.5 flex items-center justify-between gap-4 text-xs">
                    <span className="font-medium text-slate-600">
                      {item.name}
                    </span>
                    <span className="text-slate-400">
                      30 天合计 {values.reduce((sum, value) => sum + value, 0)}{" "}
                      人次
                    </span>
                  </div>
                  <div
                    className="grid h-14 items-end gap-1 rounded-md bg-slate-50 px-2 pt-2"
                    style={{
                      gridTemplateColumns: `repeat(${Math.max(1, values.length)}, minmax(6px, 1fr))`,
                    }}
                  >
                    {values.map((value, index) => (
                      <div
                        key={`${summary.trend.dates[index]}-${index}`}
                        className="min-h-0 rounded-t-sm"
                        title={`${summary.trend.dates[index]}：${value} 人`}
                        style={{
                          height:
                            value > 0
                              ? `${Math.max(8, (value / maxTrend) * 100)}%`
                              : "2px",
                          backgroundColor:
                            value > 0
                              ? EXTERNAL_TREND_COLORS[
                                  agentIndex % EXTERNAL_TREND_COLORS.length
                                ]
                              : "#e2e8f0",
                        }}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function UsagePair({
  label,
  current,
  historical,
  currentLabel = "当前",
  historicalLabel = "所选区间",
}: {
  label: string;
  current: number;
  historical: number;
  currentLabel?: string;
  historicalLabel?: string;
}) {
  return (
    <div className="rounded-lg border bg-white p-4">
      <div className="text-sm text-slate-500">{label}</div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div>
          <div className="text-2xl font-semibold text-slate-900">
            {compactNumber(current)}
          </div>
          <div className="text-xs text-slate-400">{currentLabel}</div>
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold text-cyan-700">
            {compactNumber(historical)}
          </div>
          <div className="text-xs text-slate-400">{historicalLabel}</div>
        </div>
      </div>
    </div>
  );
}

function KnowledgeUsagePanel({ summary }: { summary?: KnowledgeUsageSummary }) {
  const pageSize = 5;
  const [userPage, setUserPage] = useState(0);
  const [sharedSpacePage, setSharedSpacePage] = useState(0);
  if (!summary) {
    return (
      <Card className="min-w-0 rounded-lg shadow-none">
        <CardContent className="pt-6">
          <EmptyState label="正在加载 WeKnora 使用统计" />
        </CardContent>
      </Card>
    );
  }
  if (!summary.available) {
    return (
      <Card className="min-w-0 rounded-lg shadow-none">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BookOpenIcon className="size-4" />
            知识库使用情况
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
            暂时无法读取 WeKnora 统计。DeerFlow 看板本身仍可正常使用， 请检查
            WeKnora 数据库连接。
          </div>
        </CardContent>
      </Card>
    );
  }

  const maxFormat = Math.max(
    1,
    ...summary.file_formats.flatMap((item) => [
      item.current_count,
      item.uploaded_count,
    ]),
  );
  const userTotalPages = Math.max(
    1,
    Math.ceil(summary.users.length / pageSize),
  );
  const currentUserPage = Math.min(userPage, userTotalPages - 1);
  const visibleUsers = summary.users.slice(
    currentUserPage * pageSize,
    (currentUserPage + 1) * pageSize,
  );
  const sharedSpaceTotalPages = Math.max(
    1,
    Math.ceil(summary.shared_spaces.length / pageSize),
  );
  const currentSharedSpacePage = Math.min(
    sharedSpacePage,
    sharedSpaceTotalPages - 1,
  );
  const visibleSharedSpaces = summary.shared_spaces.slice(
    currentSharedSpacePage * pageSize,
    (currentSharedSpacePage + 1) * pageSize,
  );
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BookOpenIcon className="size-4 text-cyan-700" />
          知识库使用情况
        </CardTitle>
      </CardHeader>
      <CardContent className="min-w-0 space-y-5">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <UsagePair
            label="用户"
            current={summary.current_users}
            historical={summary.registered_users}
            currentLabel="现存"
            historicalLabel="累计新注册"
          />
          <UsagePair
            label="知识库"
            current={summary.current_knowledge_bases}
            historical={summary.created_knowledge_bases}
            currentLabel="现存"
            historicalLabel="累计创建"
          />
          <UsagePair
            label="文档"
            current={summary.current_documents}
            historical={summary.uploaded_documents}
            currentLabel="现存"
            historicalLabel="累计上传"
          />
          <UsagePair
            label="知识库会话"
            current={summary.current_conversation_sessions}
            historical={summary.conversation_sessions}
            currentLabel="现存"
            historicalLabel="累计创建"
          />
          <UsagePair
            label="用户提问"
            current={summary.current_question_turns}
            historical={summary.question_turns}
            currentLabel="现存"
            historicalLabel="累计提问"
          />
        </div>

        <div className="rounded-lg border border-cyan-100 bg-cyan-50/50 px-4 py-3 text-xs leading-5 text-slate-600">
          “知识库会话”是用户新建的一次知识库对话；“用户提问”是这些对话中发送的问题总轮次。
          一次会话可以连续提出多个问题，因此用户提问数通常大于知识库会话数。
          本区域的“累计”均表示所选时间范围内累计发生的数量。
        </div>

        <div className="min-w-0 rounded-lg border p-4">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-800">
            <FileTextIcon className="size-4 text-cyan-700" />
            文档格式分布
          </div>
          {summary.file_formats.length === 0 ? (
            <EmptyState label="所选时间范围暂无文档" />
          ) : (
            <div className="grid gap-x-6 gap-y-4 md:grid-cols-2 xl:grid-cols-3">
              {summary.file_formats.map((item) => (
                <div key={item.format} className="space-y-1.5">
                  <div className="flex justify-between gap-3 text-xs">
                    <span className="font-medium text-slate-700 uppercase">
                      {item.format || "OTHER"}
                    </span>
                    <span className="text-slate-500">
                      现存 {item.current_count} · 累计上传 {item.uploaded_count}
                    </span>
                  </div>
                  <div className="space-y-1">
                    <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full"
                        style={dashboardBarStyle(item.current_count, maxFormat)}
                      />
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full"
                        style={{
                          ...dashboardBarStyle(item.uploaded_count, maxFormat),
                          backgroundColor: "#2563eb",
                        }}
                      />
                    </div>
                  </div>
                </div>
              ))}
              <div className="flex items-center gap-4 text-xs text-slate-400">
                <span>青色：现存</span>
                <span>蓝色：累计上传</span>
              </div>
            </div>
          )}
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="border-b px-4 py-3 text-sm font-semibold text-slate-800">
            知识库明细
          </div>
          <div className="max-h-96 overflow-auto">
            <table className="w-full min-w-[900px] text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">知识库 / 创建人</th>
                  <th className="px-3 py-3 font-medium">文档</th>
                  <th className="px-3 py-3 font-medium">会话</th>
                  <th className="px-3 py-3 font-medium">提问</th>
                  <th className="px-3 py-3 font-medium">格式</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {summary.knowledge_bases.map((item) => (
                  <tr key={item.knowledge_base_id}>
                    <td className="max-w-64 px-4 py-3">
                      <div className="truncate font-medium text-slate-800">
                        {item.name}
                      </div>
                      <div className="truncate text-slate-400">
                        {item.creator_email ?? item.creator_name ?? "-"}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-slate-600">
                      现存 {item.current_documents}
                      <br />
                      累计上传 {item.uploaded_documents}
                    </td>
                    <td className="px-3 py-3 text-slate-600">
                      现存 {item.current_conversation_sessions}
                      <br />
                      累计创建 {item.conversation_sessions}
                    </td>
                    <td className="px-3 py-3 text-slate-600">
                      现存 {item.current_question_turns}
                      <br />
                      累计提问 {item.question_turns}
                    </td>
                    <td className="max-w-72 px-3 py-3 text-slate-500">
                      {item.file_formats.length
                        ? item.file_formats
                            .map(
                              (format) =>
                                `${format.format.toUpperCase()} ${format.current_count}/${format.uploaded_count}`,
                            )
                            .join(" · ")
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {summary.knowledge_bases.length === 0 ? (
              <div className="p-4">
                <EmptyState label="暂无知识库" />
              </div>
            ) : null}
          </div>
        </div>

        <div className="space-y-4 rounded-lg border p-4">
          <div>
            <div className="text-sm font-semibold text-slate-800">
              共享空间使用情况
            </div>
            <div className="mt-1 text-xs text-slate-500">
              成员按加入共享空间的工作空间统计；知识库和文档按共享记录去重。
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <UsagePair
              label="共享空间"
              current={summary.current_shared_spaces}
              historical={summary.created_shared_spaces}
              currentLabel="现存"
              historicalLabel="累计创建"
            />
            <UsagePair
              label="成员工作空间"
              current={summary.current_shared_space_members}
              historical={summary.joined_shared_space_members}
              currentLabel="当前加入"
              historicalLabel="累计加入"
            />
            <UsagePair
              label="共享知识库"
              current={summary.current_shared_knowledge_bases}
              historical={summary.shared_knowledge_bases}
              currentLabel="当前共享"
              historicalLabel="累计共享"
            />
            <UsagePair
              label="共享文档"
              current={summary.current_shared_documents}
              historical={summary.uploaded_shared_documents}
              currentLabel="当前可用"
              historicalLabel="累计上传"
            />
            <UsagePair
              label="加入申请"
              current={summary.pending_shared_space_requests}
              historical={summary.shared_space_join_requests}
              currentLabel="当前待处理"
              historicalLabel="累计提交"
            />
          </div>
          <div className="min-w-0 overflow-hidden rounded-lg border">
            <div className="max-h-96 overflow-auto">
              <table className="w-full min-w-[1100px] text-left text-xs">
                <thead className="sticky top-0 bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-4 py-3 font-medium">共享空间 / 所有者</th>
                    <th className="px-3 py-3 font-medium">创建时间</th>
                    <th className="px-3 py-3 font-medium">成员角色</th>
                    <th className="px-3 py-3 font-medium">共享知识库</th>
                    <th className="px-3 py-3 font-medium">共享文档</th>
                    <th className="px-3 py-3 font-medium">加入申请</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {visibleSharedSpaces.map((item) => (
                    <tr key={item.shared_space_id}>
                      <td className="max-w-72 px-4 py-3">
                        <div className="truncate font-medium text-slate-800">
                          {item.name}
                        </div>
                        <div className="truncate text-slate-400">
                          {item.owner_email ?? item.owner_name ?? "-"}
                        </div>
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        {shortTime(item.created_at)}
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        当前 {item.current_members} · 累计加入{" "}
                        {item.joined_members}
                        <br />
                        管理 {item.admins} / 编辑 {item.editors} / 查看{" "}
                        {item.viewers}
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        当前 {item.current_knowledge_bases}
                        <br />
                        累计共享 {item.shared_knowledge_bases}
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        当前 {item.current_documents}
                        <br />
                        累计上传 {item.uploaded_documents}
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        待处理 {item.pending_requests}
                        <br />
                        累计提交 {item.join_requests}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {summary.shared_spaces.length === 0 ? (
                <div className="p-4">
                  <EmptyState label="暂无共享空间" />
                </div>
              ) : null}
            </div>
          </div>
          <RankingPagination
            page={currentSharedSpacePage}
            totalPages={sharedSpaceTotalPages}
            onPageChange={setSharedSpacePage}
          />
        </div>

        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="border-b px-4 py-3 text-sm font-semibold text-slate-800">
            知识库使用排行
          </div>
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[820px] text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">用户</th>
                  <th className="px-3 py-3 font-medium">
                    知识库 现存/累计创建
                  </th>
                  <th className="px-3 py-3 font-medium">文档 现存/累计上传</th>
                  <th className="px-3 py-3 font-medium">累计会话</th>
                  <th className="px-3 py-3 font-medium">累计提问</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {visibleUsers.map((item) => (
                  <tr key={item.weknora_user_id}>
                    <td className="max-w-72 px-4 py-3">
                      <div className="truncate font-medium text-slate-800">
                        {item.email ?? item.name ?? item.weknora_user_id}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      {item.current_knowledge_bases} /{" "}
                      {item.created_knowledge_bases}
                    </td>
                    <td className="px-3 py-3">
                      {item.current_documents} / {item.uploaded_documents}
                    </td>
                    <td className="px-3 py-3">{item.conversation_sessions}</td>
                    <td className="px-3 py-3">{item.question_turns}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {summary.users.length === 0 ? (
              <div className="p-4">
                <EmptyState label="暂无知识库用户" />
              </div>
            ) : null}
          </div>
          <div className="px-4 pb-3">
            <RankingPagination
              page={currentUserPage}
              totalPages={userTotalPages}
              onPageChange={setUserPage}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ScheduledTaskUsagePanel({
  summary,
}: {
  summary?: ScheduledTaskUsageSummary;
}) {
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <CalendarClockIcon className="size-4 text-cyan-700" />
            定时任务使用情况
          </span>
          <span className="text-xs font-normal text-slate-500">
            历史创建审计可在任务删除后继续保留
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <UsagePair
            label="定时任务"
            current={summary?.current_tasks ?? 0}
            historical={summary?.created_tasks ?? 0}
            historicalLabel="区间创建"
          />
          <UsagePair
            label="任务状态"
            current={summary?.enabled_tasks ?? 0}
            historical={summary?.paused_tasks ?? 0}
            currentLabel="当前启用"
            historicalLabel="当前暂停"
          />
          <UsagePair
            label="任务执行"
            current={summary?.successful_runs ?? 0}
            historical={summary?.runs ?? 0}
            currentLabel="区间成功"
            historicalLabel="区间总执行"
          />
          <UsagePair
            label="异常与跳过"
            current={summary?.failed_runs ?? 0}
            historical={summary?.skipped_runs ?? 0}
            currentLabel="失败/中断"
            historicalLabel="跳过"
          />
        </div>
        <div className="min-w-0 overflow-hidden rounded-lg border">
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[780px] text-left text-xs">
              <thead className="sticky top-0 bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">用户</th>
                  <th className="px-3 py-3 font-medium">任务 当前/区间创建</th>
                  <th className="px-3 py-3 font-medium">启用/暂停</th>
                  <th className="px-3 py-3 font-medium">执行</th>
                  <th className="px-3 py-3 font-medium">成功/失败/跳过</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(summary?.users ?? []).map((item) => (
                  <tr key={item.user_id}>
                    <td className="max-w-72 px-4 py-3">
                      <div className="truncate font-medium text-slate-800">
                        {dashboardUserPrimaryLabel(item)}
                      </div>
                      <div
                        className="mt-1 truncate text-xs text-slate-500"
                        title={dashboardUserSecondaryParts(item).join(" · ")}
                      >
                        {dashboardUserSecondaryParts(item).join(" · ")}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      {item.current_tasks} / {item.created_tasks}
                    </td>
                    <td className="px-3 py-3">
                      {item.enabled_tasks} / {item.paused_tasks}
                    </td>
                    <td className="px-3 py-3">{item.runs}</td>
                    <td className="px-3 py-3">
                      {item.successful_runs} / {item.failed_runs} /{" "}
                      {item.skipped_runs}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(summary?.users.length ?? 0) === 0 ? (
              <div className="p-4">
                <EmptyState label="所选区间暂无定时任务活动" />
              </div>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
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
                      href={`/workspace/chats/${run.thread_id}?admin_review=1`}
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

function FailedRuns({
  rows,
  total,
  page,
  isLoading,
  isError,
  onPageChange,
}: {
  rows: RecentFailureItem[];
  total: number;
  page: number;
  isLoading: boolean;
  isError: boolean;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / 5));
  const currentPage = Math.min(page, totalPages - 1);
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangleIcon className="size-4" />
          全部失败运行（{compactNumber(total)}）
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <EmptyState label="正在加载失败运行" />
        ) : isError ? (
          <EmptyState label="失败运行加载失败，请刷新后重试" />
        ) : rows.length === 0 ? (
          <EmptyState label="目前没有失败运行" />
        ) : (
          <div className="space-y-3">
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {rows.map((row) => (
                <div
                  key={row.run_id}
                  className="rounded-lg border border-red-100 bg-red-50/30 p-3 text-sm"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate font-medium text-slate-800">
                      {row.run_id}
                    </span>
                    {row.can_open && row.chat_url ? (
                      <Link
                        className="shrink-0 text-xs text-red-700 hover:text-red-900"
                        href={row.chat_url}
                      >
                        查看原对话{" "}
                        <ExternalLinkIcon className="inline size-3" />
                      </Link>
                    ) : (
                      <Link
                        className="shrink-0 text-xs text-amber-700 hover:text-amber-900"
                        href={row.diagnostics_url}
                      >
                        查看诊断 <ExternalLinkIcon className="inline size-3" />
                      </Link>
                    )}
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
              onPageChange={onPageChange}
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
          用户反馈（所选区间）
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
  const [failurePage, setFailurePage] = useState(0);
  const draftStart = isoFromDateTimeLocal(customStart);
  const draftEnd = isoFromDateTimeLocal(customEnd);
  const draftHours = hoursBetween(draftStart, draftEnd);
  const canApplyCustomRange = draftHours !== null;
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
  const failuresQuery = useDashboardFailures(5, failurePage * 5);
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
                void failuresQuery.refetch();
              }}
              disabled={
                summaryQuery.isFetching ||
                analyticsQuery.isFetching ||
                failuresQuery.isFetching
              }
            >
              <RefreshCwIcon
                className={cn(
                  "size-4",
                  (summaryQuery.isFetching ||
                    analyticsQuery.isFetching ||
                    failuresQuery.isFetching) &&
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
        {analyticsQuery.isError ? (
          <Card className="rounded-lg border-amber-200 bg-amber-50 py-5 text-amber-800 shadow-none">
            <CardContent>
              运行排行加载失败：{analyticsQuery.error.message}
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
            label="创建会话"
            value={compactNumber(summary?.overview.threads ?? 0)}
            detail={`已完成 ${compactNumber(summary?.overview.terminal_runs ?? 0)} · 运行中 ${compactNumber(summary?.overview.running_runs ?? 0)} · 现存会话 ${compactNumber(summary?.overview.existing_threads ?? 0)}`}
            icon={ActivityIcon}
          />
          <MetricCard
            label="成功率"
            value={percent(summary?.overview.success_rate ?? 0)}
            detail={`成功 ${compactNumber(summary?.overview.successful_runs ?? 0)} + 失败 ${compactNumber(summary?.overview.failed_runs ?? 0)} = 已完成 ${compactNumber(summary?.overview.terminal_runs ?? 0)}`}
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
              <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
                <span className="flex items-center gap-2">
                  <BarChart3Icon className="size-4" />
                  用户活跃趋势
                </span>
                <span className="text-xs font-normal text-slate-500">
                  区间创建{" "}
                  {compactNumber(
                    summary?.user_activity.reduce(
                      (total, item) => total + item.threads,
                      0,
                    ) ?? 0,
                  )}{" "}
                  个会话 ·
                  {summary?.activity_bucket === "week"
                    ? " 按周聚合"
                    : summary?.activity_bucket === "month"
                      ? " 按约30天聚合"
                      : summary?.activity_bucket === "year"
                        ? " 按约1年聚合"
                        : " 按日统计"}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ActivityTrend
                items={summary?.user_activity ?? []}
                bucket={summary?.activity_bucket ?? "day"}
              />
              <div className="mt-3 flex gap-4 text-xs text-slate-500">
                <span className="inline-flex items-center gap-1">
                  <span className="size-2 rounded-full bg-slate-800" />
                  创建会话数
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="size-2 rounded-full bg-emerald-500" />
                  活跃用户
                </span>
              </div>
              <p className="mt-2 text-xs text-slate-400">
                按所选时间范围展示会话创建与活跃用户趋势，两组柱使用同一刻度。
              </p>
            </CardContent>
          </Card>
        </div>

        <FeedbackPanel summary={summary?.feedback} />

        <KnowledgeUsagePanel summary={summary?.knowledge_usage} />

        <ScheduledTaskUsagePanel summary={summary?.scheduled_task_usage} />

        <div className="grid min-w-0 gap-4 xl:grid-cols-2">
          <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
                <span className="flex items-center gap-2">
                  <UsersIcon className="size-4 text-cyan-700" />
                  用户运行排行
                </span>
                <span className="text-xs font-normal text-slate-500">
                  所选区间历史运行
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <UserRanking items={summary?.user_usage ?? []} />
            </CardContent>
          </Card>
          <Card className="min-w-0 rounded-xl border-white/80 bg-white/80 shadow-sm backdrop-blur">
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
                <span className="flex items-center gap-2">
                  <ExternalLinkIcon className="size-4 text-cyan-700" />
                  外部智能体点击
                </span>
                <span className="text-xs font-normal text-slate-500">
                  所选区间
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ExternalAgentClicks
                items={summary?.external_agent_clicks ?? []}
              />
            </CardContent>
          </Card>
        </div>

        <RegisteredUserSessions />

        <div className="grid min-w-0 gap-4 xl:grid-cols-2">
          <RunRanking
            title="慢运行排行（已完成）"
            rows={analyticsQuery.data?.slow_runs ?? []}
          />
          <RunRanking
            title="高 Token 运行排行（已完成）"
            rows={analyticsQuery.data?.high_token_runs ?? []}
          />
          <FailedRuns
            rows={failuresQuery.data?.items ?? []}
            total={failuresQuery.data?.total ?? 0}
            page={failurePage}
            isLoading={failuresQuery.isLoading}
            isError={failuresQuery.isError}
            onPageChange={setFailurePage}
          />
        </div>

        <ExternalPlatformClicks
          summary={summary?.external_platform_agent_clicks}
        />
      </div>
    </main>
  );
}
