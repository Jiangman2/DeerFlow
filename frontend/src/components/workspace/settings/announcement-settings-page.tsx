"use client";

import {
  ArchiveIcon,
  CalendarClockIcon,
  CheckCircle2Icon,
  EyeOffIcon,
  LoaderCircleIcon,
  MegaphoneIcon,
  PencilIcon,
  PlusIcon,
  RotateCcwIcon,
  SendIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  useAnnouncements,
  useCreateAnnouncement,
  useSetAnnouncementStatus,
  useUpdateAnnouncement,
} from "@/core/announcements/hooks";
import {
  getAnnouncementDisplayStatus,
  validateAnnouncementWindow,
} from "@/core/announcements/presentation";
import type {
  Announcement,
  AnnouncementInput,
  AnnouncementSeverity,
  AnnouncementStatus,
} from "@/core/announcements/types";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type FormState = AnnouncementInput;

const emptyForm: FormState = {
  title: "",
  content: "",
  severity: "info",
  status: "draft",
  dismissible: true,
  starts_at: "",
  ends_at: "",
};

const severityLabels: Record<AnnouncementSeverity, string> = {
  info: "通知",
  success: "完成",
  warning: "提醒",
  critical: "紧急",
};

function toDatetimeLocal(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function fromDatetimeLocal(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function itemToForm(item: Announcement): FormState {
  return {
    title: item.title,
    content: item.content,
    severity: item.severity,
    status: item.status,
    dismissible: item.dismissible,
    starts_at: toDatetimeLocal(item.starts_at),
    ends_at: toDatetimeLocal(item.ends_at),
  };
}

function normalizeForm(form: FormState): AnnouncementInput {
  return {
    ...form,
    title: form.title.trim(),
    content: form.content.trim(),
    starts_at: fromDatetimeLocal(form.starts_at),
    ends_at: fromDatetimeLocal(form.ends_at),
  };
}

function formatTime(value: string | null): string {
  if (!value) return "不限";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function getStatusVariant(status: AnnouncementStatus) {
  if (status === "published") return "default";
  if (status === "archived") return "secondary";
  return "outline";
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function AnnouncementSettingsPage() {
  const { announcements, isLoading } = useAnnouncements();
  const createMutation = useCreateAnnouncement();
  const updateMutation = useUpdateAnnouncement();
  const statusMutation = useSetAnnouncementStatus();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);

  const selected = useMemo(
    () => announcements.find((item) => item.id === selectedId) ?? null,
    [announcements, selectedId],
  );
  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    statusMutation.isPending;

  const editItem = (item: Announcement) => {
    setSelectedId(item.id);
    setForm(itemToForm(item));
  };

  const resetForm = () => {
    setSelectedId(null);
    setForm(emptyForm);
  };

  const save = async (nextStatus?: AnnouncementStatus) => {
    const input = normalizeForm({
      ...form,
      status: nextStatus ?? form.status,
    });
    if (!input.title || !input.content) {
      toast.error("标题和内容不能为空");
      return;
    }
    const startsAt = input.starts_at
      ? new Date(input.starts_at).getTime()
      : null;
    const windowError = validateAnnouncementWindow(
      input.starts_at,
      input.ends_at,
    );
    if (windowError) {
      toast.error(windowError);
      return;
    }
    try {
      const item = selectedId
        ? await updateMutation.mutateAsync({ id: selectedId, input })
        : await createMutation.mutateAsync(input);
      setSelectedId(item.id);
      setForm(itemToForm(item));
      if (input.status === "published") {
        toast.success(
          startsAt !== null && startsAt > Date.now()
            ? "公告已排期"
            : "公告已发布",
        );
      } else {
        toast.success(selectedId ? "公告已保存" : "草稿已创建");
      }
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  };

  const setStatus = async (status: AnnouncementStatus) => {
    if (!selectedId) return;
    try {
      const item = await statusMutation.mutateAsync({ id: selectedId, status });
      setForm(itemToForm(item));
      toast.success(
        status === "archived"
          ? "公告已归档"
          : status === "draft"
            ? "公告已撤下并转为草稿"
            : "公告已发布",
      );
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  };

  return (
    <SettingsSection
      title="公告管理"
      description="先保存草稿，再发布或排期；同一时段仅展示一条公告。"
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <div className="bg-muted/30 flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
            <span className="text-muted-foreground">
              {selected ? "正在编辑" : "正在创建新公告"}
            </span>
            <Badge
              variant={selected ? getStatusVariant(selected.status) : "outline"}
            >
              {selected ? getAnnouncementDisplayStatus(selected) : "未保存"}
            </Badge>
          </div>

          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
            <label className="space-y-1.5 text-sm font-medium">
              <span>标题</span>
              <Input
                value={form.title}
                maxLength={160}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    title: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-1.5 text-sm font-medium">
              <span>级别</span>
              <Select
                value={form.severity}
                onValueChange={(value) =>
                  setForm((current) => ({
                    ...current,
                    severity: value as AnnouncementSeverity,
                  }))
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(severityLabels).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          </div>

          <label className="block space-y-1.5 text-sm font-medium">
            <span>内容</span>
            <Textarea
              value={form.content}
              maxLength={4000}
              className="min-h-36 resize-y"
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  content: event.target.value,
                }))
              }
            />
          </label>

          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1.5 text-sm font-medium">
              <span>开始时间</span>
              <Input
                type="datetime-local"
                value={form.starts_at ?? ""}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    starts_at: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-1.5 text-sm font-medium">
              <span>结束时间</span>
              <Input
                type="datetime-local"
                value={form.ends_at ?? ""}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    ends_at: event.target.value,
                  }))
                }
              />
            </label>
          </div>

          <div className="text-muted-foreground bg-muted/40 flex items-start gap-2 rounded-lg px-3 py-2 text-xs leading-relaxed">
            <CalendarClockIcon className="mt-0.5 size-4 shrink-0" />
            <span>
              开始时间留空表示发布后立即展示，结束时间留空表示持续展示。未来时间发布后会进入“已排期”状态。
            </span>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2">
            <label className="flex items-center gap-3 text-sm font-medium">
              <Switch
                checked={form.dismissible}
                onCheckedChange={(checked) =>
                  setForm((current) => ({
                    ...current,
                    dismissible: checked,
                  }))
                }
              />
              允许用户关闭
            </label>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={resetForm}>
                <PlusIcon />
                新公告
              </Button>
              <Button type="submit" variant="outline" disabled={busy}>
                {busy ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <PencilIcon />
                )}
                {selected?.status === "published"
                  ? "保存并更新展示"
                  : "保存草稿"}
              </Button>
              {selected?.status === "published" ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void setStatus("draft")}
                >
                  <EyeOffIcon />
                  撤下
                </Button>
              ) : (
                <Button
                  type="button"
                  disabled={busy}
                  onClick={() => void save("published")}
                >
                  {busy ? (
                    <LoaderCircleIcon className="animate-spin" />
                  ) : (
                    <SendIcon />
                  )}
                  {form.starts_at &&
                  new Date(form.starts_at).getTime() > Date.now()
                    ? "排期发布"
                    : "立即发布"}
                </Button>
              )}
              {selected?.status === "archived" ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void setStatus("draft")}
                >
                  <RotateCcwIcon />
                  恢复为草稿
                </Button>
              ) : null}
              {selected && selected.status !== "archived" ? (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void setStatus("archived")}
                >
                  <ArchiveIcon />
                  归档
                </Button>
              ) : null}
            </div>
          </div>
        </form>

        <div className="min-h-0 space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <MegaphoneIcon className="size-4" />
            公告历史
          </div>
          <div className="max-h-[520px] space-y-2 overflow-y-auto pr-1">
            {isLoading ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <LoaderCircleIcon className="size-4 animate-spin" />
                加载中
              </div>
            ) : announcements.length === 0 ? (
              <div className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
                暂无公告
              </div>
            ) : (
              announcements.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => editItem(item)}
                  className={cn(
                    "hover:bg-muted/60 block w-full rounded-md border p-3 text-left text-sm transition-colors",
                    selectedId === item.id && "border-primary bg-primary/5",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 font-medium break-words">
                      {item.title}
                    </div>
                    <Badge
                      variant={getStatusVariant(item.status)}
                      className="shrink-0"
                    >
                      {item.status === "published" ? (
                        <CheckCircle2Icon />
                      ) : null}
                      {getAnnouncementDisplayStatus(item)}
                    </Badge>
                  </div>
                  <div className="text-muted-foreground mt-2 line-clamp-2 text-xs">
                    {item.content}
                  </div>
                  <div className="text-muted-foreground mt-2 text-xs">
                    {formatTime(item.starts_at)} 至 {formatTime(item.ends_at)}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
    </SettingsSection>
  );
}
