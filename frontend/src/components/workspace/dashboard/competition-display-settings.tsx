"use client";

import {
  CheckIcon,
  CopyIcon,
  ExternalLinkIcon,
  MonitorIcon,
  SearchIcon,
  XIcon,
} from "lucide-react";
import { useDeferredValue, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import type {
  CompetitionConfig,
  CompetitionEmployee,
} from "@/core/competition";
import { getBackendBaseURL } from "@/core/config";

const base = () => `${getBackendBaseURL()}/api/admin/competition`;

export function CompetitionDisplaySettings() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState<CompetitionConfig | null>(null);
  const [choices, setChoices] = useState<CompetitionEmployee[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const search = useDeferredValue(query);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingChoices, setLoadingChoices] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setConfig(null);
    setError("");
    setNotice("");
    void fetch(`${base()}/display`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error();
        return (await response.json()) as CompetitionConfig;
      })
      .then(setConfig)
      .catch(() => {
        if (!controller.signal.aborted) setError("无法加载展示设置，请重试。");
      });
    return () => controller.abort();
  }, [open, reload]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoadingChoices(true);
    void fetch(
      `${base()}/employees?${new URLSearchParams({ query: search, offset: String(offset), limit: "20" })}`,
      { signal: controller.signal },
    )
      .then(async (response) => {
        if (!response.ok) throw new Error();
        return (await response.json()) as {
          items: CompetitionEmployee[];
          total: number;
        };
      })
      .then((result) => {
        setChoices(result.items);
        setTotal(result.total);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setChoices([]);
          setError("员工清单加载失败，请重试。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingChoices(false);
      });
    return () => controller.abort();
  }, [open, search, offset, reload]);
  if (user?.system_role !== "admin") return null;
  function toggle(employee: CompetitionEmployee) {
    if (!config) return;
    const has = config.employees.some(
      (item) => item.identity === employee.identity,
    );
    if (!has && config.employees.length >= 100) {
      setError("最多选择 100 位数字员工。");
      return;
    }
    setConfig({
      ...config,
      employees: has
        ? config.employees.filter((item) => item.identity !== employee.identity)
        : [...config.employees, employee],
    });
    setNotice("");
  }
  async function save() {
    if (!config || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${base()}/display`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...config,
          employees: config.employees.map(
            ({ identity, publication, name }) => ({
              identity,
              publication,
              name,
            }),
          ),
        }),
      });
      if (response.status === 409)
        throw new Error(
          "名单或员工发布状态已变化，请重新加载后选择，避免覆盖其他管理员的修改。",
        );
      if (!response.ok) throw new Error("保存失败，请保留当前选择并稍后重试。");
      setConfig((await response.json()) as CompetitionConfig);
      setNotice(
        config.enabled
          ? "已发布。比赛展示页将在下次刷新时同步。"
          : "已停止公开展示。",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!busy) setOpen(value);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">
          <MonitorIcon />
          比赛大屏
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[90dvh] max-w-[calc(100vw-2rem)] flex-col gap-4 overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>比赛展示设置</DialogTitle>
          <DialogDescription>
            从数字员工广场选择参展员工。发布后，展示链接免登录开放，仅展示汇总点击量。
          </DialogDescription>
        </DialogHeader>
        {error && (
          <div
            role="alert"
            className="flex items-center justify-between gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
          >
            <span>{error}</span>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setReload((v) => v + 1)}
            >
              重新加载
            </Button>
          </div>
        )}
        {notice && (
          <p role="status" className="text-sm text-emerald-700">
            {notice}
          </p>
        )}
        {!config ? (
          <p className="text-muted-foreground py-8 text-center text-sm">
            {error ? "展示设置尚未载入" : "正在加载展示设置…"}
          </p>
        ) : (
          <>
            <label className="grid gap-2 text-sm font-medium">
              展示标题
              <Input
                maxLength={60}
                value={config.title}
                disabled={busy}
                onChange={(event) =>
                  setConfig({ ...config, title: event.target.value })
                }
              />
            </label>
            <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  className="accent-primary size-4"
                  checked={config.enabled}
                  disabled={busy}
                  onChange={(event) =>
                    setConfig({ ...config, enabled: event.target.checked })
                  }
                />
                启用公开展示
              </label>
              <span className="text-muted-foreground">
                已选 {config.employees.length} / 100
              </span>
            </div>
            <div className="bg-muted/20 max-h-24 overflow-y-auto rounded-lg border p-2">
              <div className="flex flex-wrap gap-2">
                {config.employees.map((item) => (
                  <button
                    key={item.identity}
                    type="button"
                    disabled={busy}
                    onClick={() => toggle(item)}
                    aria-label={`移除${item.name}`}
                    className="bg-primary/10 text-primary flex max-w-full items-center gap-1 rounded-md px-2 py-1 text-xs"
                  >
                    <span className="truncate">{item.name}</span>
                    <XIcon className="size-3 shrink-0" />
                  </button>
                ))}
                {!config.employees.length && (
                  <span className="text-muted-foreground p-1 text-xs">
                    请选择下方数字员工
                  </span>
                )}
              </div>
            </div>
            <div className="relative">
              <SearchIcon className="text-muted-foreground absolute top-3 left-3 size-4" />
              <Input
                aria-label="搜索参展数字员工"
                placeholder="搜索数字员工名称"
                className="pl-9"
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setOffset(0);
                }}
              />
            </div>
            <div
              className="min-h-32 flex-1 overflow-y-auto rounded-lg border"
              aria-busy={loadingChoices}
            >
              {choices.map((item) => {
                const selected = config.employees.some(
                  (e) => e.identity === item.identity,
                );
                return (
                  <label
                    key={item.identity}
                    className="hover:bg-muted/30 flex cursor-pointer items-center gap-3 border-b px-3 py-3 text-sm last:border-0"
                  >
                    <input
                      type="checkbox"
                      className="accent-primary size-4 shrink-0"
                      checked={selected}
                      disabled={busy || loadingChoices}
                      onChange={() => toggle(item)}
                    />
                    <span className="min-w-0 flex-1 break-words">
                      {item.name}
                      <span className="text-muted-foreground ml-2 text-xs">
                        {item.source === "external" ? "外部链接" : "平台员工"}
                      </span>
                    </span>
                    {selected && (
                      <CheckIcon className="text-primary size-4 shrink-0" />
                    )}
                  </label>
                );
              })}
              {!choices.length && (
                <p className="text-muted-foreground p-6 text-center text-sm">
                  {loadingChoices ? "加载中…" : "没有匹配的已发布数字员工"}
                </p>
              )}
            </div>
            <div className="text-muted-foreground flex items-center justify-between text-xs">
              <span>
                共 {total} 位 · 第 {Math.floor(offset / 20) + 1} 页
              </span>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={offset === 0 || loadingChoices}
                  onClick={() => setOffset(offset - 20)}
                >
                  上一页
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={offset + 20 >= total || loadingChoices}
                  onClick={() => setOffset(offset + 20)}
                >
                  下一页
                </Button>
              </div>
            </div>
            <footer className="flex flex-wrap justify-between gap-3 border-t pt-4">
              <div className="flex gap-2">
                <Button size="sm" variant="outline" asChild>
                  <a href="/competition" target="_blank" rel="noreferrer">
                    <ExternalLinkIcon />
                    打开大屏
                  </a>
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(`${window.location.origin}/competition`)
                      .then(
                        () => setNotice("展示链接已复制。"),
                        () =>
                          setError("复制失败，请打开大屏后复制地址栏链接。"),
                      );
                  }}
                >
                  <CopyIcon />
                  复制链接
                </Button>
              </div>
              <Button
                size="sm"
                disabled={
                  busy ||
                  !config.title.trim() ||
                  (config.enabled && !config.employees.length)
                }
                onClick={() => void save()}
              >
                {busy ? "保存中…" : "保存设置"}
              </Button>
            </footer>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
