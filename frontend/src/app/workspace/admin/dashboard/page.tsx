"use client";

import {
  ActivityIcon,
  AlertTriangleIcon,
  BarChart3Icon,
  BotIcon,
  CpuIcon,
  DatabaseIcon,
  ExternalLinkIcon,
  FileArchiveIcon,
  FilterIcon,
  LineChartIcon,
  PackageIcon,
  RefreshCwIcon,
  ThumbsUpIcon,
  UsersIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDashboardSummary } from "@/core/dashboard";
import type {
  AgentUsageItem,
  ArtifactSummary,
  BusinessValueSummary,
  DashboardFilters,
  FeedbackSummary,
  FeatureUsageItem,
  ModelUsageItem,
  RecentBusinessEventItem,
  RecentFailureItem,
  UserActivityPoint,
} from "@/core/dashboard";
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

function minutes(value: number): string {
  if (!value) return "-";
  if (value < 60) return `${value} 分钟`;
  return `${Math.round(value / 6) / 10} 小时`;
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

function statusLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    all: "全部状态",
    success: "成功",
    error: "失败",
    timeout: "超时",
    interrupted: "中断",
    running: "运行中",
    pending: "等待中",
  };
  return labels[value ?? ""] ?? value ?? "-";
}

function valueProfileLabel(value: string | undefined): string {
  if (value === "standard") return "标准折算";
  return "保守折算";
}

function uniqueOptions(
  current: string,
  items: Array<{ value: string; label: string }>,
) {
  if (
    !current ||
    current === "all" ||
    items.some((item) => item.value === current)
  ) {
    return items;
  }
  return [{ value: current, label: current }, ...items];
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

function BarRow({
  label,
  value,
  max,
  detail,
}: {
  label: string;
  value: number;
  max: number;
  detail?: string;
}) {
  const width = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 0;
  return (
    <div className="grid min-w-0 grid-cols-[minmax(72px,140px)_minmax(0,1fr)_auto] items-center gap-3 text-sm sm:grid-cols-[minmax(96px,160px)_minmax(0,1fr)_auto]">
      <div className="min-w-0 truncate text-slate-700" title={label}>
        {label}
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div
          className="h-full rounded-full bg-slate-800"
          style={{ width: `${width}%` }}
        />
      </div>
      <div className="min-w-16 text-right text-slate-500">
        {detail ?? compactNumber(value)}
      </div>
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

function FeatureUsage({ items }: { items: FeatureUsageItem[] }) {
  const max = Math.max(0, ...items.map((item) => item.count));
  if (items.length === 0) return <EmptyState label="暂无功能使用数据" />;

  return (
    <div className="space-y-3">
      {items.map((item) => (
        <BarRow
          key={item.name}
          label={item.name}
          value={item.count}
          max={max}
        />
      ))}
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
  if (items.length === 0) return <EmptyState label="暂无模型使用数据" />;

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_40px_52px_52px] gap-2 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500 sm:grid-cols-[minmax(0,1fr)_72px_96px_96px] sm:gap-3 sm:px-4">
        <div>模型</div>
        <div className="text-right">运行</div>
        <div className="text-right">Token</div>
        <div className="text-right">成功率</div>
      </div>
      {items.map((item) => (
        <div
          key={item.model_name}
          className="grid min-w-0 grid-cols-[minmax(0,1fr)_40px_52px_52px] gap-2 border-t px-3 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_72px_96px_96px] sm:gap-3 sm:px-4"
        >
          <div className="min-w-0">
            <div
              className="truncate font-medium text-slate-800"
              title={item.model_name}
            >
              {item.model_name}
            </div>
            <div className="truncate text-xs text-slate-500">
              调用 {compactNumber(item.llm_calls)}，均耗时{" "}
              {duration(item.avg_duration_ms)}
            </div>
          </div>
          <div className="text-right text-slate-700">{item.runs}</div>
          <div className="text-right text-slate-700">
            {compactNumber(item.total_tokens)}
          </div>
          <div className="text-right text-slate-700">
            {percent(item.success_rate)}
          </div>
        </div>
      ))}
    </div>
  );
}

function BusinessValue({ summary }: { summary?: BusinessValueSummary }) {
  const contributions = summary?.contributions ?? [];

  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <LineChartIcon className="size-4" />
          业务价值
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid min-w-0 gap-3 md:grid-cols-[220px_minmax(0,1fr)]">
          <div className="rounded-lg border bg-slate-900 p-4 text-white">
            <div className="text-xs text-slate-300">
              {valueProfileLabel(summary?.profile)}
            </div>
            <div className="mt-2 text-2xl font-semibold">
              {minutes(summary?.estimated_minutes_saved ?? 0)}
            </div>
            <div className="mt-2 text-xs text-slate-300">
              基于运行事件、产物记录和业务动作做粗略折算
            </div>
          </div>
          {contributions.length > 0 ? (
            <div className="grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-5">
              {contributions.map((item) => (
                <div key={item.name} className="rounded-lg border bg-white p-4">
                  <div className="text-xs text-slate-500">{item.name}</div>
                  <div className="mt-2 text-2xl font-semibold text-slate-950">
                    {compactNumber(item.count)}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {item.minutes_per_event} 分钟/次，贡献{" "}
                    {minutes(item.estimated_minutes)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState label="暂无可折算的业务价值事件" />
          )}
        </div>
        <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="rounded-lg border bg-white p-4">
            <div className="text-xs text-slate-500">产物生成</div>
            <div className="mt-2 text-xl font-semibold text-slate-950">
              {compactNumber(summary?.artifact_runs ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-xs text-slate-500">知识检索</div>
            <div className="mt-2 text-xl font-semibold text-slate-950">
              {compactNumber(summary?.knowledge_runs ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-xs text-slate-500">文件处理</div>
            <div className="mt-2 text-xl font-semibold text-slate-950">
              {compactNumber(summary?.file_processing_runs ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-xs text-slate-500">材料生成</div>
            <div className="mt-2 text-xl font-semibold text-slate-950">
              {compactNumber(summary?.presentation_runs ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-xs text-slate-500">图片生成</div>
            <div className="mt-2 text-xl font-semibold text-slate-950">
              {compactNumber(summary?.image_runs ?? 0)}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ArtifactPanel({ summary }: { summary?: ArtifactSummary }) {
  const max = Math.max(
    0,
    ...(summary?.by_type ?? []).map((item) => item.count),
  );
  return (
    <Card className="min-w-0 rounded-lg shadow-none">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PackageIcon className="size-4" />
          产物统计
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid min-w-0 grid-cols-2 gap-3">
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">产物总数</div>
            <div className="mt-2 text-2xl font-semibold">
              {compactNumber(summary?.total ?? 0)}
            </div>
          </div>
          <div className="rounded-lg border bg-white p-4">
            <div className="text-sm text-slate-500">可预览产物</div>
            <div className="mt-2 text-2xl font-semibold">
              {compactNumber(summary?.previewable ?? 0)}
            </div>
          </div>
        </div>
        {(summary?.by_type.length ?? 0) > 0 ? (
          <div className="space-y-3">
            {summary?.by_type.map((item) => (
              <BarRow
                key={item.type}
                label={item.type}
                value={item.count}
                max={max}
              />
            ))}
          </div>
        ) : (
          <EmptyState label="暂无产物类型数据" />
        )}
        {(summary?.recent.length ?? 0) > 0 ? (
          <div className="space-y-2">
            {summary?.recent.map((item) => (
              <div
                key={`${item.run_id}-${item.path}`}
                className="rounded-lg border p-3 text-sm"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="rounded bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">
                    {item.type}
                  </span>
                  <Link
                    href={`/workspace/admin/observability?run_id=${encodeURIComponent(item.run_id)}`}
                    className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-950"
                  >
                    诊断
                    <ExternalLinkIcon className="size-3" />
                  </Link>
                </div>
                <div className="mt-2 truncate text-slate-700" title={item.path}>
                  {item.path}
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  {shortTime(item.created_at)}
                </div>
              </div>
            ))}
          </div>
        ) : null}
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

function RecentFailures({ items }: { items: RecentFailureItem[] }) {
  if (items.length === 0)
    return <EmptyState label="当前时间范围内没有失败任务" />;

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="grid min-w-0 grid-cols-[64px_48px_minmax(0,1fr)_54px] gap-2 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500 sm:grid-cols-[140px_96px_minmax(0,1fr)_96px_92px] sm:gap-3 sm:px-4">
        <div>时间</div>
        <div>状态</div>
        <div>错误</div>
        <div className="hidden text-right sm:block">处置</div>
        <div className="text-right">操作</div>
      </div>
      {items.map((item) => (
        <div
          key={item.run_id}
          className="grid min-w-0 grid-cols-[64px_48px_minmax(0,1fr)_54px] gap-2 border-t px-3 py-3 text-sm sm:grid-cols-[140px_96px_minmax(0,1fr)_96px_92px] sm:gap-3 sm:px-4"
        >
          <div className="text-slate-500">{shortTime(item.started_at)}</div>
          <div>
            <span className="rounded bg-red-50 px-2 py-1 text-xs font-medium text-red-700">
              {item.status}
            </span>
          </div>
          <div
            className="min-w-0 truncate text-slate-700"
            title={item.error ?? item.run_id}
          >
            {item.error ?? item.run_id}
          </div>
          <div className="hidden text-right sm:block">
            <span className="rounded bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
              {item.disposition_status ?? "未处置"}
            </span>
          </div>
          <div className="text-right">
            <Link
              href={item.diagnostics_url}
              className="inline-flex items-center justify-end gap-1 text-xs text-slate-600 hover:text-slate-950"
            >
              诊断
              <ExternalLinkIcon className="size-3" />
            </Link>
          </div>
        </div>
      ))}
    </div>
  );
}

function BusinessEvents({ items }: { items: RecentBusinessEventItem[] }) {
  if (items.length === 0) return <EmptyState label="暂无结构化业务事件" />;

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div
          key={`${item.run_id}-${item.created_at}-${item.event_type}`}
          className="rounded-lg border p-3 text-sm"
        >
          <div className="flex items-center justify-between gap-3">
            <span className="truncate font-medium text-slate-700">
              {item.event_type}
            </span>
            <Link
              href={`/workspace/admin/observability?run_id=${encodeURIComponent(item.run_id)}`}
              className="inline-flex shrink-0 items-center gap-1 text-xs text-slate-500 hover:text-slate-950"
            >
              诊断
              <ExternalLinkIcon className="size-3" />
            </Link>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {item.features.map((feature) => (
              <span
                key={feature}
                className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600"
              >
                {feature}
              </span>
            ))}
          </div>
          <div className="mt-2 line-clamp-2 text-slate-500">
            {item.snippet || item.run_id}
          </div>
          <div className="mt-1 text-xs text-slate-400">
            {shortTime(item.created_at)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const [hours, setHours] = useState(168);
  const [status, setStatus] = useState("all");
  const [model, setModel] = useState("all");
  const [agent, setAgent] = useState("all");
  const [feature, setFeature] = useState("all");
  const [valueProfile, setValueProfile] = useState("conservative");
  const filters = useMemo<DashboardFilters>(
    () => ({
      hours,
      status,
      model,
      agent,
      feature,
      value_profile: valueProfile,
    }),
    [agent, feature, hours, model, status, valueProfile],
  );
  const summaryQuery = useDashboardSummary(filters);
  const summary = summaryQuery.data;

  const windowLabel = useMemo(() => {
    if (!summary) return "加载中";
    return `${shortTime(summary.window.start_at)} - ${shortTime(summary.window.end_at)}`;
  }, [summary]);
  const hasAdvancedFilters =
    status !== "all" || model !== "all" || agent !== "all" || feature !== "all";
  const modelOptions = uniqueOptions(
    model,
    (summary?.model_usage ?? []).map((item) => ({
      value: item.model_name,
      label: item.model_name,
    })),
  );
  const agentOptions = uniqueOptions(
    agent,
    (summary?.agent_usage ?? []).map((item) => ({
      value: item.assistant_id ?? "default",
      label: item.name,
    })),
  );
  const featureOptions = uniqueOptions(
    feature,
    (summary?.feature_usage ?? []).map((item) => ({
      value: item.name,
      label: item.name,
    })),
  );

  const resetFilters = () => {
    setStatus("all");
    setModel("all");
    setAgent("all");
    setFeature("all");
  };

  return (
    <main className="min-h-screen w-full max-w-full overflow-x-hidden bg-slate-50/60 px-4 py-6 text-slate-950 sm:px-6">
      <div className="mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-6">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-normal">业务看板</h1>
            <p className="mt-1 max-w-full text-sm break-words text-slate-500">
              {windowLabel}，展示业务价值、用户活跃、运行质量和近期失败任务。
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Select
              value={String(hours)}
              onValueChange={(value) => setHours(Number(value))}
            >
              <SelectTrigger className="w-36 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="24">最近 24 小时</SelectItem>
                <SelectItem value="168">最近 7 天</SelectItem>
                <SelectItem value="720">最近 30 天</SelectItem>
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              className="bg-white"
              onClick={() => summaryQuery.refetch()}
              disabled={summaryQuery.isFetching}
            >
              <RefreshCwIcon
                className={cn(
                  "size-4",
                  summaryQuery.isFetching && "animate-spin",
                )}
              />
              刷新
            </Button>
          </div>
        </div>

        <Card className="min-w-0 rounded-lg py-4 shadow-none">
          <CardContent className="flex flex-col gap-3 px-4 sm:px-5">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
              <FilterIcon className="size-4" />
              多维筛选
            </div>
            <div className="grid min-w-0 gap-3 md:grid-cols-2 xl:grid-cols-5">
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger className="bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="success">成功</SelectItem>
                  <SelectItem value="error">失败</SelectItem>
                  <SelectItem value="timeout">超时</SelectItem>
                  <SelectItem value="interrupted">中断</SelectItem>
                  <SelectItem value="running">运行中</SelectItem>
                  <SelectItem value="pending">等待中</SelectItem>
                </SelectContent>
              </Select>
              <Select value={feature} onValueChange={setFeature}>
                <SelectTrigger className="bg-white">
                  <SelectValue placeholder="全部功能" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部功能</SelectItem>
                  {featureOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={model} onValueChange={setModel}>
                <SelectTrigger className="bg-white">
                  <SelectValue placeholder="全部模型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部模型</SelectItem>
                  {modelOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={agent} onValueChange={setAgent}>
                <SelectTrigger className="bg-white">
                  <SelectValue placeholder="全部智能体" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部智能体</SelectItem>
                  {agentOptions.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={valueProfile} onValueChange={setValueProfile}>
                <SelectTrigger className="bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="conservative">保守折算</SelectItem>
                  <SelectItem value="standard">标准折算</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {hasAdvancedFilters ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
                <span className="min-w-0 break-words">
                  当前筛选：{statusLabel(status)} /{" "}
                  {feature === "all" ? "全部功能" : feature} /{" "}
                  {model === "all" ? "全部模型" : model} /{" "}
                  {agent === "all" ? "全部智能体" : agent}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={resetFilters}
                >
                  清空筛选
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>

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
            label="业务价值"
            value={minutes(summary?.overview.estimated_minutes_saved ?? 0)}
            detail={`产物 ${compactNumber(summary?.overview.artifact_runs ?? 0)}，知识检索 ${compactNumber(summary?.overview.knowledge_runs ?? 0)}`}
            icon={LineChartIcon}
          />
        </div>

        <BusinessValue summary={summary?.business_value} />

        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <ArtifactPanel summary={summary?.artifact_summary} />

          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ActivityIcon className="size-4" />
                业务事件
              </CardTitle>
            </CardHeader>
            <CardContent>
              <BusinessEvents items={summary?.recent_business_events ?? []} />
            </CardContent>
          </Card>
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
                <FileArchiveIcon className="size-4" />
                功能使用
              </CardTitle>
            </CardHeader>
            <CardContent>
              <FeatureUsage items={summary?.feature_usage ?? []} />
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

          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <DatabaseIcon className="size-4" />
                知识库使用
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid min-w-0 grid-cols-2 gap-3">
                <div className="rounded-lg border bg-white p-4">
                  <div className="text-sm text-slate-500">检索运行</div>
                  <div className="mt-2 text-2xl font-semibold">
                    {compactNumber(summary?.knowledge_usage.runs ?? 0)}
                  </div>
                </div>
                <div className="rounded-lg border bg-white p-4">
                  <div className="text-sm text-slate-500">检索事件</div>
                  <div className="mt-2 text-2xl font-semibold">
                    {compactNumber(summary?.knowledge_usage.tool_calls ?? 0)}
                  </div>
                </div>
              </div>
              {(summary?.knowledge_usage.recent_tool_calls.length ?? 0) > 0 ? (
                <div className="space-y-2">
                  {summary?.knowledge_usage.recent_tool_calls.map((item) => (
                    <div
                      key={`${item.run_id}-${item.created_at}`}
                      className="rounded-lg border p-3 text-sm"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium text-slate-700">
                          {item.event_type}
                        </span>
                        <span className="text-xs text-slate-500">
                          {shortTime(item.created_at)}
                        </span>
                      </div>
                      <div className="mt-1 line-clamp-2 text-slate-500">
                        {item.snippet || item.run_id}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState label="暂无知识库检索记录" />
              )}
            </CardContent>
          </Card>
        </div>

        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <Card className="min-w-0 rounded-lg shadow-none">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <CpuIcon className="size-4" />
                模型排行
              </CardTitle>
            </CardHeader>
            <CardContent className="min-w-0 overflow-hidden">
              <ModelRanking items={summary?.model_usage ?? []} />
            </CardContent>
          </Card>

          <FeedbackPanel summary={summary?.feedback} />
        </div>

        <Card className="min-w-0 rounded-lg shadow-none">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangleIcon className="size-4" />
              近期失败任务
            </CardTitle>
          </CardHeader>
          <CardContent>
            <RecentFailures items={summary?.recent_failures ?? []} />
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
