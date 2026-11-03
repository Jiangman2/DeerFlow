"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useModelMonitor } from "@/core/console/model-monitor";
import { useI18n } from "@/core/i18n/hooks";
import { modelMonitorCopy, oaMonitorCopy } from "@/core/i18n/model-monitor";

function Trend({
  title,
  points,
  locale,
  missing,
}: {
  title: string;
  points: { at: number; value: number | null }[];
  locale: string;
  missing: string;
}) {
  const maximum = Math.max(1, ...points.map((p) => p.value ?? 0));
  const x = (i: number) => 30 + (i / Math.max(1, points.length - 1)) * 560;
  const y = (v: number) => 135 - (v / maximum) * 115;
  let path = "";
  let connected = false;
  points.forEach((p, i) => {
    if (p.value === null) connected = false;
    else {
      path += `${connected ? "L" : "M"}${x(i)},${y(p.value)} `;
      connected = true;
    }
  });
  const time = (at: number) =>
    new Date(at * 1000).toLocaleTimeString(locale, {
      hour: "2-digit",
      minute: "2-digit",
    });
  return (
    <div className="min-w-0 rounded-lg border p-3">
      <h3 className="mb-3 text-sm font-medium">{title}</h3>
      <svg
        viewBox="0 0 610 165"
        role="img"
        aria-label={title}
        className="text-primary w-full"
      >
        <path
          d="M30 20V135H590"
          fill="none"
          stroke="currentColor"
          opacity="0.15"
        />
        <text x="5" y="24" fill="currentColor" fontSize="10">
          {maximum}
        </text>
        <text x="10" y="137" fill="currentColor" fontSize="10">
          0
        </text>
        <path d={path} fill="none" stroke="currentColor" strokeWidth="2" />
        {points.map((p, i) =>
          p.value === null ? null : (
            <circle
              key={p.at}
              cx={x(i)}
              cy={y(p.value)}
              r="2.5"
              fill="currentColor"
            >
              <title>
                {time(p.at)} · {p.value}
              </title>
            </circle>
          ),
        )}
        {points[0] ? (
          <text x="30" y="157" fill="currentColor" fontSize="10">
            {time(points[0].at)}
          </text>
        ) : null}
        {points.at(-1) ? (
          <text
            x="590"
            y="157"
            textAnchor="end"
            fill="currentColor"
            fontSize="10"
          >
            {time(points.at(-1)!.at)}
          </text>
        ) : null}
      </svg>
      <details className="text-muted-foreground text-xs">
        <summary>{title}</summary>
        <div className="mt-2 max-h-32 overflow-auto">
          {points.map((p) => (
            <div key={p.at}>
              {time(p.at)} · {p.value ?? missing}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

export function ModelMonitorPanel({
  scope = "models",
  enabled = true,
}: {
  scope?: "models" | "oa";
  enabled?: boolean;
}) {
  const { locale } = useI18n();
  const copy =
    scope === "oa" ? oaMonitorCopy(locale) : modelMonitorCopy(locale);
  const [minutes, setMinutes] = useState<15 | 60>(15);
  const [selected, setSelected] = useState("");
  const query = useModelMonitor(minutes, enabled);
  const data = query.isError ? undefined : query.data;
  const observed = data?.status === "observed";
  const rows = observed
    ? data.series.filter((row) =>
        scope === "oa"
          ? row.kind === "integration" &&
            row.model === "oa-policy-flow" &&
            row.operation !== "job"
          : row.kind !== "integration",
      )
    : [];
  const jobTotals = observed
    ? data.series.find((row) => row.key === "integration:job:oa-policy-flow")
    : undefined;
  const jobSummary = [
    [
      locale.startsWith("zh") ? "最近观测执行中" : "Observed running",
      data?.oa_jobs?.status === "observed" ? data.oa_jobs.running : null,
    ],
    [locale.startsWith("zh") ? "新受理任务" : "New jobs", jobTotals?.started],
    [
      locale.startsWith("zh") ? "生成成功" : "Jobs succeeded",
      jobTotals?.success,
    ],
    [locale.startsWith("zh") ? "生成失败" : "Jobs failed", jobTotals?.error],
    [
      locale.startsWith("zh") ? "平均任务耗时（秒）" : "Mean job duration (s)",
      jobTotals?.average_seconds,
    ],
  ] as const;
  const current = rows.find((row) => row.key === selected) ?? rows[0];
  const number = (n: number) => n.toLocaleString(locale);
  const label = (r: { model: string; operation: string }) =>
    `${scope === "oa" ? (locale.startsWith("zh") ? "OA 文生图" : "OA diagrams") : r.model} · ${copy.operations[r.operation] ?? r.operation}`;
  const summary = [
    [copy.current, rows.reduce((sum, r) => sum + r.active, 0)],
    [copy.starts, rows.reduce((sum, r) => sum + r.started, 0)],
    [copy.success, rows.reduce((sum, r) => sum + r.success, 0)],
    [copy.errors, rows.reduce((sum, r) => sum + r.error + r.timeout, 0)],
  ] as const;
  const status =
    query.isError || data?.status === "unavailable"
      ? copy.unavailable
      : data?.status === "not_configured"
        ? copy.notConfigured
        : copy.stale;
  return (
    <Card
      className="min-w-0"
      data-testid={scope === "oa" ? "oa-policy-flow-monitor" : "model-monitor"}
    >
      <CardHeader>
        <CardTitle>{copy.title}</CardTitle>
        <CardDescription>{copy.subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="flex min-w-0 flex-col gap-5">
        {scope === "oa" && (
          <section
            aria-label={
              locale.startsWith("zh") ? "后台任务执行" : "Background jobs"
            }
            className="rounded-lg border p-4"
          >
            <h3 className="mb-3 text-sm font-medium">
              {locale.startsWith("zh") ? "后台任务执行" : "Background jobs"}
            </h3>
            <dl className="grid grid-cols-2 gap-4 xl:grid-cols-5">
              {jobSummary.map(([title, value]) => (
                <div key={title}>
                  <dd className="text-xl font-semibold tabular-nums">
                    {observed && value != null
                      ? number(Math.round(value * 10) / 10)
                      : "—"}
                  </dd>
                  <dt className="text-muted-foreground text-xs">{title}</dt>
                </div>
              ))}
            </dl>
            <p className="text-muted-foreground mt-3 text-xs">
              {locale.startsWith("zh")
                ? "复用工作进程已有的任务状态，不新增数据库查询。执行数是最近观测值，状态变化需等工作进程下一次检查；观测失联最长 15 分钟后过期。成功与失败仅在任务状态提交后计数，耗时从受理到结束。采集启用前及进程异常时可能缺失，不作为审计账本。"
                : "Reuses existing worker observations without additional SQL. Running counts are last observed states; disconnected observations expire within 15 minutes. Outcomes count committed task transitions, with duration from acceptance to completion. Collection gaps and pre-activation history are not reconstructed."}
            </p>
          </section>
        )}
        <div className="flex flex-wrap gap-2">
          <Select
            value={String(minutes)}
            onValueChange={(value) => setMinutes(value === "60" ? 60 : 15)}
          >
            <SelectTrigger className="w-auto" aria-label={copy.window}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="15">{copy.last15}</SelectItem>
                <SelectItem value="60">{copy.last60}</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            {copy.refresh}
          </Button>
        </div>
        <dl className="grid grid-cols-2 gap-4 xl:grid-cols-4">
          {summary.map(([title, value]) => (
            <div key={title}>
              <dd className="text-2xl font-semibold tabular-nums">
                {observed ? number(value) : "—"}
              </dd>
              <dt className="text-muted-foreground text-xs">{title}</dt>
            </div>
          ))}
        </dl>
        {query.isPending ? (
          <Skeleton className="h-48 w-full" aria-label={copy.loading} />
        ) : !observed ? (
          <p role="status" className="text-muted-foreground text-sm">
            {status}
          </p>
        ) : !current ? (
          <p role="status">{copy.empty}</p>
        ) : (
          <>
            <Select value={current.key} onValueChange={setSelected}>
              <SelectTrigger
                className="w-full max-w-lg"
                aria-label={copy.model}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {rows.map((row) => (
                    <SelectItem value={row.key} key={row.key}>
                      {label(row)}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
            <div className="grid min-w-0 gap-4 lg:grid-cols-2">
              <Trend
                title={copy.calls}
                locale={locale}
                missing={copy.missing}
                points={data.trend.map((p) => ({
                  at: p.at,
                  value: p.series?.[current.key]?.started ?? null,
                }))}
              />
              <Trend
                title={copy.concurrency}
                locale={locale}
                missing={copy.missing}
                points={data.trend.map((p) => ({
                  at: p.at,
                  value: p.concurrency?.[current.key] ?? null,
                }))}
              />
            </div>
            <div className="max-h-80 min-w-0 overflow-auto">
              <table className="w-full text-left text-sm [&_td]:px-3 [&_td]:py-2 [&_th]:px-3 [&_th]:py-2 [&_tr]:border-b">
                <thead>
                  <tr>
                    {[
                      copy.label,
                      copy.active,
                      copy.started,
                      copy.completed,
                      copy.failed,
                      copy.timeout,
                      copy.cancelled,
                      copy.peak,
                      copy.average,
                    ].map((t) => (
                      <th key={t} className="whitespace-nowrap">
                        {t}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key}>
                      <td className="min-w-52">{label(row)}</td>
                      {[
                        row.active,
                        row.started,
                        row.success,
                        row.error,
                        row.timeout,
                        row.cancelled,
                        row.peak_concurrency,
                      ].map((v, i) => (
                        <td key={i} className="tabular-nums">
                          {v === null ? "—" : number(v)}
                        </td>
                      ))}
                      <td className="whitespace-nowrap tabular-nums">
                        {row.average_seconds === null
                          ? "—"
                          : `${row.average_seconds.toFixed(1)} ${copy.seconds}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </CardContent>
      <CardFooter className="text-muted-foreground flex flex-col items-start gap-2 text-xs leading-relaxed">
        <p>{copy.coverage}</p>
        {data ? (
          <p>
            {copy.processes}: {data.reporting_processes} · {copy.dropped}:{" "}
            {data.dropped_observations ?? 0} · {copy.updated}:{" "}
            {new Date(data.generated_at * 1000).toLocaleTimeString(locale)}
          </p>
        ) : null}
      </CardFooter>
    </Card>
  );
}
