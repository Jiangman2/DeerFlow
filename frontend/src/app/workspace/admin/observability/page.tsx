"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  CopyIcon,
  DatabaseIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileSearchIcon,
  MessageSquareIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  SquareIcon,
  UsersIcon,
  WorkflowIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  useObservabilityAnalytics,
  useObservabilityAuditLogs,
  useObservabilitySnapshot,
  useObservabilityTraces,
  useRecordRunDisposition,
  useRunDiagnostics,
  useUserActivity,
} from "@/core/observability";
import type {
  AuditLog,
  CostBreakdown,
  ObservabilityAnalytics,
  TraceEvent,
  TraceSummary,
  UserActivityItem,
  UserActivityReport,
} from "@/core/observability";

function compactNumber(value: number): string {
  return Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value);
}

function percent(value: number): string {
  return `${Math.round(value * 1000) / 10}%`;
}

function signedPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${Math.round(value * 1000) / 10}%`;
}

function duration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${Math.round(ms / 100) / 10}s`;
  return `${Math.round(ms / 6000) / 10}m`;
}

function comparisonTone(metric: string, value: number): string {
  if (value === 0) return "text-slate-500";
  const isBadIncrease = ["error_rate", "avg_duration_ms"].includes(metric);
  return value > 0 === isBadIncrease ? "text-red-700" : "text-emerald-700";
}

function shortTime(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
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

function windowLabel(hours: number, startAt?: string, endAt?: string): string {
  if (startAt && endAt) {
    return `${shortTime(startAt)} - ${shortTime(endAt)}`;
  }
  if (hours === 1) return "最近 1 小时";
  if (hours === 24) return "最近 24 小时";
  if (hours === 168) return "最近 7 天";
  return `最近 ${hours} 小时`;
}

function stringifyDetail(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value || "-";
  return JSON.stringify(value, null, 2);
}

function csvCell(value: unknown): string {
  let text = "";
  if (typeof value === "string") {
    text = value;
  } else if (
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    text = value.toString();
  } else if (typeof value === "symbol") {
    text = value.description ?? "";
  } else if (value !== null && value !== undefined) {
    text = JSON.stringify(value);
  }
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadUserActivityCsv(report: UserActivityReport, label: string) {
  const headers = [
    "用户",
    "用户ID",
    "运行次数",
    "成功次数",
    "失败次数",
    "错误率",
    "Token",
    "LLM调用",
    "线程数",
    "平均耗时",
    "上传",
    "下载",
    "常用智能体",
    "常用模型",
    "最近活跃",
  ];
  const rows = report.users.map((user) => [
    user.email ?? user.user_id,
    user.user_id,
    user.runs,
    user.success_runs,
    user.error_runs,
    percent(user.error_rate),
    user.tokens,
    user.llm_calls,
    user.threads,
    duration(user.avg_duration_ms),
    user.uploads,
    user.downloads,
    user.agents.join(" / "),
    user.models.join(" / "),
    shortTime(user.last_active_at),
  ]);
  const csv = [headers, ...rows]
    .map((row) => row.map(csvCell).join(","))
    .join("\n");
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `user-activity-${label.replace(/\s+/g, "-")}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function statusTone(status: string): string {
  if (
    ["error", "timeout", "interrupted", "failure", "degraded"].includes(status)
  ) {
    return "bg-red-50 text-red-700";
  }
  if (["pending", "running", "unknown"].includes(status)) {
    return "bg-amber-50 text-amber-700";
  }
  return "bg-emerald-50 text-emerald-700";
}

function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={[
        "inline-flex h-6 items-center rounded px-2 text-xs font-medium",
        statusTone(status),
      ].join(" ")}
    >
      {status}
    </span>
  );
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-semibold text-slate-950">{value}</p>
        </div>
        <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-700">
          <Icon className="size-5" />
        </div>
      </div>
      <p className="mt-3 truncate text-xs text-slate-500">{detail}</p>
    </section>
  );
}

function StatusBars({ statuses }: { statuses: Record<string, number> }) {
  const entries = Object.entries(statuses).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, count]) => count), 1);
  return (
    <div className="space-y-3">
      {entries.length === 0 ? (
        <p className="text-sm text-slate-500">暂无运行数据</p>
      ) : (
        entries.map(([status, count]) => (
          <div
            key={status}
            className="grid grid-cols-[92px_1fr_52px] items-center gap-3"
          >
            <StatusPill status={status} />
            <div className="h-2 overflow-hidden rounded bg-slate-100">
              <div
                className="h-full rounded bg-cyan-600"
                style={{ width: `${Math.max(6, (count / max) * 100)}%` }}
              />
            </div>
            <span className="text-right text-sm text-slate-600 tabular-nums">
              {count}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function HourlyTrend({
  rows,
}: {
  rows: Array<{ hour: string; runs: number }>;
}) {
  const max = Math.max(...rows.map((row) => row.runs), 1);
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-950">小时趋势</h2>
        <ClockIcon className="size-4 text-slate-400" />
      </div>
      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">暂无趋势数据</p>
      ) : (
        <div className="mt-4 flex h-44 items-end gap-2 overflow-x-auto pb-1">
          {rows.map((row) => (
            <div
              key={row.hour}
              className="flex min-w-9 flex-1 flex-col items-center justify-end gap-2"
              title={`${shortTime(row.hour)} ${row.runs} 次`}
            >
              <div
                className="w-full rounded-t bg-cyan-600"
                style={{ height: `${Math.max(8, (row.runs / max) * 132)}px` }}
              />
              <span className="text-[10px] text-slate-500 tabular-nums">
                {row.runs}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ComparisonCard({
  label,
  metric,
  value,
  renderValue,
}: {
  label: string;
  metric: string;
  value: {
    current: number;
    previous: number;
    delta: number;
    change_rate: number;
  };
  renderValue: (value: number) => string;
}) {
  return (
    <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-3">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-2 text-lg font-semibold text-slate-950">
        {renderValue(value.current)}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        前一周期 {renderValue(value.previous)} ·{" "}
        <span className={comparisonTone(metric, value.change_rate)}>
          {signedPercent(value.change_rate)}
        </span>
      </p>
    </div>
  );
}

function TrendAndComparison({
  analytics,
}: {
  analytics: ObservabilityAnalytics | undefined;
}) {
  if (!analytics) {
    return (
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-sm text-slate-500">正在加载趋势分析</p>
      </section>
    );
  }
  const maxRuns = Math.max(...analytics.trend.map((row) => row.runs), 1);
  const label = windowLabel(
    analytics.window_hours,
    analytics.window_start,
    analytics.window_end,
  );
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm xl:col-span-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-950">
          历史趋势与周期对比
        </h2>
        <span className="text-xs text-slate-500">
          {label} · {analytics.bucket === "day" ? "按天" : "按小时"}
        </span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <ComparisonCard
          label="运行量"
          metric="runs"
          value={analytics.comparison.runs}
          renderValue={(value) => `${value} 次`}
        />
        <ComparisonCard
          label="错误率"
          metric="error_rate"
          value={analytics.comparison.error_rate}
          renderValue={percent}
        />
        <ComparisonCard
          label="Token"
          metric="tokens"
          value={analytics.comparison.tokens}
          renderValue={compactNumber}
        />
        <ComparisonCard
          label="平均耗时"
          metric="avg_duration_ms"
          value={analytics.comparison.avg_duration_ms}
          renderValue={duration}
        />
      </div>
      <div className="mt-5 flex h-52 items-end gap-2 overflow-x-auto border-t border-slate-100 pt-4">
        {analytics.trend.length === 0 ? (
          <p className="self-start text-sm text-slate-500">暂无趋势数据</p>
        ) : (
          analytics.trend.map((row) => (
            <div
              key={row.period_start}
              className="flex min-w-12 flex-1 flex-col items-center justify-end gap-2"
              title={`${shortTime(row.period_start)} 运行 ${row.runs} 错误率 ${percent(row.error_rate)} Token ${row.tokens}`}
            >
              <div className="flex w-full items-end gap-1">
                <div
                  className="flex-1 rounded-t bg-cyan-600"
                  style={{
                    height: `${Math.max(8, (row.runs / maxRuns) * 132)}px`,
                  }}
                />
                <div
                  className="w-2 rounded-t bg-red-400"
                  style={{ height: `${Math.max(4, row.error_rate * 132)}px` }}
                />
              </div>
              <span className="text-[10px] text-slate-500 tabular-nums">
                {row.runs}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function RunRanking({
  title,
  rows,
  metric,
  onSelect,
}: {
  title: string;
  rows: TraceSummary[];
  metric: "duration" | "tokens";
  onSelect: (run: TraceSummary) => void;
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-slate-950">{title}</h2>
      <div className="mt-4 space-y-2">
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">暂无数据</p>
        ) : (
          rows.map((run) => (
            <button
              key={run.run_id}
              className="grid w-full grid-cols-[1fr_96px] items-center gap-3 rounded border border-slate-100 px-3 py-2 text-left text-sm hover:bg-slate-50"
              onClick={() => onSelect(run)}
              type="button"
            >
              <span className="min-w-0">
                <span className="block truncate font-medium text-slate-900">
                  {run.run_id}
                </span>
                <span className="block truncate text-xs text-slate-500">
                  {run.email ?? run.user_id ?? "-"} · {run.model ?? "unknown"}
                </span>
              </span>
              <span className="text-right text-slate-700 tabular-nums">
                {metric === "duration"
                  ? duration(run.duration_ms)
                  : compactNumber(run.tokens)}
              </span>
            </button>
          ))
        )}
      </div>
    </section>
  );
}

function CostBreakdownTable({
  title,
  rows,
}: {
  title: string;
  rows: CostBreakdown[];
}) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-base font-semibold text-slate-950">{title}</h2>
      <div className="mt-4 overflow-hidden rounded border border-slate-100">
        <div className="grid grid-cols-[1fr_64px_88px_88px_88px] gap-3 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500">
          <span>对象</span>
          <span className="text-right">运行</span>
          <span className="text-right">Token</span>
          <span className="text-right">耗时</span>
          <span className="text-right">工具耗时</span>
        </div>
        {rows.length === 0 ? (
          <p className="px-3 py-5 text-sm text-slate-500">暂无数据</p>
        ) : (
          rows.map((row) => (
            <div
              key={row.key}
              className="grid grid-cols-[1fr_64px_88px_88px_88px] gap-3 border-t border-slate-100 px-3 py-2 text-sm"
            >
              <span className="truncate text-slate-800">
                {row.email ?? row.key}
              </span>
              <span className="text-right text-slate-600 tabular-nums">
                {row.runs}
              </span>
              <span className="text-right text-slate-600 tabular-nums">
                {compactNumber(row.tokens)}
              </span>
              <span className="text-right text-slate-600 tabular-nums">
                {duration(row.avg_duration_ms)}
              </span>
              <span className="text-right text-slate-600 tabular-nums">
                {duration(row.tool_duration_ms)}
              </span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function UserActivityTrend({
  report,
}: {
  report: UserActivityReport | undefined;
}) {
  if (!report) {
    return (
      <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
        <p className="text-sm text-slate-500">正在加载用户趋势</p>
      </section>
    );
  }
  const maxActive = Math.max(...report.trend.map((row) => row.active_users), 1);
  const maxRuns = Math.max(...report.trend.map((row) => row.runs), 1);
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-slate-950">活跃趋势</h2>
        <span className="text-xs text-slate-500">
          {report.bucket === "day" ? "按天" : "按小时"}
        </span>
      </div>
      {report.trend.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">暂无用户活跃数据</p>
      ) : (
        <div className="mt-4 flex h-48 items-end gap-2 overflow-x-auto pb-1">
          {report.trend.map((row) => (
            <div
              key={row.period_start}
              className="flex min-w-10 flex-1 flex-col items-center justify-end gap-2"
              title={`${shortTime(row.period_start)} 活跃 ${row.active_users} 人 / ${row.runs} 次运行`}
            >
              <div className="flex h-36 w-full items-end gap-1">
                <div
                  className="min-h-2 flex-1 rounded-t bg-cyan-600"
                  style={{
                    height: `${Math.max(8, (row.active_users / maxActive) * 136)}px`,
                  }}
                />
                <div
                  className="min-h-2 flex-1 rounded-t bg-amber-500"
                  style={{
                    height: `${Math.max(8, (row.runs / maxRuns) * 136)}px`,
                  }}
                />
              </div>
              <span className="text-[10px] text-slate-500 tabular-nums">
                {row.active_users}/{row.runs}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 flex gap-4 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1">
          <span className="size-2 rounded bg-cyan-600" />
          活跃用户
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="size-2 rounded bg-amber-500" />
          运行次数
        </span>
      </div>
    </section>
  );
}

function UserActivityTable({ rows }: { rows: UserActivityItem[] }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm xl:col-span-2">
      <h2 className="text-base font-semibold text-slate-950">用户明细</h2>
      <div className="mt-4 overflow-x-auto rounded border border-slate-100">
        <div className="min-w-[1040px]">
          <div className="grid grid-cols-[1.5fr_72px_72px_88px_88px_88px_88px_1fr_1fr_120px] gap-3 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500">
            <span>用户</span>
            <span className="text-right">运行</span>
            <span className="text-right">错误</span>
            <span className="text-right">Token</span>
            <span className="text-right">线程</span>
            <span className="text-right">上传</span>
            <span className="text-right">下载</span>
            <span>智能体</span>
            <span>模型</span>
            <span className="text-right">最近活跃</span>
          </div>
          {rows.length === 0 ? (
            <p className="px-3 py-6 text-sm text-slate-500">暂无用户使用数据</p>
          ) : (
            rows.map((user) => (
              <div
                key={user.user_id}
                className="grid grid-cols-[1.5fr_72px_72px_88px_88px_88px_88px_1fr_1fr_120px] gap-3 border-t border-slate-100 px-3 py-3 text-sm"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-900">
                    {user.email ?? user.user_id}
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    {user.user_id}
                  </span>
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {user.runs}
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {user.error_runs}
                  <span className="ml-1 text-xs text-slate-400">
                    {percent(user.error_rate)}
                  </span>
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {compactNumber(user.tokens)}
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {user.threads}
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {user.uploads}
                </span>
                <span className="text-right text-slate-600 tabular-nums">
                  {user.downloads}
                </span>
                <span className="truncate text-slate-600">
                  {user.agents.length ? user.agents.join(" / ") : "-"}
                </span>
                <span className="truncate text-slate-600">
                  {user.models.length ? user.models.join(" / ") : "-"}
                </span>
                <span className="text-right text-xs text-slate-500">
                  {shortTime(user.last_active_at)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative min-w-0 flex-1">
      <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
      <Input
        className="pr-9 pl-9"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
      {value && (
        <Button
          className="absolute top-1/2 right-1 size-7 -translate-y-1/2"
          size="icon"
          variant="ghost"
          onClick={() => onChange("")}
          type="button"
        >
          <XIcon className="size-4" />
        </Button>
      )}
    </div>
  );
}

function TraceList({
  traces,
  selected,
  onSelect,
}: {
  traces: TraceSummary[];
  selected: string | null;
  onSelect: (trace: TraceSummary) => void;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
      <div className="grid grid-cols-[1.3fr_0.8fr_0.7fr_0.6fr_0.6fr] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium text-slate-500">
        <span>Trace / Run</span>
        <span>用户</span>
        <span>状态</span>
        <span>耗时</span>
        <span>事件</span>
      </div>
      {traces.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">暂无匹配链路数据</p>
      ) : (
        traces.map((trace) => (
          <button
            key={trace.run_id}
            className={[
              "grid w-full grid-cols-[1.3fr_0.8fr_0.7fr_0.6fr_0.6fr] gap-3 border-b border-slate-100 px-4 py-3 text-left text-sm last:border-b-0 hover:bg-slate-50",
              selected === trace.run_id ? "bg-cyan-50/70" : "",
            ].join(" ")}
            onClick={() => onSelect(trace)}
            type="button"
          >
            <span className="min-w-0">
              <span className="block truncate font-medium text-slate-900">
                {trace.run_id}
              </span>
              <span className="block truncate text-xs text-slate-500">
                {trace.model ?? "unknown"} · {trace.agent ?? "default"} ·{" "}
                {shortTime(trace.started_at)}
              </span>
            </span>
            <span className="truncate text-slate-600">
              {trace.email ?? trace.user_id ?? "-"}
            </span>
            <StatusPill status={trace.status} />
            <span className="text-slate-600 tabular-nums">
              {duration(trace.duration_ms)}
            </span>
            <span className="text-slate-600 tabular-nums">
              {trace.event_count}
            </span>
          </button>
        ))
      )}
    </div>
  );
}

function AuditTable({
  logs,
  onSelect,
}: {
  logs: AuditLog[];
  onSelect: (log: AuditLog) => void;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
      <div className="grid grid-cols-[120px_1fr_1fr_90px_80px] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium text-slate-500">
        <span>时间</span>
        <span>操作者</span>
        <span>操作对象</span>
        <span>结果</span>
        <span>耗时</span>
      </div>
      {logs.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">暂无匹配审计日志</p>
      ) : (
        logs.map((log) => (
          <button
            key={log.id}
            className="grid w-full grid-cols-[120px_1fr_1fr_90px_80px] gap-3 border-b border-slate-100 px-4 py-3 text-left text-sm last:border-b-0 hover:bg-slate-50"
            onClick={() => onSelect(log)}
            type="button"
          >
            <span className="text-xs text-slate-500">
              {shortTime(log.created_at)}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-slate-900">
                {log.user_email ?? log.user_id ?? "anonymous"}
              </span>
              <span className="block truncate text-xs text-slate-500">
                {log.client_ip ?? "-"}
              </span>
            </span>
            <span className="min-w-0">
              <span className="block truncate font-medium text-slate-800">
                {log.action}
              </span>
              <span className="block truncate text-xs text-slate-500">
                {log.operation_type === "management" ? "管理操作" : "普通调用"}{" "}
                · {log.method} {log.path}
              </span>
            </span>
            <StatusPill status={log.result} />
            <span className="text-slate-600 tabular-nums">
              {duration(log.duration_ms)}
            </span>
          </button>
        ))
      )}
    </div>
  );
}

function DetailGrid({
  rows,
}: {
  rows: Array<[string, string | number | null | undefined]>;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-md bg-slate-50 px-3 py-2">
          <p className="text-xs text-slate-500">{label}</p>
          <p className="mt-1 text-sm break-all text-slate-900">
            {value ?? "-"}
          </p>
        </div>
      ))}
    </div>
  );
}

function TraceEventList({
  events,
  isLoading,
}: {
  events: TraceEvent[] | undefined;
  isLoading: boolean;
}) {
  if (isLoading) {
    return <p className="text-sm text-slate-500">正在加载事件</p>;
  }

  if (!events?.length) {
    return <p className="text-sm text-slate-500">这条链路暂无事件记录</p>;
  }

  return (
    <div className="max-h-72 space-y-3 overflow-auto">
      {events.map((event) => (
        <div
          key={event.id}
          className="rounded border border-slate-100 bg-slate-50 px-3 py-2"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-slate-800">
              #{event.seq} {event.category}.{event.event_type}
            </span>
            <span className="text-xs text-slate-500">
              {shortTime(event.created_at)}
            </span>
          </div>
          {event.content && (
            <p className="mt-1 line-clamp-3 text-xs text-slate-600">
              {event.content}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ObservabilityPage() {
  const searchParams = useSearchParams();
  const initialRunId = searchParams.get("run_id") ?? "";
  const [hours, setHours] = useState(24);
  const [rangeMode, setRangeMode] = useState<"preset" | "custom">("preset");
  const [customStart, setCustomStart] = useState(() =>
    toDateTimeLocal(new Date(Date.now() - 24 * 60 * 60 * 1000)),
  );
  const [customEnd, setCustomEnd] = useState(() => toDateTimeLocal(new Date()));
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    initialRunId || null,
  );
  const [selectedAuditLog, setSelectedAuditLog] = useState<AuditLog | null>(
    null,
  );
  const [traceQuery, setTraceQuery] = useState("");
  const [traceStatus, setTraceStatus] = useState("all");
  const [traceUser, setTraceUser] = useState("");
  const [traceModel, setTraceModel] = useState("all");
  const [traceAgent, setTraceAgent] = useState("all");
  const [traceThread, setTraceThread] = useState("");
  const [traceRunId, setTraceRunId] = useState(initialRunId);
  const [traceErrorType, setTraceErrorType] = useState("all");
  const [auditQuery, setAuditQuery] = useState("");
  const [auditResult, setAuditResult] = useState("all");
  const [auditUser, setAuditUser] = useState("");
  const [auditOperationType, setAuditOperationType] = useState("all");
  const [dispositionNote, setDispositionNote] = useState("");

  const draftStart = isoFromDateTimeLocal(customStart);
  const draftEnd = isoFromDateTimeLocal(customEnd);
  const draftHours = hoursBetween(draftStart, draftEnd);
  const canApplyCustomRange = Boolean(draftHours);
  const rangeStart = rangeMode === "custom" ? draftStart : undefined;
  const rangeEnd = rangeMode === "custom" ? draftEnd : undefined;
  const activeHours = rangeMode === "custom" ? (draftHours ?? hours) : hours;
  const trendBucket: "hour" | "day" = activeHours > 72 ? "day" : "hour";

  useEffect(() => {
    const runId = searchParams.get("run_id") ?? "";
    if (!runId) return;
    setTraceRunId(runId);
    setSelectedRunId(runId);
  }, [searchParams]);

  const snapshotQuery = useObservabilitySnapshot(hours, rangeStart, rangeEnd);
  const analyticsQuery = useObservabilityAnalytics(
    hours,
    trendBucket,
    rangeStart,
    rangeEnd,
  );
  const userActivityQuery = useUserActivity(hours, rangeStart, rangeEnd);
  const traceFilters = useMemo(
    () => ({
      hours,
      start_at: rangeStart,
      end_at: rangeEnd,
      limit: 200,
      q: traceQuery,
      user_id: traceUser,
      status: traceStatus,
      model: traceModel,
      agent: traceAgent,
      thread_id: traceThread,
      run_id: traceRunId,
      error_type: traceErrorType,
    }),
    [
      rangeEnd,
      rangeStart,
      hours,
      traceAgent,
      traceErrorType,
      traceModel,
      traceQuery,
      traceRunId,
      traceStatus,
      traceThread,
      traceUser,
    ],
  );
  const auditFilters = useMemo(
    () => ({
      hours,
      start_at: rangeStart,
      end_at: rangeEnd,
      limit: 200,
      q: auditQuery,
      user_id: auditUser,
      result: auditResult,
      operation_type: auditOperationType,
    }),
    [
      auditOperationType,
      auditQuery,
      auditResult,
      auditUser,
      rangeEnd,
      rangeStart,
      hours,
    ],
  );
  const tracesQuery = useObservabilityTraces(traceFilters);
  const auditLogsQuery = useObservabilityAuditLogs(auditFilters);
  const diagnosticsQuery = useRunDiagnostics(selectedRunId);
  const dispositionMutation = useRecordRunDisposition(selectedRunId);
  const snapshot = snapshotQuery.data;

  const busiestHour = useMemo(() => {
    if (!snapshot?.runs_by_hour.length) return null;
    return [...snapshot.runs_by_hour].sort((a, b) => b.runs - a.runs)[0];
  }, [snapshot]);

  const traceStatusOptions = useMemo(() => {
    const statuses = new Set(
      [...(snapshot?.recent_traces ?? []), ...(tracesQuery.data ?? [])].map(
        (trace) => trace.status,
      ),
    );
    return ["all", ...Array.from(statuses).sort()];
  }, [snapshot, tracesQuery.data]);

  const traceModelOptions = useMemo(() => {
    const models = new Set(
      [...(snapshot?.recent_traces ?? []), ...(tracesQuery.data ?? [])]
        .map((trace) => trace.model)
        .filter((model): model is string => Boolean(model)),
    );
    return ["all", ...Array.from(models).sort()];
  }, [snapshot, tracesQuery.data]);

  const traceAgentOptions = useMemo(() => {
    const agents = new Set(
      [...(snapshot?.recent_traces ?? []), ...(tracesQuery.data ?? [])]
        .map((trace) => trace.agent)
        .filter((agent): agent is string => Boolean(agent)),
    );
    return ["all", ...Array.from(agents).sort()];
  }, [snapshot, tracesQuery.data]);

  const auditResultOptions = useMemo(() => {
    const results = new Set(
      [...(snapshot?.audit_logs ?? []), ...(auditLogsQuery.data ?? [])].map(
        (log) => log.result,
      ),
    );
    return ["all", ...Array.from(results).sort()];
  }, [snapshot, auditLogsQuery.data]);

  const traces = tracesQuery.data ?? snapshot?.recent_traces ?? [];
  const auditLogs = auditLogsQuery.data ?? snapshot?.audit_logs ?? [];

  async function recordDisposition(
    action:
      | "copy_run_id"
      | "copy_thread_id"
      | "open_thread"
      | "retry_run"
      | "cancel_run"
      | "mark_handled"
      | "add_note",
    options?: { note?: string | null; status?: "open" | "handled" | "ignored" },
  ) {
    if (!selectedRunId) return;
    await dispositionMutation.mutateAsync({
      action,
      note: options?.note ?? null,
      status: options?.status ?? null,
    });
  }

  if (snapshotQuery.isLoading) {
    return (
      <main className="flex h-full items-center justify-center text-slate-600">
        <RefreshCwIcon className="mr-2 size-4 animate-spin" />
        正在加载监控数据
      </main>
    );
  }

  if (snapshotQuery.isError || !snapshot) {
    return (
      <main className="flex h-full items-center justify-center p-6">
        <div className="rounded-md border border-red-200 bg-white p-5 text-red-700 shadow-sm">
          <AlertTriangleIcon className="mb-3 size-5" />
          无法读取监控数据。请确认当前账号具备管理员权限。
        </div>
      </main>
    );
  }

  return (
    <main className="h-full overflow-auto bg-slate-50/80 p-6">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">
              运维监控与审计
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              指标、日志、链路追踪与管理操作审计汇总
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
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
                onClick={() => {
                  setRangeMode("custom");
                }}
              >
                应用区间
              </Button>
            </div>
            <span className="text-xs text-slate-500">
              {windowLabel(activeHours, rangeStart, rangeEnd)}
            </span>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void snapshotQuery.refetch();
                void analyticsQuery.refetch();
                void userActivityQuery.refetch();
                void tracesQuery.refetch();
                void auditLogsQuery.refetch();
              }}
            >
              <RefreshCwIcon className="size-4" />
              刷新
            </Button>
          </div>
        </header>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={ShieldCheckIcon}
            label="系统健康"
            value={snapshot.health.status === "healthy" ? "健康" : "需关注"}
            detail={`错误率 ${percent(snapshot.health.error_rate)} · 活跃运行 ${snapshot.health.active_runs}`}
          />
          <MetricCard
            icon={UsersIcon}
            label="注册与活跃用户"
            value={`${compactNumber(snapshot.metrics.users_total)} / ${compactNumber(snapshot.metrics.active_users)}`}
            detail="注册用户 / 当前窗口活跃用户"
          />
          <MetricCard
            icon={ActivityIcon}
            label="运行与调用"
            value={compactNumber(snapshot.metrics.runs_window)}
            detail={`${compactNumber(snapshot.metrics.llm_calls_window)} 次 LLM 调用 · 总运行 ${compactNumber(snapshot.metrics.runs_total)}`}
          />
          <MetricCard
            icon={DatabaseIcon}
            label="Token 与耗时"
            value={compactNumber(snapshot.metrics.tokens_window)}
            detail={`平均耗时 ${duration(snapshot.metrics.avg_run_duration_ms)} · DB ${snapshot.health.database}`}
          />
        </section>

        <Tabs defaultValue="overview" className="gap-4">
          <TabsList className="bg-white">
            <TabsTrigger value="overview">
              <ActivityIcon className="size-4" />
              总览
            </TabsTrigger>
            <TabsTrigger value="users">
              <UsersIcon className="size-4" />
              用户
            </TabsTrigger>
            <TabsTrigger value="traces">
              <WorkflowIcon className="size-4" />
              链路追踪
            </TabsTrigger>
            <TabsTrigger value="audit">
              <FileSearchIcon className="size-4" />
              审计日志
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="grid gap-4 xl:grid-cols-2">
            <TrendAndComparison analytics={analyticsQuery.data} />
            <RunRanking
              title="慢 Run 排行"
              rows={analyticsQuery.data?.slow_runs ?? []}
              metric="duration"
              onSelect={(run) => setSelectedRunId(run.run_id)}
            />
            <RunRanking
              title="高 Token Run 排行"
              rows={analyticsQuery.data?.high_token_runs ?? []}
              metric="tokens"
              onSelect={(run) => setSelectedRunId(run.run_id)}
            />
            <CostBreakdownTable
              title="按用户成本与耗时"
              rows={analyticsQuery.data?.by_user ?? []}
            />
            <CostBreakdownTable
              title="按模型成本与耗时"
              rows={analyticsQuery.data?.by_model ?? []}
            />
            <CostBreakdownTable
              title="按 Agent 成本与耗时"
              rows={analyticsQuery.data?.by_agent ?? []}
            />
            <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">
                运行状态
              </h2>
              <div className="mt-4">
                <StatusBars statuses={snapshot.runs_by_status} />
              </div>
            </section>
            <HourlyTrend rows={snapshot.runs_by_hour} />
            <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">
                用户使用情况
              </h2>
              <div className="mt-4 space-y-3">
                {snapshot.top_users.length === 0 ? (
                  <p className="text-sm text-slate-500">暂无用户使用数据</p>
                ) : (
                  snapshot.top_users.map((user) => (
                    <div
                      key={user.user_id}
                      className="grid grid-cols-[1fr_72px_96px] items-center gap-3 text-sm"
                    >
                      <span className="truncate text-slate-800">
                        {user.email ?? user.user_id}
                      </span>
                      <span className="text-right text-slate-600 tabular-nums">
                        {user.runs} 次
                      </span>
                      <span className="text-right text-slate-600 tabular-nums">
                        {compactNumber(user.tokens)} token
                      </span>
                    </div>
                  ))
                )}
              </div>
              <p className="mt-4 text-xs text-slate-500">
                峰值时段：
                {busiestHour
                  ? `${shortTime(busiestHour.hour)} · ${busiestHour.runs} 次运行`
                  : "暂无"}
              </p>
            </section>
            <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">
                最近错误
              </h2>
              <div className="mt-4 overflow-hidden rounded border border-slate-100">
                {snapshot.recent_errors.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-slate-500">
                    当前窗口没有错误运行
                  </p>
                ) : (
                  snapshot.recent_errors.map((item) => (
                    <button
                      key={item.run_id}
                      className="grid w-full grid-cols-[180px_120px_1fr_130px] gap-3 border-b border-slate-100 px-4 py-3 text-left text-sm last:border-b-0 hover:bg-slate-50"
                      onClick={() => {
                        setSelectedRunId(item.run_id);
                      }}
                      type="button"
                    >
                      <span className="truncate font-medium text-slate-800">
                        {item.run_id}
                      </span>
                      <StatusPill status={item.status} />
                      <span className="truncate text-red-700">
                        {item.error ?? "未记录错误详情"}
                      </span>
                      <span className="text-right text-xs text-slate-500">
                        {shortTime(item.updated_at)}
                      </span>
                    </button>
                  ))
                )}
              </div>
            </section>
          </TabsContent>

          <TabsContent value="users" className="grid gap-4 xl:grid-cols-2">
            <section className="grid gap-4 md:grid-cols-2 xl:col-span-2 xl:grid-cols-4">
              <MetricCard
                icon={UsersIcon}
                label="活跃用户"
                value={compactNumber(
                  userActivityQuery.data?.summary.active_users ?? 0,
                )}
                detail={`活跃率 ${percent(userActivityQuery.data?.summary.activation_rate ?? 0)} / 注册 ${compactNumber(userActivityQuery.data?.summary.total_users ?? 0)}`}
              />
              <MetricCard
                icon={ActivityIcon}
                label="用户运行"
                value={compactNumber(
                  userActivityQuery.data?.summary.total_runs ?? 0,
                )}
                detail={`人均 ${userActivityQuery.data?.summary.avg_runs_per_active_user ?? 0} 次运行`}
              />
              <MetricCard
                icon={AlertTriangleIcon}
                label="失败情况"
                value={percent(userActivityQuery.data?.summary.error_rate ?? 0)}
                detail={`${compactNumber(userActivityQuery.data?.summary.error_runs ?? 0)} 次失败 / ${compactNumber(userActivityQuery.data?.summary.success_runs ?? 0)} 次成功`}
              />
              <MetricCard
                icon={DatabaseIcon}
                label="用户 Token"
                value={compactNumber(
                  userActivityQuery.data?.summary.total_tokens ?? 0,
                )}
                detail={`活跃用户人均 ${compactNumber(userActivityQuery.data?.summary.avg_tokens_per_active_user ?? 0)} token`}
              />
            </section>
            <section className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white p-4 shadow-sm xl:col-span-2">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  用户活跃与应用使用
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  {windowLabel(activeHours, rangeStart, rangeEnd)}
                  {userActivityQuery.isFetching ? " / 正在刷新" : ""}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={!userActivityQuery.data?.users.length}
                onClick={() => {
                  if (userActivityQuery.data) {
                    downloadUserActivityCsv(
                      userActivityQuery.data,
                      windowLabel(activeHours, rangeStart, rangeEnd),
                    );
                  }
                }}
              >
                <DownloadIcon className="size-4" />
                导出 CSV
              </Button>
            </section>
            <UserActivityTrend report={userActivityQuery.data} />
            <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
              <h2 className="text-base font-semibold text-slate-950">
                使用口径
              </h2>
              <div className="mt-4 grid gap-3 text-sm text-slate-600">
                <p>
                  活跃用户按当前时间窗口内有运行记录或上传下载记录的注册用户统计。
                </p>
                <p>
                  上传和下载来自审计日志中的 uploads、files、artifacts、download
                  路径。
                </p>
                <p>智能体和模型取每个用户在窗口内最常使用的前三项。</p>
              </div>
            </section>
            <UserActivityTable rows={userActivityQuery.data?.users ?? []} />
          </TabsContent>

          <TabsContent value="traces" className="grid gap-4">
            <section className="grid gap-3 rounded-md border border-slate-200 bg-white p-4 shadow-sm xl:grid-cols-[1.4fr_repeat(4,minmax(120px,0.8fr))]">
              <SearchBox
                value={traceQuery}
                onChange={setTraceQuery}
                placeholder="搜索 run、thread、用户、模型或错误"
              />
              <Select value={traceStatus} onValueChange={setTraceStatus}>
                <SelectTrigger className="w-full md:w-40">
                  <SelectValue placeholder="状态" />
                </SelectTrigger>
                <SelectContent>
                  {traceStatusOptions.map((status) => (
                    <SelectItem key={status} value={status}>
                      {status === "all" ? "全部状态" : status}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={traceModel} onValueChange={setTraceModel}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="模型" />
                </SelectTrigger>
                <SelectContent>
                  {traceModelOptions.map((model) => (
                    <SelectItem key={model} value={model}>
                      {model === "all" ? "全部模型" : model}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={traceAgent} onValueChange={setTraceAgent}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Agent" />
                </SelectTrigger>
                <SelectContent>
                  {traceAgentOptions.map((agent) => (
                    <SelectItem key={agent} value={agent}>
                      {agent === "all" ? "全部 Agent" : agent}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={traceErrorType} onValueChange={setTraceErrorType}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="错误类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部错误类型</SelectItem>
                  <SelectItem value="model_timeout">模型超时</SelectItem>
                  <SelectItem value="tool_failure">工具失败</SelectItem>
                  <SelectItem value="permission">权限问题</SelectItem>
                  <SelectItem value="database">数据库问题</SelectItem>
                  <SelectItem value="file_missing">文件缺失</SelectItem>
                  <SelectItem value="config">配置错误</SelectItem>
                  <SelectItem value="unknown">未知原因</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={traceUser}
                onChange={(event) => setTraceUser(event.target.value)}
                placeholder="用户 ID"
              />
              <Input
                value={traceThread}
                onChange={(event) => setTraceThread(event.target.value)}
                placeholder="Thread ID"
              />
              <Input
                value={traceRunId}
                onChange={(event) => setTraceRunId(event.target.value)}
                placeholder="Run ID"
              />
              <Button
                variant="outline"
                onClick={() => {
                  setTraceQuery("");
                  setTraceStatus("all");
                  setTraceModel("all");
                  setTraceAgent("all");
                  setTraceErrorType("all");
                  setTraceUser("");
                  setTraceThread("");
                  setTraceRunId("");
                }}
              >
                清空筛选
              </Button>
            </section>
            {tracesQuery.isFetching && (
              <p className="text-xs text-slate-500">正在刷新链路数据...</p>
            )}
            <TraceList
              traces={traces}
              selected={selectedRunId}
              onSelect={(trace) => {
                setSelectedRunId(trace.run_id);
              }}
            />
          </TabsContent>

          <TabsContent value="audit" className="grid gap-4">
            <section className="grid gap-3 rounded-md border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-[1.4fr_repeat(3,minmax(120px,0.8fr))]">
              <SearchBox
                value={auditQuery}
                onChange={setAuditQuery}
                placeholder="搜索用户、动作、路径、IP 或错误"
              />
              <Select value={auditResult} onValueChange={setAuditResult}>
                <SelectTrigger className="w-full md:w-40">
                  <SelectValue placeholder="结果" />
                </SelectTrigger>
                <SelectContent>
                  {auditResultOptions.map((result) => (
                    <SelectItem key={result} value={result}>
                      {result === "all" ? "全部结果" : result}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={auditOperationType}
                onValueChange={setAuditOperationType}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="操作类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部操作</SelectItem>
                  <SelectItem value="management">管理操作</SelectItem>
                  <SelectItem value="api">普通调用</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={auditUser}
                onChange={(event) => setAuditUser(event.target.value)}
                placeholder="用户 ID"
              />
              <Button
                variant="outline"
                onClick={() => {
                  setAuditQuery("");
                  setAuditResult("all");
                  setAuditOperationType("all");
                  setAuditUser("");
                }}
              >
                清空筛选
              </Button>
            </section>
            {auditLogsQuery.isFetching && (
              <p className="text-xs text-slate-500">正在刷新审计日志...</p>
            )}
            <AuditTable logs={auditLogs} onSelect={setSelectedAuditLog} />
          </TabsContent>
        </Tabs>
      </div>

      <Dialog
        open={Boolean(selectedRunId)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedRunId(null);
          }
        }}
      >
        <DialogContent className="max-h-[88vh] overflow-auto sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>错误诊断详情</DialogTitle>
            <DialogDescription>
              Run
              级别指标、工具调用、节点和错误原因，用于定位一次请求为什么失败或变慢。
            </DialogDescription>
          </DialogHeader>
          {diagnosticsQuery.isLoading ? (
            <p className="text-sm text-slate-500">正在加载诊断详情</p>
          ) : diagnosticsQuery.data ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await navigator.clipboard.writeText(
                      diagnosticsQuery.data.run.run_id,
                    );
                    await recordDisposition("copy_run_id");
                  }}
                >
                  <CopyIcon className="size-4" />
                  复制 run_id
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await navigator.clipboard.writeText(
                      diagnosticsQuery.data.run.thread_id,
                    );
                    await recordDisposition("copy_thread_id");
                  }}
                >
                  <CopyIcon className="size-4" />
                  复制 thread_id
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    await recordDisposition("open_thread");
                    window.open(
                      `/workspace/chats/${encodeURIComponent(
                        diagnosticsQuery.data.run.thread_id,
                      )}`,
                      "_blank",
                    );
                  }}
                >
                  <ExternalLinkIcon className="size-4" />
                  跳转原会话
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={dispositionMutation.isPending}
                  onClick={() => void recordDisposition("retry_run")}
                >
                  <RotateCcwIcon className="size-4" />
                  重试 run
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={dispositionMutation.isPending}
                  onClick={() => void recordDisposition("cancel_run")}
                >
                  <SquareIcon className="size-4" />
                  取消卡住的 run
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={dispositionMutation.isPending}
                  onClick={() =>
                    void recordDisposition("mark_handled", {
                      status: "handled",
                      note: dispositionNote || null,
                    })
                  }
                >
                  <CheckCircleIcon className="size-4" />
                  标记已处理
                </Button>
              </div>
              <div className="grid gap-3 rounded-md border border-slate-200 bg-white p-3">
                <Textarea
                  value={dispositionNote}
                  onChange={(event) => setDispositionNote(event.target.value)}
                  placeholder="添加处置备注"
                />
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-slate-500">
                    当前状态：
                    {diagnosticsQuery.data.disposition_status ?? "未处理"}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={
                      !dispositionNote.trim() || dispositionMutation.isPending
                    }
                    onClick={async () => {
                      await recordDisposition("add_note", {
                        note: dispositionNote,
                      });
                      setDispositionNote("");
                    }}
                  >
                    <MessageSquareIcon className="size-4" />
                    保存备注
                  </Button>
                </div>
                {diagnosticsQuery.data.dispositions.length > 0 && (
                  <div className="max-h-32 space-y-2 overflow-auto border-t border-slate-100 pt-2">
                    {diagnosticsQuery.data.dispositions.map((item) => (
                      <div
                        key={item.id}
                        className="grid grid-cols-[120px_1fr] gap-3 text-xs text-slate-600"
                      >
                        <span>{shortTime(item.created_at)}</span>
                        <span className="truncate">
                          {item.action}
                          {item.note ? ` · ${item.note}` : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <DetailGrid
                rows={[
                  ["Run ID", diagnosticsQuery.data.run.run_id],
                  ["Thread ID", diagnosticsQuery.data.run.thread_id],
                  [
                    "用户",
                    diagnosticsQuery.data.run.email ??
                      diagnosticsQuery.data.run.user_id,
                  ],
                  ["状态", diagnosticsQuery.data.status],
                  ["模型", diagnosticsQuery.data.model],
                  ["Agent", diagnosticsQuery.data.agent],
                  ["最后成功节点", diagnosticsQuery.data.last_successful_node],
                  ["失败节点", diagnosticsQuery.data.failed_node],
                  ["耗时", duration(diagnosticsQuery.data.duration_ms)],
                  ["Token", diagnosticsQuery.data.tokens.total],
                  ["输入 Token", diagnosticsQuery.data.tokens.input],
                  ["输出 Token", diagnosticsQuery.data.tokens.output],
                  ["LLM 调用", diagnosticsQuery.data.tokens.llm_calls],
                  ["开始时间", shortTime(diagnosticsQuery.data.run.started_at)],
                  ["更新时间", shortTime(diagnosticsQuery.data.run.updated_at)],
                ]}
              />
              <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
                <p className="text-xs text-slate-500">用户问题</p>
                <p className="mt-1 text-sm whitespace-pre-wrap text-slate-900">
                  {diagnosticsQuery.data.user_question ?? "-"}
                </p>
              </div>
              {diagnosticsQuery.data.possible_causes.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {diagnosticsQuery.data.possible_causes.map((cause) => (
                    <span
                      key={cause.type}
                      className="rounded bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700"
                    >
                      可能原因：{cause.label} · {cause.confidence}
                    </span>
                  ))}
                </div>
              )}
              {diagnosticsQuery.data.error_stack && (
                <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                  <p className="mb-2 font-medium">错误栈 / 错误信息</p>
                  <pre className="max-h-48 overflow-auto text-xs whitespace-pre-wrap">
                    {diagnosticsQuery.data.error_stack}
                  </pre>
                </div>
              )}
              <div>
                <h3 className="mb-3 text-sm font-semibold text-slate-950">
                  工具调用
                </h3>
                {diagnosticsQuery.data.tool_calls.length === 0 ? (
                  <p className="text-sm text-slate-500">没有记录到工具调用</p>
                ) : (
                  <div className="overflow-hidden rounded-md border border-slate-200">
                    {diagnosticsQuery.data.tool_calls.map((tool) => (
                      <div
                        key={tool.id}
                        className="grid gap-3 border-b border-slate-100 px-3 py-3 text-sm last:border-b-0 md:grid-cols-[180px_90px_1fr]"
                      >
                        <span className="truncate font-medium text-slate-800">
                          {tool.name ?? tool.id}
                        </span>
                        <StatusPill status={tool.status} />
                        <span className="line-clamp-2 text-xs text-slate-600">
                          {tool.error ??
                            tool.result_preview ??
                            stringifyDetail(tool.args)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <h3 className="mb-3 text-sm font-semibold text-slate-950">
                  事件流
                </h3>
                <TraceEventList
                  events={diagnosticsQuery.data.events}
                  isLoading={diagnosticsQuery.isLoading}
                />
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">未找到这条运行记录</p>
          )}
        </DialogContent>
      </Dialog>

      <Dialog
        open={Boolean(selectedAuditLog)}
        onOpenChange={(open) => {
          if (!open) setSelectedAuditLog(null);
        }}
      >
        <DialogContent className="max-h-[88vh] overflow-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>审计详情</DialogTitle>
            <DialogDescription>
              谁在何时对什么做了什么，以及请求结果和上下文。
            </DialogDescription>
          </DialogHeader>
          {selectedAuditLog && (
            <div className="space-y-4">
              <DetailGrid
                rows={[
                  ["时间", shortTime(selectedAuditLog.created_at)],
                  [
                    "操作者",
                    selectedAuditLog.user_email ?? selectedAuditLog.user_id,
                  ],
                  ["角色", selectedAuditLog.user_role],
                  [
                    "操作类型",
                    selectedAuditLog.operation_type === "management"
                      ? "管理操作"
                      : "普通调用",
                  ],
                  ["动作", selectedAuditLog.action],
                  ["资源类型", selectedAuditLog.resource_type],
                  ["资源 ID", selectedAuditLog.resource_id],
                  [
                    "请求",
                    `${selectedAuditLog.method} ${selectedAuditLog.path}`,
                  ],
                  ["状态码", selectedAuditLog.status_code],
                  ["结果", selectedAuditLog.result],
                  ["耗时", duration(selectedAuditLog.duration_ms)],
                  ["客户端 IP", selectedAuditLog.client_ip],
                  ["User-Agent", selectedAuditLog.user_agent],
                ]}
              />
              {selectedAuditLog.error && (
                <div className="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {selectedAuditLog.error}
                </div>
              )}
              <div>
                <h3 className="mb-2 text-sm font-semibold text-slate-950">
                  详情
                </h3>
                <pre className="max-h-60 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
                  {stringifyDetail(selectedAuditLog.details)}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </main>
  );
}
