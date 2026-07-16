"use client";

import {
  ExternalLinkIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  useAdminSiteNavigation,
  useCreateSiteNavigation,
  useDeleteSiteNavigation,
  useUpdateSiteNavigation,
} from "@/core/site-navigation";
import type {
  SiteNavigationInput,
  SiteNavigationItem,
} from "@/core/site-navigation";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type FormState = {
  name: string;
  url: string;
  aliases: string;
  description: string;
  category: string;
  sort_order: string;
  enabled: boolean;
};

const emptyForm: FormState = {
  name: "",
  url: "",
  aliases: "",
  description: "",
  category: "",
  sort_order: "0",
  enabled: true,
};

function itemToForm(item: SiteNavigationItem): FormState {
  return {
    name: item.name,
    url: item.url,
    aliases: item.aliases.join(", "),
    description: item.description,
    category: item.category,
    sort_order: String(item.sort_order),
    enabled: item.enabled,
  };
}

function normalizeForm(form: FormState): SiteNavigationInput {
  return {
    name: form.name.trim(),
    url: form.url.trim(),
    aliases: form.aliases
      .split(",")
      .map((alias) => alias.trim())
      .filter(Boolean),
    description: form.description.trim(),
    category: form.category.trim(),
    sort_order: Number.parseInt(form.sort_order, 10) || 0,
    enabled: form.enabled,
  };
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function SiteNavigationSettingsPage() {
  const { sites, isLoading } = useAdminSiteNavigation();
  const createMutation = useCreateSiteNavigation();
  const updateMutation = useUpdateSiteNavigation();
  const deleteMutation = useDeleteSiteNavigation();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);

  const selected = useMemo(
    () => sites.find((site) => site.id === selectedId) ?? null,
    [selectedId, sites],
  );
  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending;

  const resetForm = () => {
    setSelectedId(null);
    setForm(emptyForm);
  };

  const editSite = (site: SiteNavigationItem) => {
    setSelectedId(site.id);
    setForm(itemToForm(site));
  };

  const save = async () => {
    const input = normalizeForm(form);
    if (!input.name || !input.url) {
      toast.error("站点名称和 URL 不能为空");
      return;
    }
    if (!input.url.startsWith("http://") && !input.url.startsWith("https://")) {
      toast.error("URL 必须以 http:// 或 https:// 开头");
      return;
    }
    if (selectedId) {
      await updateMutation.mutateAsync({ id: selectedId, input });
      toast.success("站点已更新");
    } else {
      const created = await createMutation.mutateAsync(input);
      setSelectedId(created.id);
      setForm(itemToForm(created));
      toast.success("站点已创建");
    }
  };

  const toggleEnabled = async (site: SiteNavigationItem, enabled: boolean) => {
    await updateMutation.mutateAsync({
      id: site.id,
      input: { enabled },
    });
    toast.success(enabled ? "站点已启用" : "站点已停用");
  };

  const deleteSite = async (site: SiteNavigationItem) => {
    if (!window.confirm(`确认删除站点「${site.name}」？`)) return;
    await deleteMutation.mutateAsync(site.id);
    if (selectedId === site.id) resetForm();
    toast.success("站点已删除");
  };

  return (
    <SettingsSection
      title="站点管理"
      description="管理员维护企业内网站点和快捷入口，启用后的站点会出现在工作区侧边栏。"
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_160px_120px]">
            <label className="space-y-1.5 text-sm font-medium">
              <span>名称</span>
              <Input
                value={form.name}
                maxLength={160}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    name: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-1.5 text-sm font-medium">
              <span>分组</span>
              <Input
                value={form.category}
                maxLength={80}
                placeholder="内网工具"
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    category: event.target.value,
                  }))
                }
              />
            </label>
            <label className="space-y-1.5 text-sm font-medium">
              <span>排序</span>
              <Input
                type="number"
                value={form.sort_order}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    sort_order: event.target.value,
                  }))
                }
              />
            </label>
          </div>

          <label className="block space-y-1.5 text-sm font-medium">
            <span>URL</span>
            <Input
              value={form.url}
              placeholder="https://example.internal"
              onChange={(event) =>
                setForm((current) => ({ ...current, url: event.target.value }))
              }
            />
          </label>

          <label className="block space-y-1.5 text-sm font-medium">
            <span>别名</span>
            <Input
              value={form.aliases}
              placeholder="多个别名用逗号分隔"
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  aliases: event.target.value,
                }))
              }
            />
          </label>

          <label className="block space-y-1.5 text-sm font-medium">
            <span>描述</span>
            <Textarea
              value={form.description}
              rows={3}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
            />
          </label>

          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border px-3 py-2">
            <div>
              <div className="text-sm font-medium">启用站点</div>
              <div className="text-muted-foreground text-xs">
                停用后普通用户不会在侧边栏看到该入口
              </div>
            </div>
            <Switch
              checked={form.enabled}
              onCheckedChange={(enabled) =>
                setForm((current) => ({ ...current, enabled }))
              }
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? <LoaderCircleIcon className="animate-spin" /> : null}
              {selectedId ? "保存" : "新增"}
            </Button>
            <Button type="button" variant="outline" onClick={resetForm}>
              <PlusIcon />
              新建
            </Button>
            {selected ? (
              <Button type="button" variant="outline" asChild>
                <a href={selected.url} target="_blank" rel="noreferrer noopener">
                  <ExternalLinkIcon />
                  打开
                </a>
              </Button>
            ) : null}
          </div>
        </form>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">站点列表</div>
            <Badge variant="outline">{sites.length}</Badge>
          </div>
          <div className="space-y-2">
            {isLoading ? (
              <div className="text-muted-foreground flex items-center gap-2 text-sm">
                <LoaderCircleIcon className="size-4 animate-spin" />
                加载站点中
              </div>
            ) : sites.length === 0 ? (
              <div className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
                暂无站点，创建后会出现在工作区侧边栏。
              </div>
            ) : (
              sites.map((site) => (
                <div
                  key={site.id}
                  className={cn(
                    "rounded-md border p-3 text-sm transition-colors",
                    selectedId === site.id ? "border-primary bg-primary/5" : "",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <button
                      type="button"
                      className="min-w-0 text-left"
                      onClick={() => editSite(site)}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium break-all">
                          {site.name}
                        </span>
                        {site.enabled ? (
                          <Badge>启用</Badge>
                        ) : (
                          <Badge variant="secondary">停用</Badge>
                        )}
                        {site.category ? (
                          <Badge variant="outline">{site.category}</Badge>
                        ) : null}
                      </div>
                      <div className="text-muted-foreground mt-1 truncate text-xs">
                        {site.url}
                      </div>
                      <div className="text-muted-foreground mt-1 text-xs">
                        排序 {site.sort_order} · 更新 {formatTime(site.updated_at)}
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      <Switch
                        checked={site.enabled}
                        disabled={busy}
                        onCheckedChange={(enabled) =>
                          void toggleEnabled(site, enabled)
                        }
                      />
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        onClick={() => editSite(site)}
                      >
                        <PencilIcon />
                      </Button>
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => void deleteSite(site)}
                      >
                        <Trash2Icon />
                      </Button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </SettingsSection>
  );
}
