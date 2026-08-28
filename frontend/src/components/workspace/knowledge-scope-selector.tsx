"use client";

import {
  BookOpenIcon,
  CheckIcon,
  FileTextIcon,
  SearchIcon,
  TagsIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  buildWeKnoraScopeContext,
  isWeKnoraScopeItemSelected,
  toggleWeKnoraScopeItem,
  useWeKnoraKnowledge,
  useWeKnoraKnowledgeBases,
  useWeKnoraTags,
  weknoraScopeItemsFromContext,
  type WeKnoraScopeItem,
} from "@/core/weknora";
import { cn } from "@/lib/utils";

type KnowledgeScopeContext = {
  weknora_knowledge_base_ids?: string[];
  weknora_knowledge_bases?: { id: string; name: string }[];
  weknora_knowledge_ids?: string[];
  weknora_tag_scopes?: {
    knowledge_base_id: string;
    tag_ids: string[];
  }[];
  weknora_scope_items?: WeKnoraScopeItem[];
};

type KnowledgeScopeSelectorProps<T> = {
  className?: string;
  context: T & KnowledgeScopeContext;
  disabled?: boolean;
  onContextChange?: (context: T & KnowledgeScopeContext) => void;
};

function SelectionMark({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-5 shrink-0 items-center justify-center rounded-md border",
        selected
          ? "border-primary bg-primary text-primary-foreground"
          : "border-input bg-background",
      )}
    >
      {selected && <CheckIcon className="size-3.5" />}
    </span>
  );
}

export function KnowledgeScopeSelector<T>({
  className,
  context,
  disabled,
  onContextChange,
}: KnowledgeScopeSelectorProps<T>) {
  const [open, setOpen] = useState(false);
  const [activeKnowledgeBaseId, setActiveKnowledgeBaseId] = useState<
    string | undefined
  >();
  const [section, setSection] = useState<"files" | "tags">("files");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<WeKnoraScopeItem[]>([]);

  const { data, isError, isLoading } = useWeKnoraKnowledgeBases();
  const knowledgeBases = useMemo(() => data?.items ?? [], [data?.items]);
  const activeKnowledgeBase = knowledgeBases.find(
    (kb) => kb.id === activeKnowledgeBaseId,
  );
  const activeTenantId =
    activeKnowledgeBase?.access_tenant_id ?? activeKnowledgeBase?.tenant_id;
  const documentsQuery = useWeKnoraKnowledge(
    activeKnowledgeBaseId,
    activeTenantId,
  );
  const tagsQuery = useWeKnoraTags(activeKnowledgeBaseId, activeTenantId);
  const selectedItems = useMemo(
    () => weknoraScopeItemsFromContext(context, knowledgeBases),
    [context, knowledgeBases],
  );
  const selectedCount = selectedItems.length;

  useEffect(() => {
    if (!open) {
      return;
    }
    setDraft(selectedItems);
    setQuery("");
    setActiveKnowledgeBaseId((current) => {
      if (current && knowledgeBases.some((kb) => kb.id === current)) {
        return current;
      }
      const selectedParent = selectedItems[0]?.kb_id ?? selectedItems[0]?.id;
      return knowledgeBases.some((kb) => kb.id === selectedParent)
        ? selectedParent
        : knowledgeBases[0]?.id;
    });
  }, [knowledgeBases, open, selectedItems]);

  const toggle = useCallback((item: WeKnoraScopeItem) => {
    setDraft((current) => toggleWeKnoraScopeItem(current, item));
  }, []);

  const knowledgeBaseItem = useCallback(
    (knowledgeBaseId: string): WeKnoraScopeItem | undefined => {
      const kb = knowledgeBases.find(
        (candidate) => candidate.id === knowledgeBaseId,
      );
      if (!kb) {
        return undefined;
      }
      return {
        id: kb.id,
        name: kb.name,
        type: "kb",
        ...(kb.type ? { kb_type: kb.type } : {}),
        ...(kb.access_tenant_id !== undefined
          ? { tenant_id: String(kb.access_tenant_id) }
          : {}),
      };
    },
    [knowledgeBases],
  );

  const filteredDocuments = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const items = documentsQuery.data?.items ?? [];
    if (!needle) {
      return items;
    }
    return items.filter((item) =>
      [item.title, item.file_name, item.description]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(needle)),
    );
  }, [documentsQuery.data?.items, query]);

  const filteredTags = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const items = tagsQuery.data?.items ?? [];
    return needle
      ? items.filter((tag) => tag.name.toLocaleLowerCase().includes(needle))
      : items;
  }, [query, tagsQuery.data?.items]);

  const apply = useCallback(() => {
    onContextChange?.({
      ...context,
      ...buildWeKnoraScopeContext(draft),
    });
    setOpen(false);
  }, [context, draft, onContextChange]);

  return (
    <>
      <PromptInputButton
        aria-label={
          selectedCount > 0
            ? `知识范围，已选择 ${selectedCount} 项`
            : "选择知识范围"
        }
        className={cn(
          "max-w-36 min-w-0 gap-1 px-2!",
          selectedCount > 0 && "border-primary/40 bg-primary/10",
          className,
        )}
        data-active={selectedCount > 0}
        data-selected-count={selectedCount}
        data-testid="knowledge-scope-trigger"
        disabled={disabled}
        onClick={() => setOpen(true)}
        type="button"
      >
        <BookOpenIcon className="size-3" />
        <span className="text-xs font-normal whitespace-nowrap">知识库</span>
      </PromptInputButton>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="h-[min(46rem,calc(100dvh-1rem))] max-h-[calc(100dvh-1rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-4xl"
          data-testid="knowledge-scope-dialog"
        >
          <DialogHeader className="border-b px-5 py-4 pr-12 text-left">
            <DialogTitle>选择本次任务的知识范围</DialogTitle>
            <DialogDescription>
              可选择整个知识库，也可精确到文件或标签；整库与其下的细分范围不会重复生效。
            </DialogDescription>
          </DialogHeader>

          <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[17rem_minmax(0,1fr)]">
            <section className="flex min-h-0 flex-col border-b md:border-r md:border-b-0">
              <div className="text-muted-foreground px-4 pt-3 pb-2 text-xs font-medium">
                知识库
              </div>
              <ScrollArea className="min-h-32 flex-1 md:min-h-0">
                <div className="space-y-1 px-2 pb-3">
                  {isLoading && (
                    <div className="text-muted-foreground px-3 py-4 text-sm">
                      正在加载知识库…
                    </div>
                  )}
                  {isError && (
                    <div className="text-destructive px-3 py-4 text-sm">
                      知识库服务暂时不可用
                    </div>
                  )}
                  {!isLoading && !isError && knowledgeBases.length === 0 && (
                    <div className="text-muted-foreground px-3 py-4 text-sm">
                      暂无可用知识库
                    </div>
                  )}
                  {knowledgeBases.map((kb) => {
                    const item = knowledgeBaseItem(kb.id)!;
                    const selected = isWeKnoraScopeItemSelected(draft, item);
                    const active = kb.id === activeKnowledgeBaseId;
                    return (
                      <div
                        className={cn(
                          "flex items-center gap-2 rounded-lg border border-transparent p-2",
                          active && "bg-accent border-border",
                        )}
                        key={kb.id}
                      >
                        <button
                          aria-pressed={selected}
                          aria-label={`${selected ? "取消选择" : "选择"}知识库 ${kb.name}`}
                          className="rounded-md"
                          onClick={() => toggle(item)}
                          type="button"
                        >
                          <SelectionMark selected={selected} />
                        </button>
                        <button
                          className="min-w-0 flex-1 text-left"
                          onClick={() => {
                            setActiveKnowledgeBaseId(kb.id);
                            setQuery("");
                          }}
                          type="button"
                        >
                          <span className="block truncate text-sm font-medium">
                            {kb.name}
                          </span>
                          <span className="text-muted-foreground block truncate text-xs">
                            {kb.knowledge_count ?? 0} 个文件
                            {kb.processing_count
                              ? ` · ${kb.processing_count} 个处理中`
                              : ""}
                          </span>
                        </button>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            </section>

            <section className="flex min-h-0 flex-col">
              {activeKnowledgeBase ? (
                <>
                  <div className="space-y-3 border-b px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold">
                          {activeKnowledgeBase.name}
                        </div>
                        <div className="text-muted-foreground text-xs">
                          选择文件或标签会将该库切换为精确范围
                        </div>
                      </div>
                      <div className="bg-muted flex shrink-0 rounded-lg p-1">
                        <Button
                          className="h-7 gap-1.5 px-2 text-xs"
                          onClick={() => {
                            setSection("files");
                            setQuery("");
                          }}
                          size="sm"
                          variant={section === "files" ? "secondary" : "ghost"}
                        >
                          <FileTextIcon className="size-3.5" />
                          文件
                        </Button>
                        <Button
                          className="h-7 gap-1.5 px-2 text-xs"
                          onClick={() => {
                            setSection("tags");
                            setQuery("");
                          }}
                          size="sm"
                          variant={section === "tags" ? "secondary" : "ghost"}
                        >
                          <TagsIcon className="size-3.5" />
                          标签
                        </Button>
                      </div>
                    </div>
                    <div className="relative">
                      <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                      <Input
                        aria-label={
                          section === "files" ? "搜索文件" : "搜索标签"
                        }
                        className="pl-9"
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder={
                          section === "files" ? "搜索文件" : "搜索标签"
                        }
                        value={query}
                      />
                    </div>
                  </div>

                  <ScrollArea className="min-h-0 flex-1">
                    <div className="space-y-1 p-3">
                      {section === "files" && documentsQuery.isLoading && (
                        <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                          正在加载文件…
                        </div>
                      )}
                      {section === "files" && documentsQuery.isError && (
                        <div className="text-destructive px-3 py-6 text-center text-sm">
                          文件列表加载失败
                        </div>
                      )}
                      {section === "files" &&
                        !documentsQuery.isLoading &&
                        filteredDocuments.length === 0 && (
                          <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                            没有匹配的文件
                          </div>
                        )}
                      {section === "files" &&
                        filteredDocuments.map((document) => {
                          const name =
                            document.title ?? document.file_name ?? document.id;
                          const item: WeKnoraScopeItem = {
                            id: document.id,
                            name,
                            type: "file",
                            kb_id: activeKnowledgeBase.id,
                            kb_name: activeKnowledgeBase.name,
                            ...(activeKnowledgeBase.access_tenant_id !==
                            undefined
                              ? {
                                  tenant_id: String(
                                    activeKnowledgeBase.access_tenant_id,
                                  ),
                                }
                              : {}),
                          };
                          const selected = isWeKnoraScopeItemSelected(
                            draft,
                            item,
                          );
                          return (
                            <button
                              aria-pressed={selected}
                              className="hover:bg-accent flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left"
                              key={document.id}
                              onClick={() => toggle(item)}
                              type="button"
                            >
                              <SelectionMark selected={selected} />
                              <FileTextIcon className="text-muted-foreground size-4 shrink-0" />
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm">
                                  {name}
                                </span>
                                <span className="text-muted-foreground block truncate text-xs">
                                  {document.file_type ?? "文档"}
                                  {document.parse_status
                                    ? ` · ${document.parse_status}`
                                    : ""}
                                </span>
                              </span>
                            </button>
                          );
                        })}

                      {section === "tags" && tagsQuery.isLoading && (
                        <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                          正在加载标签…
                        </div>
                      )}
                      {section === "tags" && tagsQuery.isError && (
                        <div className="text-destructive px-3 py-6 text-center text-sm">
                          标签列表加载失败
                        </div>
                      )}
                      {section === "tags" &&
                        !tagsQuery.isLoading &&
                        filteredTags.length === 0 && (
                          <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                            没有匹配的标签
                          </div>
                        )}
                      {section === "tags" &&
                        filteredTags.map((tag) => {
                          const item: WeKnoraScopeItem = {
                            id: tag.id,
                            name: tag.name,
                            type: "tag",
                            kb_id: activeKnowledgeBase.id,
                            kb_name: activeKnowledgeBase.name,
                            ...(activeKnowledgeBase.access_tenant_id !==
                            undefined
                              ? {
                                  tenant_id: String(
                                    activeKnowledgeBase.access_tenant_id,
                                  ),
                                }
                              : {}),
                          };
                          const selected = isWeKnoraScopeItemSelected(
                            draft,
                            item,
                          );
                          return (
                            <button
                              aria-pressed={selected}
                              className="hover:bg-accent flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left"
                              key={tag.id}
                              onClick={() => toggle(item)}
                              type="button"
                            >
                              <SelectionMark selected={selected} />
                              <TagsIcon className="text-muted-foreground size-4 shrink-0" />
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm">
                                  {tag.name}
                                </span>
                                <span className="text-muted-foreground block text-xs">
                                  {tag.knowledge_count ?? 0} 个文件
                                </span>
                              </span>
                            </button>
                          );
                        })}
                    </div>
                  </ScrollArea>
                </>
              ) : (
                <div className="text-muted-foreground flex flex-1 items-center justify-center p-6 text-sm">
                  请先选择一个知识库查看其文件和标签
                </div>
              )}
            </section>
          </div>

          <DialogFooter className="border-t px-4 py-3 sm:items-center sm:justify-between">
            <div className="text-muted-foreground text-left text-xs">
              已选择 {draft.length} 项
            </div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button
                disabled={draft.length === 0}
                onClick={() => setDraft([])}
                variant="ghost"
              >
                清空范围
              </Button>
              <Button onClick={() => setOpen(false)} variant="outline">
                取消
              </Button>
              <Button onClick={apply}>应用范围</Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
