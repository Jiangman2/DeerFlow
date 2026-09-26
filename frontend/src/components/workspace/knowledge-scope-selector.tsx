"use client";

import { useQueries } from "@tanstack/react-query";
import {
  BookOpenIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FileTextIcon,
  FolderIcon,
  Loader2Icon,
  SearchIcon,
  TagIcon,
  XIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  buildWeKnoraScopeContext,
  isWeKnoraScopeItemSelected,
  MAX_WEKNORA_SCOPE_ITEMS,
  toggleWeKnoraScopeItem,
  useWeKnoraKnowledgeBases,
  useWeKnoraKnowledgeSearch,
  weknoraScopeItemKey,
  weknoraScopeItemsFromContext,
  weknoraTagsQueryKey,
  type WeKnoraKnowledge,
  type WeKnoraKnowledgeBase,
  type WeKnoraScopeItem,
  type WeKnoraTag,
} from "@/core/weknora";
import { listWeKnoraTags } from "@/core/weknora/api";
import { cn } from "@/lib/utils";

export type KnowledgeScopeContext = {
  weknora_knowledge_base_ids?: string[];
  weknora_knowledge_bases?: { id: string; name: string }[];
  weknora_knowledge_ids?: string[];
  weknora_tag_scopes?: {
    knowledge_base_id: string;
    tag_ids: string[];
  }[];
  weknora_scope_items?: WeKnoraScopeItem[];
};

type KnowledgeScopeProps<T> = {
  className?: string;
  context: T & KnowledgeScopeContext;
  disabled?: boolean;
  allowTags?: boolean;
  onContextChange?: (context: T & KnowledgeScopeContext) => void;
};

type ScopeGroup = "root" | "kb" | "tag" | "file";

type TagWithParent = WeKnoraTag & {
  parent: WeKnoraKnowledgeBase;
};

function commitScope<T>(
  context: T & KnowledgeScopeContext,
  items: WeKnoraScopeItem[],
  onContextChange?: (context: T & KnowledgeScopeContext) => void,
) {
  onContextChange?.({
    ...context,
    ...buildWeKnoraScopeContext(items),
  });
}

function scopeIcon(type: WeKnoraScopeItem["type"], className?: string) {
  if (type === "kb") {
    return <FolderIcon className={className} />;
  }
  if (type === "tag") {
    return <TagIcon className={className} />;
  }
  return <FileTextIcon className={className} />;
}

function SelectedMark({ selected }: { selected: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded border",
        selected
          ? "border-[#5f85df] bg-[#5f85df] text-white"
          : "border-[#c8d3e3] bg-white",
      )}
    >
      {selected && <CheckIcon className="size-3" />}
    </span>
  );
}

function ResourceRow({
  children,
  description,
  icon,
  selected,
  testId,
  onSelect,
}: {
  children: ReactNode;
  description?: ReactNode;
  icon: ReactNode;
  selected: boolean;
  testId: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      aria-checked={selected}
      className={cn(
        "min-h-11 w-full max-w-full min-w-0 cursor-pointer items-start gap-2.5 overflow-hidden rounded-lg border border-transparent px-2.5 py-2",
        selected && "border-[#aac0ef] bg-[#edf4ff] text-[#285fc8]",
      )}
      data-testid={testId}
      onSelect={(event) => {
        event.preventDefault();
        onSelect();
      }}
      role="menuitemcheckbox"
    >
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-[#f1f5fb] text-[#587092]">
        {icon}
      </span>
      <span className="min-w-0 flex-1 overflow-hidden">
        <span
          className="line-clamp-2 block min-w-0 text-sm leading-5 font-medium break-all"
          title={typeof children === "string" ? children : undefined}
        >
          {children}
        </span>
        {description && (
          <span className="text-muted-foreground mt-0.5 block min-w-0 overflow-hidden text-xs">
            {description}
          </span>
        )}
      </span>
      <span className="ml-auto shrink-0">
        <SelectedMark selected={selected} />
      </span>
    </DropdownMenuItem>
  );
}

function GroupEntry({
  count,
  icon,
  label,
  loading,
  testId,
  onSelect,
}: {
  count: number;
  icon: ReactNode;
  label: string;
  loading?: boolean;
  testId: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenuItem
      className="min-h-11 cursor-pointer gap-3 rounded-lg px-3"
      data-testid={testId}
      onSelect={(event) => {
        event.preventDefault();
        onSelect();
      }}
    >
      <span className="flex size-7 items-center justify-center rounded-lg bg-[#eef4fd] text-[#4f70ad]">
        {icon}
      </span>
      <span className="flex-1 text-sm font-medium">{label}</span>
      <span className="text-muted-foreground text-xs">
        {loading ? "…" : count}
      </span>
      <ChevronRightIcon className="text-muted-foreground size-4" />
    </DropdownMenuItem>
  );
}

function FileTagChips({ tags }: { tags: WeKnoraTag[] }) {
  if (tags.length === 0) {
    return null;
  }
  return (
    <span className="mt-1 flex min-w-0 flex-wrap gap-1">
      {tags.slice(0, 3).map((tag) => (
        <span
          className="inline-flex max-w-24 items-center gap-1 rounded-full border border-[#d4deee] bg-[#f7f9fc] px-1.5 py-0.5 text-[10px] leading-3.5 text-[#60708a]"
          key={tag.id}
        >
          <span
            aria-hidden="true"
            className="size-1.5 shrink-0 rounded-full bg-[#8d72d8]"
            style={tag.color ? { backgroundColor: tag.color } : undefined}
          />
          <span className="truncate">{tag.name}</span>
        </span>
      ))}
      {tags.length > 3 && (
        <span className="text-muted-foreground text-[10px] leading-4">
          +{tags.length - 3}
        </span>
      )}
    </span>
  );
}

export function KnowledgeScopeChips<T>({
  className,
  context,
  disabled,
  onContextChange,
}: KnowledgeScopeProps<T>) {
  const { data } = useWeKnoraKnowledgeBases();
  const knowledgeBases = useMemo(() => data?.items ?? [], [data?.items]);
  const selectedItems = useMemo(
    () => weknoraScopeItemsFromContext(context, knowledgeBases),
    [context, knowledgeBases],
  );

  const remove = useCallback(
    (item: WeKnoraScopeItem) => {
      const key = weknoraScopeItemKey(item);
      commitScope(
        context,
        selectedItems.filter(
          (candidate) => weknoraScopeItemKey(candidate) !== key,
        ),
        onContextChange,
      );
    },
    [context, onContextChange, selectedItems],
  );

  if (selectedItems.length === 0) {
    return null;
  }

  return (
    <div
      className={cn(
        "cnooc-knowledge-scope-chips flex w-full flex-wrap items-center gap-1.5",
        className,
      )}
      data-testid="knowledge-scope-chips"
    >
      {selectedItems.map((item) => (
        <span
          className="cnooc-knowledge-scope-chip inline-flex h-8 w-fit max-w-60 min-w-0 shrink-0 items-center gap-1.5 rounded-md border border-[#d4ddea] bg-[#f7f9fc] px-2 text-xs font-medium text-[#42526a]"
          data-testid={`knowledge-scope-chip-${item.type}-${item.id}`}
          key={weknoraScopeItemKey(item)}
          title={item.kb_name ? `${item.name} · ${item.kb_name}` : item.name}
        >
          {scopeIcon(item.type, "size-3.5 shrink-0 text-[#5575aa]")}
          <span className="truncate">{item.name}</span>
          <button
            aria-label={`移除${item.name}`}
            className="hover:bg-muted flex size-4 shrink-0 items-center justify-center rounded-full"
            disabled={disabled}
            onClick={() => remove(item)}
            type="button"
          >
            <XIcon className="size-3" />
          </button>
        </span>
      ))}
    </div>
  );
}

export function KnowledgeScopeSelector<T>({
  className,
  context,
  disabled,
  allowTags = true,
  onContextChange,
}: KnowledgeScopeProps<T>) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<ScopeGroup>("root");
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const { data, isError, isLoading, refetch } = useWeKnoraKnowledgeBases();
  const knowledgeBases = useMemo(() => data?.items ?? [], [data?.items]);
  const selectedItems = useMemo(
    () => weknoraScopeItemsFromContext(context, knowledgeBases),
    [context, knowledgeBases],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 180);
    return () => window.clearTimeout(timer);
  }, [query]);

  const fileQuery = useWeKnoraKnowledgeSearch(debouncedQuery, open);
  const tagQueries = useQueries({
    queries: knowledgeBases.map((kb) => {
      const tenantId = kb.access_tenant_id ?? kb.tenant_id;
      return {
        queryKey: weknoraTagsQueryKey(kb.id, tenantId),
        queryFn: () => listWeKnoraTags(kb.id, tenantId),
        enabled: open && allowTags,
        staleTime: 30_000,
      };
    }),
  });

  const allTags = useMemo<TagWithParent[]>(
    () =>
      (allowTags ? tagQueries : []).flatMap((tagQuery, index) => {
        const parent = knowledgeBases[index];
        return parent
          ? (tagQuery.data?.items ?? []).map((tag) => ({ ...tag, parent }))
          : [];
      }),
    [allowTags, knowledgeBases, tagQueries],
  );
  const tagsLoading = tagQueries.some((tagQuery) => tagQuery.isLoading);
  const tagsError = tagQueries.some((tagQuery) => tagQuery.isError);
  const knowledgeBaseById = useMemo(
    () => new Map(knowledgeBases.map((kb) => [kb.id, kb] as const)),
    [knowledgeBases],
  );
  const tagsByParent = useMemo(() => {
    const map = new Map<string, Map<string, WeKnoraTag>>();
    for (const tag of allTags) {
      const tags = map.get(tag.parent.id) ?? new Map<string, WeKnoraTag>();
      tags.set(tag.id, tag);
      map.set(tag.parent.id, tags);
    }
    return map;
  }, [allTags]);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filteredKnowledgeBases = useMemo(
    () =>
      normalizedQuery
        ? knowledgeBases.filter((kb) =>
            `${kb.name} ${kb.description ?? ""}`
              .toLocaleLowerCase()
              .includes(normalizedQuery),
          )
        : knowledgeBases,
    [knowledgeBases, normalizedQuery],
  );
  const filteredTags = useMemo(
    () =>
      normalizedQuery
        ? allTags.filter((tag) =>
            `${tag.name} ${tag.parent.name}`
              .toLocaleLowerCase()
              .includes(normalizedQuery),
          )
        : allTags,
    [allTags, normalizedQuery],
  );
  const files = fileQuery.data?.items ?? [];

  const updateSelection = useCallback(
    (item: WeKnoraScopeItem) => {
      const selected = isWeKnoraScopeItemSelected(selectedItems, item);
      const nextItems = toggleWeKnoraScopeItem(selectedItems, item);
      if (!selected && nextItems.length > MAX_WEKNORA_SCOPE_ITEMS) {
        toast.error(`知识范围最多选择 ${MAX_WEKNORA_SCOPE_ITEMS} 项`);
        return;
      }
      commitScope(context, nextItems, onContextChange);
    },
    [context, onContextChange, selectedItems],
  );

  const knowledgeBaseItem = useCallback(
    (kb: WeKnoraKnowledgeBase): WeKnoraScopeItem => {
      const tenantId = kb.access_tenant_id ?? kb.tenant_id;
      return {
        id: kb.id,
        name: kb.name,
        type: "kb",
        ...(kb.type ? { kb_type: kb.type } : {}),
        ...(tenantId !== undefined ? { tenant_id: String(tenantId) } : {}),
      };
    },
    [],
  );

  const documentItem = useCallback(
    (document: WeKnoraKnowledge): WeKnoraScopeItem | undefined => {
      const parentId = document.knowledge_base_id;
      const parent = parentId ? knowledgeBaseById.get(parentId) : undefined;
      if (!parentId || !parent) {
        return undefined;
      }
      const tenantId = parent.access_tenant_id ?? parent.tenant_id;
      return {
        id: document.id,
        name: document.title ?? document.file_name ?? document.id,
        type: "file",
        kb_id: parentId,
        kb_name: parent.name,
        ...(tenantId !== undefined ? { tenant_id: String(tenantId) } : {}),
      };
    },
    [knowledgeBaseById],
  );

  const documentTags = useCallback(
    (document: WeKnoraKnowledge): WeKnoraTag[] => {
      if (document.tags?.length) {
        return document.tags;
      }
      const parentTags = document.knowledge_base_id
        ? tagsByParent.get(document.knowledge_base_id)
        : undefined;
      const tag = document.tag_id
        ? parentTags?.get(document.tag_id)
        : undefined;
      return tag ? [tag] : [];
    },
    [tagsByParent],
  );

  const renderKnowledgeBases = () =>
    filteredKnowledgeBases.map((kb) => {
      const item = knowledgeBaseItem(kb);
      return (
        <ResourceRow
          description={`${kb.knowledge_count ?? 0} 个文件${kb.org_name ? ` · ${kb.org_name}` : ""}`}
          icon={<FolderIcon className="size-4" />}
          key={kb.id}
          selected={isWeKnoraScopeItemSelected(selectedItems, item)}
          testId={`knowledge-base-item-${kb.id}`}
          onSelect={() => updateSelection(item)}
        >
          {kb.name}
        </ResourceRow>
      );
    });

  const renderTags = () =>
    filteredTags.map((tag) => {
      const tenantId = tag.parent.access_tenant_id ?? tag.parent.tenant_id;
      const item: WeKnoraScopeItem = {
        id: tag.id,
        name: tag.name,
        type: "tag",
        kb_id: tag.parent.id,
        kb_name: tag.parent.name,
        ...(tenantId !== undefined ? { tenant_id: String(tenantId) } : {}),
      };
      return (
        <ResourceRow
          description={`${tag.parent.name} · ${tag.knowledge_count ?? 0} 个文件`}
          icon={
            <TagIcon
              className="size-4"
              style={tag.color ? { color: tag.color } : undefined}
            />
          }
          key={`${tag.parent.id}:${tag.id}`}
          selected={isWeKnoraScopeItemSelected(selectedItems, item)}
          testId={`knowledge-tag-${tag.parent.id}-${tag.id}`}
          onSelect={() => updateSelection(item)}
        >
          {tag.name}
        </ResourceRow>
      );
    });

  const renderFiles = () =>
    files.map((document) => {
      const item = documentItem(document);
      if (!item) {
        return null;
      }
      return (
        <ResourceRow
          description={
            <>
              <span className="block truncate">
                {item.kb_name}
                {document.file_type ? ` · ${document.file_type}` : ""}
              </span>
              <FileTagChips tags={documentTags(document)} />
            </>
          }
          icon={<FileTextIcon className="size-4" />}
          key={`${item.kb_id}:${item.id}`}
          selected={isWeKnoraScopeItemSelected(selectedItems, item)}
          testId={`knowledge-document-${item.kb_id}-${item.id}`}
          onSelect={() => updateSelection(item)}
        >
          {item.name}
        </ResourceRow>
      );
    });

  const showFlatResults = Boolean(normalizedQuery);
  const noFlatResults =
    showFlatResults &&
    filteredKnowledgeBases.length === 0 &&
    filteredTags.length === 0 &&
    files.length === 0 &&
    !fileQuery.isLoading;

  return (
    <DropdownMenu
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (nextOpen) {
          setGroup("root");
          setQuery("");
          setDebouncedQuery("");
        }
      }}
    >
      <DropdownMenuTrigger asChild>
        <PromptInputButton
          aria-label={
            selectedItems.length > 0
              ? `知识范围，已选择 ${selectedItems.length} 项`
              : allowTags
                ? "知识库、标签或文件"
                : "知识库或文件"
          }
          className={cn(
            "min-w-0 gap-1.5 px-2!",
            selectedItems.length > 0 &&
              "border-[#79a2ef]! bg-[#edf4ff]! text-[#2c67d2]!",
            className,
          )}
          data-active={selectedItems.length > 0}
          data-selected-count={selectedItems.length}
          data-testid="knowledge-scope-trigger"
          disabled={disabled}
          type="button"
        >
          <BookOpenIcon className="size-3.5" />
          <span className="text-xs font-normal whitespace-nowrap">知识库</span>
          {selectedItems.length > 0 && (
            <span
              className="cnooc-knowledge-scope-count flex min-w-4 items-center justify-center rounded-full bg-[#4f76d9] px-1 text-[10px] leading-4 font-semibold text-white"
              data-testid="knowledge-scope-count"
            >
              {selectedItems.length}
            </span>
          )}
        </PromptInputButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="flex min-h-0 w-[min(20rem,calc(100vw-1rem))] flex-col overflow-hidden rounded-xl border-[#ccd8e9] p-0 shadow-xl"
        data-testid="knowledge-scope-menu"
        side="top"
        sideOffset={8}
        style={{
          maxHeight:
            "min(32rem, calc(100dvh - 1rem), var(--radix-dropdown-menu-content-available-height))",
        }}
      >
        <div
          className="shrink-0 border-b p-2"
          onKeyDown={(event) => event.stopPropagation()}
        >
          <div className="relative">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              aria-label={
                allowTags ? "搜索知识库、标签或文件" : "搜索知识库或文件"
              }
              className="h-9 pl-8 text-sm"
              onChange={(event) => setQuery(event.target.value)}
              placeholder={
                allowTags ? "搜索知识库、标签或文件" : "搜索知识库或文件"
              }
              value={query}
            />
          </div>
        </div>

        <div
          className="max-h-96 min-h-0 flex-1 touch-pan-y overflow-y-auto overscroll-contain"
          data-testid="knowledge-scope-list"
        >
          <div className="space-y-0.5 p-1.5">
            {!showFlatResults && group === "root" && (
              <>
                <GroupEntry
                  count={knowledgeBases.length}
                  icon={<FolderIcon className="size-4" />}
                  label="知识库"
                  loading={isLoading}
                  testId="knowledge-scope-group-kb"
                  onSelect={() => setGroup("kb")}
                />
                {allowTags ? (
                  <GroupEntry
                    count={allTags.length}
                    icon={<TagIcon className="size-4" />}
                    label="标签"
                    loading={tagsLoading}
                    testId="knowledge-scope-group-tag"
                    onSelect={() => setGroup("tag")}
                  />
                ) : null}
                <GroupEntry
                  count={fileQuery.data?.total ?? files.length}
                  icon={<FileTextIcon className="size-4" />}
                  label="文件"
                  loading={fileQuery.isLoading}
                  testId="knowledge-scope-group-file"
                  onSelect={() => setGroup("file")}
                />
              </>
            )}

            {!showFlatResults && group !== "root" && (
              <>
                <DropdownMenuItem
                  className="cursor-pointer gap-2 rounded-lg px-2.5 py-2 font-medium"
                  data-testid="knowledge-scope-back"
                  onSelect={(event) => {
                    event.preventDefault();
                    setGroup("root");
                  }}
                >
                  <ChevronLeftIcon className="size-4" />
                  {group === "kb"
                    ? "知识库"
                    : group === "tag"
                      ? "标签"
                      : "文件"}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}

            {showFlatResults && filteredKnowledgeBases.length > 0 && (
              <>
                <DropdownMenuLabel className="text-muted-foreground px-2.5 py-1 text-xs">
                  知识库
                </DropdownMenuLabel>
                {renderKnowledgeBases()}
              </>
            )}
            {showFlatResults && filteredTags.length > 0 && (
              <>
                <DropdownMenuLabel className="text-muted-foreground px-2.5 pt-2 pb-1 text-xs">
                  标签
                </DropdownMenuLabel>
                {renderTags()}
              </>
            )}
            {showFlatResults && files.length > 0 && (
              <>
                <DropdownMenuLabel className="text-muted-foreground px-2.5 pt-2 pb-1 text-xs">
                  文件
                </DropdownMenuLabel>
                {renderFiles()}
              </>
            )}

            {!showFlatResults && group === "kb" && renderKnowledgeBases()}
            {!showFlatResults && group === "tag" && renderTags()}
            {!showFlatResults && group === "file" && renderFiles()}

            {(isError || tagsError || fileQuery.isError) && (
              <div className="text-destructive px-3 py-3 text-center text-xs">
                部分知识范围加载失败，请重试
                <button
                  className="ml-1 underline"
                  onClick={() => {
                    void refetch();
                    void fileQuery.refetch();
                    for (const tagQuery of tagQueries) {
                      void tagQuery.refetch();
                    }
                  }}
                  type="button"
                >
                  重新加载
                </button>
              </div>
            )}
            {(fileQuery.isLoading || tagsLoading) && group !== "root" && (
              <div className="text-muted-foreground flex items-center justify-center gap-2 px-3 py-4 text-xs">
                <Loader2Icon className="size-3.5 animate-spin" />
                正在加载…
              </div>
            )}
            {noFlatResults && (
              <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                没有匹配的知识范围
              </div>
            )}
            {!showFlatResults &&
              group === "kb" &&
              !isLoading &&
              knowledgeBases.length === 0 && (
                <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                  暂无可用知识库
                </div>
              )}
            {!showFlatResults &&
              group === "tag" &&
              !tagsLoading &&
              allTags.length === 0 && (
                <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                  暂无可用标签
                </div>
              )}
            {!showFlatResults &&
              group === "file" &&
              !fileQuery.isLoading &&
              files.length === 0 && (
                <div className="text-muted-foreground px-3 py-6 text-center text-sm">
                  暂无可用文件
                </div>
              )}
          </div>
        </div>
        {selectedItems.length > 0 && (
          <div className="flex shrink-0 items-center justify-between border-t bg-[#f8fafc] px-3 py-2 text-xs">
            <span className="font-medium text-[#315fcf]">
              已选择 {selectedItems.length} 项
            </span>
            <button
              className="text-muted-foreground hover:text-foreground"
              data-testid="knowledge-scope-clear"
              onClick={() => {
                commitScope(context, [], onContextChange);
                setOpen(false);
              }}
              type="button"
            >
              清空
            </button>
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
