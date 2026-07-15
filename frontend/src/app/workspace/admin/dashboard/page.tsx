"use client";

import type { LucideIcon } from "lucide-react";
import {
  ActivityIcon,
  BarChart3Icon,
  BotIcon,
  CheckCircle2Icon,
  ClockIcon,
  HeartIcon,
  RefreshCwIcon,
  SparklesIcon,
  TrendingUpIcon,
  UsersIcon,
  ZapIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useDashboardSummary } from "@/core/dashboard";
import type {
  BusinessValueSummary,
  DashboardFilters,
  FeatureUsageItem,
  FeedbackSummary,
  UserActivityPoint,
  UserUsageItem,
} from "@/core/dashboard";
import { cn } from "@/lib/utils";

const FEATURE_LABELS: Record<string, string> = {
  artifacts: "产物生成",
  artifact: "产物生成",
  knowledge: "知识库问答",
  file_processing: "文件处理",
  presentation: "汇报材料",
  image: "图像处理",
  deep_research: "深度研究",
  scheduled_task: "定时任务",
  agent: "智能体",
  chat: "对话问答",
};

function compactNumber(value: number): string {
  return Intl.NumberFormat("zh-CN", { notation: "compact" }).format(value);
}

function percent(value: number): string {
  if (!Number.isFinite(value)) return "-";
  return `${Math.round(value * 1000) / 10}%`;
}

function duration(ms: number): string {
  if (!ms) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${Math.round(ms / 100) / 10}s`;
  return `${Math.round(ms / 6000) / 10} 分钟`;
}

function minutes(value: number): string {
  if (!value) return "-";
  if (value < 60) return `${Math.round(value)} 分钟`;
  return `${Math.round(value / 6) / 10} 小时`;
}

function safeRatio(part: number, total: number): number {
  if (!total || total <= 0) return 0;
  return part / total;
}

function featureLabel(name: string): string {
  return FEATURE_LABELS[name] ?? FEATURE_LABELS[name.toLowerCase()] ?? name;
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

function windowLabel(hours: number, startAt?: string, endAt?: string): string {
  if (startAt && endAt) {
    return `自定义区间 · ${hours} 小时`;
  }
  if (hours === 1) return "最近 1 小时";
  if (hours === 24) return "最近 24 小时";
  if (hours === 168) return "最近 7 天";
  return `最近 ${hours} 小时`;
}

function EmptyState({ label }: { label: string }) {
  return (
    <div className="flex min-h-32 items-center justify-center rounded-md border border-dashed border-slate-200 bg-slate-50 text-sm text-slate-500">
      {label}
    </div>
  );
}

function ExecutiveMetric({
  title,
  value,
  caption,
  icon: Icon,
  tone = "blue",
  progress,
}: {
  title: string;
  value: string;
  caption: string;
  icon: LucideIcon;
  tone?: "blue" | "green" | "amber" | "red" | "slate";
  progress?: number;
}) {
  const toneClass = {
    blue: "bg-blue-50 text-blue-700",
    green: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    red: "bg-red-50 text-red-700",
    slate: "bg-slate-100 text-slate-700",
  }[tone];

  const barClass = {
    blue: "bg-blue-600",
    green: "bg-emerald-600",
    amber: "bg-amber-500",
    red: "bg-red-500",
    slate: "bg-slate-500",
  }[tone];

  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-medium text-slate-600">{title}</div>
          <div className="mt-2 text-2xl font-semibold text-slate-950">
            {value}
          </div>
        </div>
        <span className={cn("rounded-md p-2", toneClass)}>
          <Icon className="size-4" />
        </span>
      </div>
      <div className="mt-2 text-xs leading-5 text-slate-500">{caption}</div>
      {progress !== undefined ? (
        <div className="mt-3 h-1.5 rounded-full bg-slate-100">
          <div
            className={cn("h-1.5 rounded-full", barClass)}
            style={{
              width: `${Math.max(3, Math.min(100, progress * 100))}%`,
            }}
          />
        </div>
      ) : null}
    </div>
  );
}

function InsightBlock({
  title,
  value,
  detail,
  icon: Icon,
}: {
  title: string;
  value: string;
  detail: string;
  icon: LucideIcon;
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-700">
        <Icon className="size-4 text-blue-700" />
        {title}
      </div>
      <div className="mt-2 text-xl font-semibold text-slate-950">{value}</div>
      <div className="mt-1 text-xs leading-5 text-slate-500">{detail}</div>
    </div>
  );
}

function ActivityTrend({ items }: { items: UserActivityPoint[] }) {
  if (items.length === 0) {
    return <EmptyState label="暂无活跃趋势数据" />;
  }

  const maxRuns = Math.max(...items.map((item) => item.runs), 1);
  const maxActive = Math.max(...items.map((item) => item.active_users), 1);

  return (
    <div className="grid min-h-64 grid-cols-[repeat(auto-fit,minmax(34px,1fr))] items-end gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-4">
      {items.map((item) => {
        const runHeight = Math.max(8, Math.round((item.runs / maxRuns) * 100));
        const activeHeight = Math.max(
          8,
          Math.round((item.active_users / maxActive) * 100),
        );
        return (
          <div
            key={item.date}
            className="flex min-w-0 flex-col items-center gap-2"
            title={`${shortDate(item.date)}：${item.active_users} 位活跃用户，${item.runs} 次运行`}
          >
            <div className="flex h-44 items-end gap-1">
              <div
                className="w-2 rounded-t-sm bg-blue-600"
                style={{ height: `${runHeight}%` }}
              />
              <div
                className="w-2 rounded-t-sm bg-emerald-500"
                style={{ height: `${activeHeight}%` }}
              />
            </div>
            <div className="truncate text-[11px] text-slate-500">
              {shortDate(item.date)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function BarRow({
  label,
  value,
  max,
  suffix,
}: {
  label: string;
  value: number;
  max: number;
  suffix?: string;
}) {
  const width = max > 0 ? Math.max(3, Math.round((value / max) * 100)) : 0;
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="truncate text-slate-700">{label}</span>
        <span className="shrink-0 font-medium text-slate-950">
          {compactNumber(value)}
          {suffix}
        </span>
      </div>
      <div className="h-2 rounded-full bg-slate-100">
        <div
          className="h-2 rounded-full bg-blue-600"
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

function FeatureUsage({ items }: { items: FeatureUsageItem[] }) {
  const topItems = items.slice(0, 6);
  if (topItems.length === 0) {
    return <EmptyState label="暂无能力使用数据" />;
  }
  const max = Math.max(...topItems.map((item) => item.count), 1);

  return (
    <div className="grid gap-4">
      {topItems.map((item) => (
        <BarRow
          key={item.name}
          label={featureLabel(item.name)}
          value={item.count}
          max={max}
        />
      ))}
    </div>
  );
}

function UserUsageRanking({ items }: { items: UserUsageItem[] }) {
  const topItems = items.slice(0, 5);
  if (topItems.length === 0) {
    return <EmptyState label="暂无用户使用数据" />;
  }

  return (
    <div className="grid gap-3">
      {topItems.map((item, index) => (
        <div
          key={item.user_id}
          className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-3"
        >
          <div className="flex size-8 items-center justify-center rounded-md bg-white text-sm font-semibold text-blue-700 shadow-sm">
            {index + 1}
          </div>
          <div className="min-w-0">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate font-medium text-slate-900">
                  {item.display_name || item.account}
                </div>
                <div className="mt-0.5 truncate text-xs text-slate-500">
                  {item.account}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="text-base font-semibold text-slate-950">
                  {compactNumber(item.runs)}
                </div>
                <div className="text-xs text-slate-500">次使用</div>
              </div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
              <div className="rounded bg-white px-2 py-1">
                会话 {compactNumber(item.threads)}
              </div>
              <div className="rounded bg-white px-2 py-1">
                成功率 {percent(item.success_rate)}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function BusinessValuePanel({ summary }: { summary?: BusinessValueSummary }) {
  const contributions = summary?.contributions?.slice(0, 5) ?? [];
  const max = Math.max(
    ...contributions.map((item) => item.estimated_minutes),
    1,
  );

  return (
    <div className="grid gap-4">
      <div className="rounded-md border border-blue-100 bg-blue-50 p-4">
        <div className="text-sm text-blue-700">保守估算节省时间</div>
        <div className="mt-1 text-2xl font-semibold text-blue-950">
          {minutes(summary?.estimated_minutes_saved ?? 0)}
        </div>
        <div className="mt-1 text-xs text-blue-700/80">
          只统计可识别的文件处理、产物生成、知识库和汇报相关能力。
        </div>
      </div>
      {contributions.length > 0 ? (
        contributions.map((item) => (
          <BarRow
            key={item.name}
            label={featureLabel(item.name)}
            value={item.estimated_minutes}
            max={max}
            suffix=" 分钟"
          />
        ))
      ) : (
        <EmptyState label="暂无可估算的业务价值数据" />
      )}
    </div>
  );
}

function FeedbackPanel({ summary }: { summary?: FeedbackSummary }) {
  const total = summary?.total ?? 0;
  const positive = summary?.positive ?? 0;
  const negative = summary?.negative ?? 0;
  const satisfaction = summary?.satisfaction_rate ?? 0;

  if (total === 0) {
    return (
      <EmptyState label="暂无用户反馈数据，建议在试点阶段引导用户评价回答质量" />
    );
  }

  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
          <div className="text-xs text-slate-500">反馈总数</div>
          <div className="mt-1 text-xl font-semibold text-slate-950">
            {compactNumber(total)}
          </div>
        </div>
        <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
          <div className="text-xs text-emerald-700">正向反馈</div>
          <div className="mt-1 text-xl font-semibold text-emerald-950">
            {compactNumber(positive)}
          </div>
        </div>
        <div className="rounded-md border border-red-200 bg-red-50 p-3">
          <div className="text-xs text-red-700">待改进</div>
          <div className="mt-1 text-xl font-semibold text-red-950">
            {compactNumber(negative)}
          </div>
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-600">满意率</span>
          <span className="font-medium text-slate-950">
            {percent(satisfaction)}
          </span>
        </div>
        <div className="mt-2 h-2 rounded-full bg-slate-100">
          <div
            className="h-2 rounded-full bg-emerald-600"
            style={{ width: `${Math.max(3, satisfaction * 100)}%` }}
          />
        </div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const [hours, setHours] = useState(24);
  const [rangeMode, setRangeMode] = useState<"preset" | "custom">("preset");
  const [customStart, setCustomStart] = useState(() =>
    toDateTimeLocal(new Date(Date.now() - 24 * 60 * 60 * 1000)),
  );
  const [customEnd, setCustomEnd] = useState(() => toDateTimeLocal(new Date()));
  const rangeStart =
    rangeMode === "custom" ? isoFromDateTimeLocal(customStart) : undefined;
  const rangeEnd =
    rangeMode === "custom" ? isoFromDateTimeLocal(customEnd) : undefined;
  const customHours = hoursBetween(rangeStart, rangeEnd);
  const activeHours = rangeMode === "custom" ? (customHours ?? hours) : hours;
  const canApplyCustomRange = Boolean(
    isoFromDateTimeLocal(customStart) &&
      isoFromDateTimeLocal(customEnd) &&
      hoursBetween(isoFromDateTimeLocal(customStart), isoFromDateTimeLocal(customEnd)),
  );
  const filters = useMemo<DashboardFilters>(
    () => ({
      hours: activeHours,
      start_at: rangeStart,
      end_at: rangeEnd,
      value_profile: "conservative",
    }),
    [activeHours, rangeEnd, rangeStart],
  );
  const summaryQuery = useDashboardSummary(filters);
  const summary = summaryQuery.data;
  const overview = summary?.overview;

  const totalUsers = overview?.total_users ?? 0;
  const activeUsers = overview?.active_users ?? 0;
  const newUsers = overview?.new_users ?? 0;
  const runs = overview?.runs ?? 0;
  const threads = overview?.threads ?? 0;
  const failedRuns = overview?.failed_runs ?? 0;
  const successRate = overview?.success_rate ?? 0;
  const avgDuration = overview?.avg_run_duration_ms ?? 0;
  const satisfactionRate = overview?.satisfaction_rate ?? 0;
  const feedbackTotal = overview?.feedback_total ?? 0;
  const activationRate = safeRatio(activeUsers, totalUsers);
  const runsPerActive = activeUsers ? Math.round((runs / activeUsers) * 10) / 10 : 0;
  const valueMinutes =
    summary?.business_value?.estimated_minutes_saved ??
    overview?.estimated_minutes_saved ??
    0;
  const qualityTone = successRate >= 0.95 ? "green" : successRate >= 0.85 ? "amber" : "red";
  const satisfactionTone =
    feedbackTotal === 0 ? "slate" : satisfactionRate >= 0.9 ? "green" : satisfactionRate >= 0.75 ? "amber" : "red";

  return (
    <main className="h-full overflow-auto bg-slate-50/80 p-6 text-slate-950">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-slate-950">运营看板</h1>
            <p className="mt-1 text-sm text-slate-500">
              聚焦采用规模、使用深度、运行质量、业务价值和用户满意度。
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
                onClick={() => setRangeMode("custom")}
              >
                应用区间
              </Button>
            </div>
            <span className="text-xs text-slate-500">
              {windowLabel(activeHours, rangeStart, rangeEnd)}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => summaryQuery.refetch()}
              disabled={summaryQuery.isFetching}
            >
              <RefreshCwIcon
                className={cn(
                  "mr-2 size-4",
                  summaryQuery.isFetching && "animate-spin",
                )}
              />
              刷新
            </Button>
          </div>
        </header>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          <ExecutiveMetric
            title="用户采用"
            value={`${compactNumber(activeUsers)} / ${compactNumber(totalUsers)}`}
            caption={`新增 ${compactNumber(newUsers)} 人，激活率 ${percent(activationRate)}`}
            icon={UsersIcon}
            tone="blue"
            progress={activationRate}
          />
          <ExecutiveMetric
            title="使用规模"
            value={compactNumber(runs)}
            caption={`${compactNumber(threads)} 个会话，人均 ${runsPerActive || "-"} 次运行`}
            icon={ActivityIcon}
            tone="slate"
          />
          <ExecutiveMetric
            title="运行质量"
            value={percent(successRate)}
            caption={`${compactNumber(failedRuns)} 次失败，平均耗时 ${duration(avgDuration)}`}
            icon={CheckCircle2Icon}
            tone={qualityTone}
            progress={successRate}
          />
          <ExecutiveMetric
            title="业务价值"
            value={minutes(valueMinutes)}
            caption="按保守口径估算可节省的人工作业时间"
            icon={ZapIcon}
            tone="amber"
          />
          <ExecutiveMetric
            title="用户满意"
            value={feedbackTotal ? percent(satisfactionRate) : "-"}
            caption={`${compactNumber(feedbackTotal)} 条反馈，越高代表回答越被认可`}
            icon={HeartIcon}
            tone={satisfactionTone}
            progress={feedbackTotal ? satisfactionRate : undefined}
          />
        </section>

        <section className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <SparklesIcon className="size-4 text-blue-700" />
            <h2 className="text-base font-semibold text-slate-950">
              关键运营洞察
            </h2>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <InsightBlock
              title="使用深度"
              value={runsPerActive ? `人均 ${runsPerActive} 次运行` : "暂无活跃"}
              detail="反映用户是否把智能体真正用于日常工作，而不仅是登录试用。"
              icon={TrendingUpIcon}
            />
            <InsightBlock
              title="能力覆盖"
              value={`${compactNumber(summary?.feature_usage?.length ?? 0)} 类能力被调用`}
              detail="能力类型越丰富，说明产品从问答向文件、知识库、产物等工作流扩展。"
              icon={BotIcon}
            />
            <InsightBlock
              title="质量风险"
              value={failedRuns ? `${compactNumber(failedRuns)} 次失败` : "无明显风险"}
              detail="失败运行和低满意反馈需要优先进入运维页排查。"
              icon={ClockIcon}
            />
          </div>
        </section>

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
          <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  活跃与运行趋势
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  蓝色代表运行次数，绿色代表活跃用户数。
                </p>
              </div>
              <BarChart3Icon className="size-5 text-slate-400" />
            </div>
            <ActivityTrend items={summary?.user_activity ?? []} />
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  用户满意度
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  用真实反馈衡量回答质量和产品信任度。
                </p>
              </div>
              <HeartIcon className="size-5 text-slate-400" />
            </div>
            <FeedbackPanel summary={summary?.feedback} />
          </div>
        </section>

        <section className="grid gap-5 xl:grid-cols-3">
          <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  能力使用分布
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  看用户主要把产品用于哪些高价值场景。
                </p>
              </div>
              <ActivityIcon className="size-5 text-slate-400" />
            </div>
            <FeatureUsage items={summary?.feature_usage ?? []} />
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  用户使用排行榜
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  展示数据湖 SSO 用户的中文名、账号和使用次数。
                </p>
              </div>
              <UsersIcon className="size-5 text-slate-400" />
            </div>
            <UserUsageRanking items={summary?.user_usage ?? []} />
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-950">
                  业务价值贡献
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  只展示能直接体现提效的能力，不做复杂归因。
                </p>
              </div>
              <ZapIcon className="size-5 text-slate-400" />
            </div>
            <BusinessValuePanel summary={summary?.business_value} />
          </div>
        </section>
      </div>
    </main>
  );
}
