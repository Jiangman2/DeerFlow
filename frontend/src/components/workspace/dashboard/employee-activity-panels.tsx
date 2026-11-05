"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  MousePointer2Icon,
  SearchIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import Link from "next/link";
import { useDeferredValue, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";
import type { DashboardFilters } from "@/core/dashboard/types";

interface ClickItem {
  identity: string;
  name: string;
  source: string;
  clicks: number;
  users: number;
  total_clicks: number;
}
interface FeedbackItem {
  feedback_id: string;
  display_name: string | null;
  email: string | null;
  organization_name: string | null;
  rating: number;
  created_at: string;
  thread_title: string | null;
  thread_id: string;
  run_id: string;
}
interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}
const PAGE_SIZE = 10;
const FEEDBACK_PAGE_SIZE = 5;
const count = (value: number) => value.toLocaleString("zh-CN");
// The production dashboard has a bounded DB query slot. Do not make these
// two additional reports contend with one another, including manual refresh.
let reportQueue: Promise<unknown> = Promise.resolve();

function windowParams(filters: DashboardFilters) {
  const params = new URLSearchParams({ hours: String(filters.hours ?? 168) });
  if (filters.start_at) params.set("start_at", filters.start_at);
  if (filters.end_at) params.set("end_at", filters.end_at);
  return params;
}

function loadPage<T>(
  path: string,
  params: string,
  signal: AbortSignal,
): Promise<Page<T>> {
  const pending = reportQueue
    .catch(() => undefined)
    .then(async () => {
      signal.throwIfAborted();
      const result = await fetch(
        `${getBackendBaseURL()}/api/admin/dashboard/${path}?${params}`,
        { signal },
      );
      if (!result.ok) throw new Error("统计暂不可用");
      return (await result.json()) as Page<T>;
    });
  reportQueue = pending.catch(() => undefined);
  return pending;
}

function PageButtons({
  label,
  total,
  offset,
  onOffsetChange,
  busy,
  pageSize = PAGE_SIZE,
}: {
  label: string;
  total: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
  busy: boolean;
  pageSize?: number;
}) {
  return (
    <nav
      aria-label={`${label}分页`}
      className="text-muted-foreground mt-4 flex flex-wrap items-center justify-between gap-3 border-t pt-3 text-xs"
    >
      <span>
        共 {count(total)} 条 · 第 {Math.floor(offset / pageSize) + 1} /{" "}
        {Math.max(1, Math.ceil(total / pageSize))} 页
      </span>
      <div className="flex gap-2">
        <Button
          size="icon-sm"
          variant="outline"
          aria-label={`${label}上一页`}
          disabled={offset === 0 || busy}
          onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}
        >
          <ChevronLeftIcon />
        </Button>
        <Button
          size="icon-sm"
          variant="outline"
          aria-label={`${label}下一页`}
          disabled={offset + pageSize >= total || busy}
          onClick={() => onOffsetChange(offset + pageSize)}
        >
          <ChevronRightIcon />
        </Button>
      </div>
    </nav>
  );
}

function QueryState({
  error,
  loading,
  empty,
  retry,
}: {
  error: boolean;
  loading: boolean;
  empty: boolean;
  retry: () => void;
}) {
  if (error)
    return (
      <div
        role="alert"
        className="text-muted-foreground flex min-h-28 items-center justify-center gap-3 text-sm"
      >
        统计暂不可用
        <Button size="sm" variant="outline" onClick={retry}>
          重试
        </Button>
      </div>
    );
  if (loading || empty)
    return (
      <p
        role="status"
        className="text-muted-foreground py-10 text-center text-sm"
      >
        {loading ? "正在加载…" : "当前条件下暂无记录"}
      </p>
    );
  return null;
}

export function EmployeeClicksPanel({
  filters,
  enabled = true,
}: {
  filters: DashboardFilters;
  enabled?: boolean;
}) {
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [offset, setOffset] = useState(0);
  useEffect(
    () => setOffset(0),
    [filters.hours, filters.start_at, filters.end_at],
  );
  const params = windowParams(filters);
  params.set("query", query);
  params.set("offset", String(offset));
  params.set("limit", String(PAGE_SIZE));
  const serialized = params.toString();
  const result = useQuery({
    queryKey: ["dashboard", "employee-clicks", user?.id, serialized],
    queryFn: ({ signal }) =>
      loadPage<ClickItem>("employee-clicks", serialized, signal),
    enabled: enabled && user?.system_role === "admin",
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
  const rows = result.isError ? [] : (result.data?.items ?? []);
  return (
    <Card className="min-w-0 rounded-xl" data-testid="employee-clicks-panel">
      <CardHeader className="gap-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <MousePointer2Icon className="text-primary size-4" />
            数字员工广场点击
          </span>
          <span className="text-muted-foreground text-xs font-normal">
            所选时间范围 · 全部已发布员工
          </span>
        </CardTitle>
        <div className="relative w-full sm:max-w-sm">
          <SearchIcon
            aria-hidden="true"
            className="text-muted-foreground absolute top-3 left-3 size-4"
          />
          <Input
            className="pl-9 font-normal"
            aria-label="搜索点击统计中的数字员工"
            placeholder="搜索数字员工"
            value={search}
            maxLength={100}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      </CardHeader>
      <CardContent>
        <QueryState
          error={result.isError}
          loading={result.isPending}
          empty={rows.length === 0}
          retry={() => void result.refetch()}
        />
        {rows.length > 0 ? (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-left text-xs">
                  <tr className="border-b">
                    <th className="pb-3 font-medium">数字员工</th>
                    <th className="pb-3 text-right font-medium">区间点击量</th>
                    <th className="pb-3 text-right font-medium">区间用户数</th>
                    <th className="pb-3 text-right font-medium">累计点击量</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.identity} className="border-b last:border-0">
                      <td className="max-w-80 py-3 pr-4 font-medium break-words">
                        {row.name}
                        <span className="text-muted-foreground ml-2 text-xs font-normal">
                          {row.source === "external" ? "外部" : "平台"}
                        </span>
                      </td>
                      <td className="py-3 text-right tabular-nums">
                        {count(row.clicks)}
                      </td>
                      <td className="py-3 text-right tabular-nums">
                        {count(row.users)}
                      </td>
                      <td className="py-3 text-right tabular-nums">
                        {count(row.total_clicks)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-col gap-3 md:hidden">
              {rows.map((row) => (
                <div key={row.identity} className="rounded-lg border p-3">
                  <p className="text-sm font-medium break-words">{row.name}</p>
                  <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <dt className="text-muted-foreground">区间点击</dt>
                      <dd className="mt-1 font-semibold tabular-nums">
                        {count(row.clicks)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">区间用户</dt>
                      <dd className="mt-1 font-semibold tabular-nums">
                        {count(row.users)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">累计点击</dt>
                      <dd className="mt-1 font-semibold tabular-nums">
                        {count(row.total_clicks)}
                      </dd>
                    </div>
                  </dl>
                </div>
              ))}
            </div>
          </>
        ) : null}
        {!result.isError && result.data ? (
          <PageButtons
            label="员工点击"
            total={result.data.total}
            offset={offset}
            onOffsetChange={setOffset}
            busy={result.isFetching}
          />
        ) : null}
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          统计点击“开始工作”或外部入口的次数，保留已有点击记录；接入前未记录的点击不补算。同一员工由原发布者重新发布时累计保留。
        </p>
      </CardContent>
    </Card>
  );
}

function Rating({ rating }: { rating: number }) {
  return (
    <Badge variant={rating === 1 ? "secondary" : "outline"}>
      {rating === 1 ? <ThumbsUpIcon /> : <ThumbsDownIcon />}
      {rating === 1 ? "点赞" : "点踩"}
    </Badge>
  );
}

export function FeedbackActorsPanel({
  filters,
  enabled = true,
}: {
  filters: DashboardFilters;
  enabled?: boolean;
}) {
  const { user } = useAuth();
  const [search, setSearch] = useState("");
  const query = useDeferredValue(search);
  const [rating, setRating] = useState("");
  const [offset, setOffset] = useState(0);
  useEffect(
    () => setOffset(0),
    [filters.hours, filters.start_at, filters.end_at],
  );
  const params = windowParams(filters);
  params.set("query", query);
  params.set("offset", String(offset));
  params.set("limit", String(FEEDBACK_PAGE_SIZE));
  if (rating) params.set("rating", rating);
  const serialized = params.toString();
  const result = useQuery({
    queryKey: ["dashboard", "feedback-actors", user?.id, serialized],
    queryFn: ({ signal }) =>
      loadPage<FeedbackItem>("feedback-actors", serialized, signal),
    enabled: enabled && user?.system_role === "admin",
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
  });
  const rows = result.isError ? [] : (result.data?.items ?? []);
  const actorName = (row: FeedbackItem) =>
    row.display_name?.trim()
      ? row.display_name
      : (row.email ?? "账号已删除或未记录");
  const threadTitle = (row: FeedbackItem) =>
    row.thread_title?.trim() ? row.thread_title : "查看对话";
  const conversation = (row: FeedbackItem) => (
    <Link
      className="text-primary block truncate hover:underline"
      title={threadTitle(row)}
      href={`/workspace/chats/${encodeURIComponent(row.thread_id)}?admin_review=1`}
    >
      {threadTitle(row)}
    </Link>
  );
  const time = (row: FeedbackItem) =>
    new Date(row.created_at).toLocaleString("zh-CN", { hour12: false });
  return (
    <Card className="min-w-0 rounded-xl" data-testid="feedback-actors-panel">
      <CardHeader className="gap-3">
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <ThumbsUpIcon className="text-primary size-4" />
            点赞点踩明细
          </span>
          <span className="text-muted-foreground text-xs font-normal">
            所选时间范围 · 仅管理员可见
          </span>
        </CardTitle>
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative min-w-0 flex-1 sm:max-w-sm">
            <SearchIcon
              aria-hidden="true"
              className="text-muted-foreground absolute top-3 left-3 size-4"
            />
            <Input
              className="pl-9 font-normal"
              aria-label="搜索反馈人"
              placeholder="搜索姓名、邮箱或单位"
              maxLength={100}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          <ToggleGroup
            type="single"
            aria-label="反馈类型"
            value={rating || "all"}
            onValueChange={(value) => {
              if (!value) return;
              setRating(value === "all" ? "" : value);
              setOffset(0);
            }}
            className="w-full shrink-0 sm:w-auto"
          >
            <ToggleGroupItem value="all" className="flex-1">
              全部
            </ToggleGroupItem>
            <ToggleGroupItem value="1" className="flex-1">
              点赞
            </ToggleGroupItem>
            <ToggleGroupItem value="-1" className="flex-1">
              点踩
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      </CardHeader>
      <CardContent>
        <QueryState
          error={result.isError}
          loading={result.isPending}
          empty={rows.length === 0}
          retry={() => void result.refetch()}
        />
        {rows.length > 0 ? (
          <>
            <div className="hidden max-h-80 overflow-auto md:block">
              <table className="w-full text-sm">
                <thead className="bg-card text-muted-foreground sticky top-0 text-left text-xs">
                  <tr className="border-b">
                    <th className="pb-3 font-medium">反馈人</th>
                    <th className="pb-3 font-medium">评价</th>
                    <th className="pb-3 font-medium">对应对话</th>
                    <th className="pb-3 text-right font-medium">反馈时间</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.feedback_id}
                      className="border-b last:border-0"
                    >
                      <td className="max-w-72 py-2 pr-4">
                        <p className="font-medium break-words">
                          {actorName(row)}
                        </p>
                        <div className="text-muted-foreground mt-1 flex flex-wrap gap-x-2 text-xs">
                          {row.display_name && row.email ? (
                            <span className="break-all">{row.email}</span>
                          ) : null}
                          {row.organization_name ? (
                            <span>{row.organization_name}</span>
                          ) : null}
                        </div>
                      </td>
                      <td className="py-3 pr-4">
                        <Rating rating={row.rating} />
                      </td>
                      <td className="max-w-64 py-3 pr-4">
                        {conversation(row)}
                      </td>
                      <td className="text-muted-foreground py-3 text-right text-xs whitespace-nowrap">
                        {time(row)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex max-h-80 flex-col gap-3 overflow-y-auto md:hidden">
              {rows.map((row) => (
                <div
                  key={row.feedback_id}
                  className="min-w-0 rounded-lg border p-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium break-words">
                        {actorName(row)}
                      </p>
                      {row.display_name && row.email ? (
                        <p className="text-muted-foreground mt-1 text-xs break-all">
                          {row.email}
                        </p>
                      ) : null}
                      {row.organization_name ? (
                        <p className="text-muted-foreground mt-1 text-xs">
                          {row.organization_name}
                        </p>
                      ) : null}
                    </div>
                    <Rating rating={row.rating} />
                  </div>
                  <div className="mt-3 border-t pt-3 text-xs">
                    {conversation(row)}
                    <p className="text-muted-foreground mt-2">{time(row)}</p>
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : null}
        {!result.isError && result.data ? (
          <PageButtons
            label="反馈明细"
            pageSize={FEEDBACK_PAGE_SIZE}
            total={result.data.total}
            offset={offset}
            onOffsetChange={setOffset}
            busy={result.isFetching}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
