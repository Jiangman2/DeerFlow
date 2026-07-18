"use client";

import {
  BookOpenIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  FileTextIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  UploadIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  useCreateWeKnoraKnowledgeBase,
  useCreateWeKnoraManualKnowledge,
  useUploadWeKnoraKnowledgeFile,
  useWeKnoraKnowledge,
  useWeKnoraKnowledgeBases,
  type WeKnoraKnowledge,
  type WeKnoraKnowledgeBase,
} from "@/core/weknora";
import { cn } from "@/lib/utils";

type KnowledgeBaseFilter = "all" | "owned" | "joined" | "team";

const knowledgeBaseFilters: Array<{
  key: KnowledgeBaseFilter;
  label: string;
}> = [
  { key: "all", label: "全部" },
  { key: "owned", label: "我创建的" },
  { key: "joined", label: "我加入的" },
  { key: "team", label: "团队/公开" },
];

function formatCount(value: number | undefined): string {
  return typeof value === "number" ? String(value) : "0";
}

function isOwnedKb(kb: WeKnoraKnowledgeBase): boolean {
  return kb.access_scope === "owned" || Boolean(kb.is_owner);
}

function isJoinedKb(kb: WeKnoraKnowledgeBase): boolean {
  return (
    kb.access_scope === "joined" ||
    Boolean(kb.share_id?.trim()) ||
    Boolean(kb.organization_id?.trim())
  );
}

function isTeamOrPublicKb(kb: WeKnoraKnowledgeBase): boolean {
  return Boolean(
    kb.access_scope === "team" ||
    kb.access_scope === "public" ||
    kb.type === "team" ||
    kb.type === "public",
  );
}

function matchesKnowledgeBaseFilter(
  kb: WeKnoraKnowledgeBase,
  filter: KnowledgeBaseFilter,
): boolean {
  if (filter === "all") return true;
  if (filter === "owned") return isOwnedKb(kb);
  if (filter === "joined") return isJoinedKb(kb);
  return isTeamOrPublicKb(kb);
}

function kbAccessScope(kb: WeKnoraKnowledgeBase): KnowledgeBaseFilter {
  if (isTeamOrPublicKb(kb)) return "team";
  if (isOwnedKb(kb)) return "owned";
  if (kb.access_scope === "joined" || kb.share_id || kb.organization_id) {
    return "joined";
  }
  return "team";
}

function kbScopeLabel(kb: WeKnoraKnowledgeBase): string {
  const scope = kbAccessScope(kb);
  if (scope === "owned") return "我创建";
  if (scope === "joined") return kb.org_name ? `来自 ${kb.org_name}` : "已加入";
  return kb.type === "public" ? "公开" : "团队";
}

function statusLabel(item: WeKnoraKnowledge): {
  label: string;
  tone: "success" | "warning" | "danger" | "muted";
} {
  const status = item.parse_status ?? item.summary_status ?? "";
  if (status === "done" || status === "success" || status === "completed") {
    return { label: "已处理", tone: "success" };
  }
  if (status === "failed" || status === "error") {
    return { label: "失败", tone: "danger" };
  }
  if (status === "processing" || status === "pending" || status === "parsing") {
    return { label: "处理中", tone: "warning" };
  }
  return { label: status || "未知", tone: "muted" };
}

export default function KnowledgePage() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedKbId, setSelectedKbId] = useState<string | undefined>();
  const [kbFilter, setKbFilter] = useState<KnowledgeBaseFilter>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [kbName, setKbName] = useState("");
  const [kbDescription, setKbDescription] = useState("");
  const [manualTitle, setManualTitle] = useState("");
  const [manualContent, setManualContent] = useState("");

  const knowledgeBasesQuery = useWeKnoraKnowledgeBases();
  const knowledgeBases = useMemo(
    () => knowledgeBasesQuery.data?.items ?? [],
    [knowledgeBasesQuery.data?.items],
  );
  const filteredKnowledgeBases = useMemo(() => {
    return knowledgeBases.filter((kb) =>
      matchesKnowledgeBaseFilter(kb, kbFilter),
    );
  }, [knowledgeBases, kbFilter]);
  const filterCounts = useMemo(
    () => ({
      all: knowledgeBases.length,
      owned: knowledgeBases.filter(isOwnedKb).length,
      joined: knowledgeBases.filter(isJoinedKb).length,
      team: knowledgeBases.filter(isTeamOrPublicKb).length,
    }),
    [knowledgeBases],
  );
  const selectedKb = knowledgeBases.find((kb) => kb.id === selectedKbId);
  const knowledgeQuery = useWeKnoraKnowledge(selectedKb?.id);

  const createKb = useCreateWeKnoraKnowledgeBase();
  const uploadFile = useUploadWeKnoraKnowledgeFile();
  const createManual = useCreateWeKnoraManualKnowledge();

  useEffect(() => {
    const selectableKnowledgeBases = filteredKnowledgeBases;
    if (!selectedKbId && selectableKnowledgeBases[0]) {
      setSelectedKbId(selectableKnowledgeBases[0].id);
    }
    if (selectedKbId && selectableKnowledgeBases.length > 0) {
      const exists = selectableKnowledgeBases.some(
        (kb) => kb.id === selectedKbId,
      );
      if (!exists) {
        setSelectedKbId(selectableKnowledgeBases[0]?.id);
      }
    }
    if (selectedKbId && selectableKnowledgeBases.length === 0) {
      setSelectedKbId(undefined);
    }
  }, [filteredKnowledgeBases, selectedKbId]);

  const handleCreateKnowledgeBase = async () => {
    if (!kbName.trim()) {
      toast.error("请输入知识库名称");
      return;
    }
    try {
      const result = await createKb.mutateAsync({
        name: kbName.trim(),
        description: kbDescription.trim(),
      });
      const createdId = result.data?.id;
      if (createdId) {
        setSelectedKbId(createdId);
      }
      setKbName("");
      setKbDescription("");
      setCreateOpen(false);
      toast.success("知识库已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建失败");
    }
  };

  const handleCreateManualKnowledge = async () => {
    if (!selectedKb?.id) return;
    if (!manualTitle.trim() || !manualContent.trim()) {
      toast.error("请输入标题和正文");
      return;
    }
    try {
      await createManual.mutateAsync({
        knowledgeBaseId: selectedKb.id,
        title: manualTitle.trim(),
        content: manualContent,
      });
      setManualTitle("");
      setManualContent("");
      setManualOpen(false);
      toast.success("文档已提交处理");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建失败");
    }
  };

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !selectedKb?.id) return;
    try {
      await uploadFile.mutateAsync({ knowledgeBaseId: selectedKb.id, file });
      toast.success("文件已上传，正在处理");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "上传失败");
    }
  };

  const knowledgeItems = knowledgeQuery.data?.items ?? [];

  return (
    <main className="flex h-full min-h-0 flex-col bg-[#f6f8fb]">
      <header className="flex shrink-0 items-center justify-between border-b border-[#dbe3ef] bg-white/80 px-6 py-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-[#202938]">知识库</h1>
          <p className="mt-1 text-sm text-[#687386]">
            管理知识库，并在对话中选择任务检索范围
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => knowledgeBasesQuery.refetch()}
          >
            <RefreshCwIcon className="size-4" />
            刷新
          </Button>
          <Button size="sm" onClick={() => setCreateOpen(true)}>
            <PlusIcon className="size-4" />
            新建知识库
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(280px,360px)_1fr] gap-4 p-4">
        <aside className="min-h-0 overflow-y-auto rounded-lg border border-[#dbe3ef] bg-white">
          <div className="border-b border-[#edf1f7] px-4 py-3 text-sm font-medium text-[#4f5967]">
            知识库列表
          </div>
          <div className="grid grid-cols-2 gap-2 border-b border-[#edf1f7] p-3">
            {knowledgeBaseFilters.map((filter) => (
              <button
                key={filter.key}
                type="button"
                className={cn(
                  "flex min-h-9 items-center justify-between rounded-md border px-3 text-xs font-medium transition",
                  kbFilter === filter.key
                    ? "border-[#6d5dfc] bg-[#f4f2ff] text-[#5b4ef7]"
                    : "border-[#edf1f7] bg-white text-[#687386] hover:border-[#cbd6e7] hover:bg-[#f8fafc]",
                )}
                onClick={() => setKbFilter(filter.key)}
              >
                <span>{filter.label}</span>
                <span>{filterCounts[filter.key]}</span>
              </button>
            ))}
          </div>
          <div className="space-y-2 p-3">
            {knowledgeBasesQuery.isLoading && (
              <div className="flex items-center gap-2 px-2 py-8 text-sm text-[#687386]">
                <Loader2Icon className="size-4 animate-spin" />
                加载中
              </div>
            )}
            {knowledgeBasesQuery.isError && (
              <div className="space-y-2 px-2 py-8 text-sm text-[#b42318]">
                <div>知识库加载失败，请稍后重试。</div>
                <div className="break-words text-xs text-[#687386]">
                  {knowledgeBasesQuery.error instanceof Error
                    ? knowledgeBasesQuery.error.message
                    : "服务暂时不可用"}
                </div>
              </div>
            )}
            {!knowledgeBasesQuery.isLoading &&
              filteredKnowledgeBases.length === 0 && (
                <div className="px-2 py-8 text-sm text-[#687386]">
                  {knowledgeBases.length === 0
                    ? "还没有知识库"
                    : "当前范围没有知识库"}
                </div>
              )}
            {filteredKnowledgeBases.map((kb) => (
              <button
                key={kb.id}
                type="button"
                className={cn(
                  "w-full rounded-md border p-3 text-left transition",
                  selectedKb?.id === kb.id
                    ? "border-[#6d5dfc] bg-[#f4f2ff]"
                    : "border-[#edf1f7] bg-white hover:border-[#cbd6e7] hover:bg-[#f8fafc]",
                )}
                onClick={() => setSelectedKbId(kb.id)}
              >
                <div className="flex items-start gap-3">
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-[#eef2ff] text-[#6d5dfc]">
                    <BookOpenIcon className="size-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold text-[#202938]">
                      {kb.name}
                    </div>
                    <div className="mt-1">
                      <Badge
                        variant="outline"
                        className="rounded-md border-[#dbe3ef] bg-[#f8fafc] px-1.5 py-0 text-[11px] font-normal text-[#687386]"
                      >
                        {kbScopeLabel(kb)}
                      </Badge>
                    </div>
                    <div className="mt-1 line-clamp-2 text-xs text-[#687386]">
                      {kb.description ?? "暂无简介"}
                    </div>
                    <div className="mt-2 flex items-center gap-3 text-xs text-[#8791a1]">
                      <span>{formatCount(kb.knowledge_count)} 文档</span>
                      <span>{formatCount(kb.chunk_count)} 分块</span>
                      {kb.is_processing && <span>处理中</span>}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <section className="flex min-h-0 flex-col rounded-lg border border-[#dbe3ef] bg-white">
          <div className="flex shrink-0 items-center justify-between border-b border-[#edf1f7] px-5 py-4">
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold text-[#202938]">
                {selectedKb?.name ?? "请选择知识库"}
              </h2>
              <p className="mt-1 truncate text-sm text-[#687386]">
                {selectedKb?.description ??
                  "上传文件或新建手工文档后，系统会自动解析并建立索引"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={handleFileChange}
              />
              <Button
                variant="outline"
                size="sm"
                disabled={!selectedKb || uploadFile.isPending}
                onClick={() => fileInputRef.current?.click()}
              >
                {uploadFile.isPending ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <UploadIcon className="size-4" />
                )}
                上传文件
              </Button>
              <Button
                size="sm"
                disabled={!selectedKb}
                onClick={() => setManualOpen(true)}
              >
                <FileTextIcon className="size-4" />
                手工文档
              </Button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {!selectedKb && (
              <div className="flex h-full items-center justify-center text-sm text-[#687386]">
                选择或新建一个知识库开始管理文档
              </div>
            )}
            {selectedKb && knowledgeQuery.isLoading && (
              <div className="flex items-center gap-2 text-sm text-[#687386]">
                <Loader2Icon className="size-4 animate-spin" />
                正在读取文档状态
              </div>
            )}
            {selectedKb &&
              !knowledgeQuery.isLoading &&
              knowledgeItems.length === 0 && (
                <div className="flex h-full items-center justify-center text-sm text-[#687386]">
                  当前知识库还没有文档
                </div>
              )}
            {knowledgeItems.length > 0 && (
              <div className="overflow-hidden rounded-lg border border-[#edf1f7]">
                <div className="grid grid-cols-[1fr_120px_120px_180px] bg-[#f8fafc] px-4 py-2 text-xs font-medium text-[#687386]">
                  <span>文档</span>
                  <span>类型</span>
                  <span>状态</span>
                  <span>更新时间</span>
                </div>
                {knowledgeItems.map((item) => {
                  const status = statusLabel(item);
                  return (
                    <div
                      key={item.id}
                      className="grid grid-cols-[1fr_120px_120px_180px] items-center border-t border-[#edf1f7] px-4 py-3 text-sm"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-medium text-[#202938]">
                          {item.title ?? item.file_name ?? item.id}
                        </div>
                        {item.error_message && (
                          <div className="mt-1 truncate text-xs text-[#c2410c]">
                            {item.error_message}
                          </div>
                        )}
                      </div>
                      <span className="truncate text-[#687386]">
                        {item.file_type ?? item.source ?? "manual"}
                      </span>
                      <Badge
                        variant="outline"
                        className={cn(
                          "w-fit gap-1 rounded-md",
                          status.tone === "success" &&
                            "border-[#bbf7d0] bg-[#f0fdf4] text-[#15803d]",
                          status.tone === "warning" &&
                            "border-[#fde68a] bg-[#fffbeb] text-[#a16207]",
                          status.tone === "danger" &&
                            "border-[#fecaca] bg-[#fef2f2] text-[#b91c1c]",
                        )}
                      >
                        {status.tone === "success" && (
                          <CheckCircle2Icon className="size-3" />
                        )}
                        {status.tone === "warning" && (
                          <Loader2Icon className="size-3 animate-spin" />
                        )}
                        {status.tone === "danger" && (
                          <CircleAlertIcon className="size-3" />
                        )}
                        {status.label}
                      </Badge>
                      <span className="truncate text-[#687386]">
                        {item.updated_at
                          ? new Date(item.updated_at).toLocaleString()
                          : "-"}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建知识库</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              value={kbName}
              placeholder="知识库名称"
              onChange={(event) => setKbName(event.target.value)}
            />
            <Textarea
              value={kbDescription}
              placeholder="简介"
              rows={4}
              onChange={(event) => setKbDescription(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button
              disabled={createKb.isPending}
              onClick={handleCreateKnowledgeBase}
            >
              {createKb.isPending && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={manualOpen} onOpenChange={setManualOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>新建手工文档</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <Input
              value={manualTitle}
              placeholder="文档标题"
              onChange={(event) => setManualTitle(event.target.value)}
            />
            <Textarea
              value={manualContent}
              placeholder="Markdown 正文"
              rows={12}
              onChange={(event) => setManualContent(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setManualOpen(false)}>
              取消
            </Button>
            <Button
              disabled={createManual.isPending}
              onClick={handleCreateManualKnowledge}
            >
              {createManual.isPending && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              提交处理
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </main>
  );
}
