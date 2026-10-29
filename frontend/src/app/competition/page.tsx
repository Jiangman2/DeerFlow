"use client";

import {
  ArrowDownWideNarrowIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  MaximizeIcon,
  MousePointer2Icon,
  PauseIcon,
  PlayIcon,
  TrophyIcon,
} from "lucide-react";
import Image from "next/image";
import { useEffect, useState } from "react";

import { brand } from "@/brand";
import { Button } from "@/components/ui/button";
import {
  competitionBoardSchema,
  type CompetitionBoard,
} from "@/core/competition";
import { getBackendBaseURL } from "@/core/config";

const number = (n: number) => n.toLocaleString("zh-CN");

export default function CompetitionPage() {
  const [data, setData] = useState<CompetitionBoard | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [autoplay, setAutoplay] = useState(true);
  const [sort, setSort] = useState<"total_clicks" | "today_clicks">(
    "total_clicks",
  );
  const [fullscreenError, setFullscreenError] = useState("");
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    async function load() {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 15_000);
      try {
        const response = await fetch(
          `${getBackendBaseURL()}/api/competition/display`,
          { signal: controller.signal, credentials: "omit", cache: "no-store" },
        );
        if (response.status === 404) {
          if (!disposed) {
            setData(null);
            setError("展示尚未开放，请联系管理员配置比赛名单。");
          }
          return;
        }
        if (!response.ok) throw new Error("unavailable");
        const next = competitionBoardSchema.parse(await response.json());
        if (!disposed) {
          setData(next);
          setError("");
        }
      } catch {
        if (!disposed)
          setError("暂时无法更新，将自动重试；已有数据以所示更新时间为准。");
      } finally {
        clearTimeout(deadline);
        if (!disposed) timer = setTimeout(() => void load(), 10_000);
      }
    }
    void load();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, []);
  const rows = [...(data?.items ?? [])].sort(
    (a, b) => b[sort] - a[sort] || a.name.localeCompare(b.name, "zh-CN"),
  );
  const pages = Math.max(1, Math.ceil(rows.length / 6));
  const currentPage = Math.min(page, pages - 1);
  useEffect(() => {
    if (!autoplay || pages < 2) return;
    const interval = setInterval(
      () => setPage((value) => (value + 1) % pages),
      15_000,
    );
    return () => clearInterval(interval);
  }, [autoplay, pages]);
  const largest = rows[0]?.[sort] ?? 0;
  return (
    <main
      className="min-h-dvh bg-[#f3f7fc] px-4 py-6 text-[#17314f] sm:px-8 lg:px-12"
      data-testid="competition-display"
    >
      <div className="mx-auto flex min-h-[calc(100dvh-3rem)] max-w-[1920px] flex-col gap-6">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#d8e4f2] pb-5">
          <div className="flex min-w-0 items-center gap-4">
            <Image
              src={brand.logo.markSrc}
              alt="中国海油"
              width={52}
              height={52}
              className="shrink-0"
            />
            <div>
              <p className="mb-1 text-xs tracking-[0.16em] text-[#52708f]">
                海能 AI · 数字员工
              </p>
              <h1 className="text-xl font-semibold [overflow-wrap:anywhere] sm:text-3xl">
                {data?.title ?? "数字员工比赛展示"}
              </h1>
            </div>
          </div>
          <div className="flex items-center gap-3 text-xs text-[#52708f]">
            <span
              className={`h-2 w-2 rounded-full ${error ? "bg-amber-500" : data ? "bg-emerald-500" : "bg-slate-300"}`}
            />
            {error ? "等待更新" : data ? "每 10 秒刷新" : "正在连接"}
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="全屏展示"
              onClick={() => {
                if (!document.documentElement.requestFullscreen) {
                  setFullscreenError(
                    "当前浏览器不支持全屏，可使用浏览器全屏功能。",
                  );
                  return;
                }
                void document.documentElement
                  .requestFullscreen()
                  .catch(() =>
                    setFullscreenError(
                      "当前浏览器不支持全屏，可使用浏览器全屏功能。",
                    ),
                  );
              }}
            >
              <MaximizeIcon />
            </Button>
          </div>
        </header>
        {(error || fullscreenError) && (
          <p
            role="status"
            className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            {error || fullscreenError}
          </p>
        )}
        <section
          className="flex flex-wrap items-center justify-between gap-4"
          aria-label="展示概览"
        >
          <div className="flex flex-wrap gap-7 sm:gap-12">
            <div>
              <p className="text-xs text-[#607b98]">参展数字员工</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums">
                {data ? number(rows.length) : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-[#607b98]">累计点击量</p>
              <p className="mt-1 text-3xl font-semibold text-[#0569a8] tabular-nums">
                {data
                  ? number(rows.reduce((sum, row) => sum + row.total_clicks, 0))
                  : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-[#607b98]">今日点击量</p>
              <p className="mt-1 text-3xl font-semibold tabular-nums">
                {data
                  ? number(rows.reduce((sum, row) => sum + row.today_clicks, 0))
                  : "—"}
              </p>
            </div>
          </div>
          <Button
            variant="outline"
            onClick={() => {
              setSort(
                sort === "total_clicks" ? "today_clicks" : "total_clicks",
              );
              setPage(0);
            }}
          >
            <ArrowDownWideNarrowIcon />按
            {sort === "total_clicks" ? "累计" : "今日"}点击排序
          </Button>
        </section>
        <section
          className="grid flex-1 content-start gap-4 md:grid-cols-2 xl:grid-cols-3"
          aria-label="参展数字员工点击量"
        >
          {rows
            .slice(currentPage * 6, currentPage * 6 + 6)
            .map((row, index) => (
              <article
                key={row.identity}
                className="flex min-h-56 flex-col gap-6 rounded-2xl border border-[#d7e3ef] bg-white p-6 shadow-[0_2px_6px_rgba(28,64,104,0.03)] lg:min-h-64 lg:p-7"
              >
                <div className="flex items-start gap-3">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-sm font-semibold ${currentPage === 0 && index < 3 ? "bg-[#e8f2fc] text-[#0569a8]" : "bg-[#f3f6fa] text-[#71869c]"}`}
                  >
                    {currentPage * 6 + index + 1}
                  </span>
                  <h2 className="min-w-0 self-center text-lg leading-relaxed font-semibold break-words lg:text-xl">
                    {row.name}
                  </h2>
                </div>
                <div className="mt-auto flex flex-wrap items-end justify-between gap-4">
                  <div>
                    <p className="mb-2 text-xs text-[#607b98]">累计点击量</p>
                    <p className="text-4xl font-semibold tracking-tight text-[#0569a8] tabular-nums lg:text-5xl">
                      {number(row.total_clicks)}
                    </p>
                  </div>
                  <div className="pb-1 text-right">
                    <p className="mb-2 text-xs text-[#607b98]">今日</p>
                    <p className="text-2xl font-medium tabular-nums">
                      {number(row.today_clicks)}
                    </p>
                  </div>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-[#edf3f9]">
                  <div
                    className="h-full rounded-full bg-[#65a8d5] transition-[width] motion-reduce:transition-none"
                    style={{
                      width: `${largest ? Math.max(0, Math.min(100, (row[sort] / largest) * 100)) : 0}%`,
                    }}
                  />
                </div>
              </article>
            ))}
          {data && rows.length === 0 && (
            <div className="col-span-full flex min-h-64 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-[#c9d9ea] text-[#607b98]">
              <TrophyIcon className="size-9" />
              <p>暂无可展示的数字员工，请管理员更新名单。</p>
            </div>
          )}
          {!data && !error && (
            <div className="col-span-full py-20 text-center text-[#607b98]">
              正在加载比赛数据…
            </div>
          )}
        </section>
        <footer className="flex flex-wrap items-center justify-between gap-4 border-t border-[#d8e4f2] pt-4 text-xs text-[#607b98]">
          <div className="space-y-1">
            <p className="flex items-center gap-1.5">
              <MousePointer2Icon className="size-3.5" />
              点击量不代表任务执行次数 · 今日按北京时间自然日统计
            </p>
            <p>
              数据更新：
              {data
                ? new Date(data.updated_at).toLocaleString("zh-CN", {
                    timeZone: "Asia/Shanghai",
                    hour12: false,
                  })
                : "—"}
              {!!data?.unavailable_count &&
                ` · ${data.unavailable_count} 位员工已下架或需重新确认`}
            </p>
          </div>
          <nav className="flex items-center gap-3" aria-label="大屏分页">
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={autoplay ? "暂停轮播" : "开始轮播"}
              onClick={() => setAutoplay(!autoplay)}
            >
              {autoplay ? <PauseIcon /> : <PlayIcon />}
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="上一页"
              disabled={pages === 1}
              onClick={() => setPage((currentPage + pages - 1) % pages)}
            >
              <ChevronLeftIcon />
            </Button>
            <span>
              {currentPage + 1} / {pages}
            </span>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label="下一页"
              disabled={pages === 1}
              onClick={() => setPage((currentPage + 1) % pages)}
            >
              <ChevronRightIcon />
            </Button>
          </nav>
        </footer>
      </div>
    </main>
  );
}
