"use client";

import {
  BotIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  FilterIcon,
  PlusIcon,
  RotateCcwIcon,
  SearchIcon,
  StoreIcon,
  UserIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ModulePageTitle } from "@/components/workspace/module-page-title";
import {
  AgentsApiDisabledError,
  SHARED_EXTERNAL_AGENTS,
  SHARED_EXTERNAL_AGENT_NAME_ALIASES,
  collectDigitalEmployeeTags,
  filterDigitalEmployees,
  useAgents,
  useDigitalEmployeeCategories,
  useTeamAgents,
} from "@/core/agents";
import type { Agent, DigitalEmployeeCategory } from "@/core/agents";
import { applyEmployeeMetadata } from "@/core/agents/employee-directory";
import { useEmployeeDirectory } from "@/core/agents/employee-directory-hooks";
import { galleryPageSize } from "@/core/agents/gallery-page-size";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { AgentCard } from "./agent-card";
import { BusinessDomainBadge } from "./business-domain-badge";
import { DigitalEmployeeSettingsDialog } from "./digital-employee-settings-dialog";

type GalleryTab = "square" | "mine";
const POPULAR_TAG_PREVIEW_COUNT = 8;
const MAX_POPULAR_TAGS = 24;

function getPaginationItems(currentPage: number, totalPages: number) {
  const pages = [1, currentPage - 1, currentPage, currentPage + 1, totalPages]
    .filter((page) => page >= 1 && page <= totalPages)
    .filter((page, index, values) => values.indexOf(page) === index)
    .sort((a, b) => a - b);

  const items: Array<number | string> = [];
  let previousPage = 0;
  for (const page of pages) {
    if (page - previousPage > 1) {
      items.push(`ellipsis-${previousPage}-${page}`);
    }
    items.push(page);
    previousPage = page;
  }
  return items;
}

function FilterPanel({
  agents,
  categories,
  selectedCategory,
  selectedTags,
  onCategoryChange,
  onTagToggle,
  onReset,
  discoveryControl,
}: {
  agents: Agent[];
  categories: DigitalEmployeeCategory[];
  selectedCategory: string;
  selectedTags: Set<string>;
  onCategoryChange: (category: string) => void;
  onTagToggle: (tag: string) => void;
  onReset: () => void;
  discoveryControl?: React.ReactNode;
}) {
  const { t } = useI18n();
  const [categoriesOpen, setCategoriesOpen] = useState(false);
  const [showAllTags, setShowAllTags] = useState(false);
  const tags = collectDigitalEmployeeTags(agents).slice(0, MAX_POPULAR_TAGS);
  const previewTags = tags.slice(0, POPULAR_TAG_PREVIEW_COUNT);
  const selectedOverflowTags = tags.filter(
    ({ label }) =>
      selectedTags.has(label) &&
      !previewTags.some((preview) => preview.label === label),
  );
  const visibleTags = showAllTags
    ? tags
    : [...previewTags, ...selectedOverflowTags];
  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const agent of agents) {
      if (agent.category_id) {
        counts.set(agent.category_id, (counts.get(agent.category_id) ?? 0) + 1);
      }
    }
    return counts;
  }, [agents]);
  const selectedCategoryName =
    selectedCategory === "all"
      ? t.agents.allCategories
      : (categories.find((category) => category.id === selectedCategory)
          ?.name ?? t.agents.allCategories);

  function categoryButton(category: DigitalEmployeeCategory) {
    return (
      <button
        key={category.id}
        type="button"
        title={category.description}
        data-testid="business-domain-option"
        className={cn(
          "flex min-h-9 min-w-0 items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors",
          selectedCategory === category.id
            ? "bg-accent text-accent-foreground font-medium"
            : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
        onClick={() => onCategoryChange(category.id)}
      >
        <span className="truncate">{category.name}</span>
        <span className="flex shrink-0 items-center gap-1.5">
          <BusinessDomainBadge domainGroup={category.domain_group} />
          <span className="min-w-4 text-right text-xs tabular-nums">
            {categoryCounts.get(category.id) ?? 0}
          </span>
        </span>
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold tracking-wide uppercase">
            {t.agents.category}
          </h3>
          {selectedCategory !== "all" || selectedTags.size > 0 ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground h-6 px-1.5 text-[11px]"
              onClick={onReset}
            >
              <RotateCcwIcon />
              {t.agents.resetFilters}
            </Button>
          ) : null}
        </div>
        <Collapsible open={categoriesOpen} onOpenChange={setCategoriesOpen}>
          <CollapsibleTrigger asChild>
            <Button
              type="button"
              variant="outline"
              data-testid="business-domain-toggle"
              aria-label={
                categoriesOpen
                  ? t.agents.collapseCategories
                  : t.agents.expandCategories
              }
              className="h-auto min-h-10 w-full justify-between gap-2 px-2.5 py-2 text-left"
            >
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">
                  {selectedCategoryName}
                </span>
                <span className="text-muted-foreground block text-[11px] font-normal">
                  {t.agents.businessDomainCount.replace(
                    "{count}",
                    String(categories.length),
                  )}
                </span>
              </span>
              {categoriesOpen ? (
                <ChevronUpIcon aria-hidden="true" />
              ) : (
                <ChevronDownIcon aria-hidden="true" />
              )}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div
              data-testid="business-domain-directory"
              className="mt-2 grid gap-0.5 pr-1"
            >
              <button
                type="button"
                className={cn(
                  "flex min-h-9 items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors",
                  selectedCategory === "all"
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                onClick={() => onCategoryChange("all")}
              >
                <span>{t.agents.allCategories}</span>
                <span className="text-xs tabular-nums">{agents.length}</span>
              </button>
              {categories.map(categoryButton)}
            </div>
          </CollapsibleContent>
        </Collapsible>
      </section>

      <Separator />

      <section data-testid="popular-tag-filter">
        <h3 className="mb-3 text-xs font-semibold tracking-wide uppercase">
          {t.agents.popularTags}
        </h3>
        {discoveryControl}
        {tags.length > 0 ? (
          <>
            <div className="flex flex-wrap gap-1.5">
              {visibleTags.map(({ label, count }) => {
                const selected = selectedTags.has(label);
                return (
                  <button
                    key={label}
                    type="button"
                    data-testid="popular-tag-option"
                    onClick={() => onTagToggle(label)}
                    aria-pressed={selected}
                    className={cn(
                      "rounded-md border px-2 py-1 text-[11px] transition-colors",
                      selected
                        ? "border-primary bg-primary text-primary-foreground"
                        : "bg-background text-muted-foreground hover:border-primary/50 hover:text-foreground",
                    )}
                  >
                    {label} <span className="opacity-70">{count}</span>
                  </button>
                );
              })}
            </div>
            {tags.length > POPULAR_TAG_PREVIEW_COUNT ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-expanded={showAllTags}
                data-testid="popular-tag-toggle"
                className="text-muted-foreground mt-2 h-7 w-full justify-start px-1.5 text-xs"
                onClick={() => setShowAllTags((current) => !current)}
              >
                {showAllTags
                  ? t.agents.collapseTags
                  : t.agents.expandTags.replace("{count}", String(tags.length))}
                {showAllTags ? (
                  <ChevronUpIcon aria-hidden="true" />
                ) : (
                  <ChevronDownIcon aria-hidden="true" />
                )}
              </Button>
            ) : null}
          </>
        ) : (
          <p className="text-muted-foreground text-xs">{t.agents.noTags}</p>
        )}
      </section>
    </div>
  );
}

export function AgentGallery() {
  const { t } = useI18n();
  const directory = useEmployeeDirectory();
  const [hotOnly, setHotOnly] = useState(true);
  const router = useRouter();
  const { agents, isLoading, error } = useAgents();
  const {
    agents: teamAgents,
    isLoading: isTeamLoading,
    error: teamError,
  } = useTeamAgents();
  const { categories } = useDigitalEmployeeCategories();
  const [tab, setTab] = useState<GalleryTab>("square");
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(5);
  const [listSlot, setListSlot] = useState<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!listSlot) return;
    const media = window.matchMedia("(min-width: 1024px)");
    const measure = () => {
      const fontSize = parseFloat(getComputedStyle(listSlot).fontSize) || 16;
      const next = galleryPageSize(
        listSlot.clientHeight,
        media.matches,
        Math.max(112, fontSize * 7),
      );
      setPageSize(next);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(listSlot);
    media.addEventListener("change", measure);
    measure();
    return () => {
      observer.disconnect();
      media.removeEventListener("change", measure);
    };
  }, [listSlot]);
  const agentsApiDisabled = error instanceof AgentsApiDisabledError;
  const teamAgentsApiDisabled = teamError instanceof AgentsApiDisabledError;
  const sharedAgentNames = new Set(
    SHARED_EXTERNAL_AGENTS.map((agent) => agent.name).concat(
      SHARED_EXTERNAL_AGENT_NAME_ALIASES,
    ),
  );
  const squareAgents = [
    ...SHARED_EXTERNAL_AGENTS,
    ...teamAgents.filter((agent) => !sharedAgentNames.has(agent.name)),
  ];
  const currentAgents = applyEmployeeMetadata(
    tab === "mine" ? agents : squareAgents,
    directory.entries,
  );
  const categoryById = new Map(
    categories.map((category) => [category.id, category.name]),
  );
  const categoryNames = Object.fromEntries(categoryById);
  const filteredAgents = filterDigitalEmployees(currentAgents, {
    hotOnly: tab === "square" && hotOnly,
    query,
    categoryId: selectedCategory === "all" ? null : selectedCategory,
    categoryNames,
    tags: [...selectedTags],
  });
  const totalPages = Math.max(1, Math.ceil(filteredAgents.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const visibleAgents = filteredAgents.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize,
  );
  const paginationItems = getPaginationItems(currentPage, totalPages);
  const activeFilterCount =
    selectedTags.size + (selectedCategory === "all" ? 0 : 1);
  const isLoadingCurrent =
    tab === "mine"
      ? isLoading
      : isTeamLoading || (hotOnly && directory.isPending);

  useEffect(() => {
    setPage(1);
  }, [query, selectedCategory, selectedTags, tab, hotOnly, pageSize]);

  function goToPage(nextPage: number) {
    setPage(Math.min(Math.max(nextPage, 1), totalPages));
    contentRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }

  function toggleTag(tag: string) {
    setSelectedTags((current) => {
      const next = new Set(current);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  function resetFilters() {
    setSelectedCategory("all");
    setSelectedTags(new Set());
  }

  function showAll() {
    setHotOnly(false);
    setQuery("");
    resetFilters();
    setPage(1);
    setFiltersOpen(false);
  }

  const discoveryControl =
    tab === "square" ? (
      <div className="mb-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={hotOnly ? "default" : "outline"}
          aria-pressed={hotOnly}
          onClick={() => setHotOnly(true)}
        >
          {t.employeeDiscovery.hotOnly}
        </Button>
        <Button
          size="sm"
          data-testid="employees-show-all"
          variant={hotOnly ? "outline" : "default"}
          aria-pressed={!hotOnly}
          onClick={showAll}
        >
          {t.employeeDiscovery.showAll}
        </Button>
      </div>
    ) : null;

  const filterPanel = (
    <FilterPanel
      agents={currentAgents}
      categories={categories}
      selectedCategory={selectedCategory}
      selectedTags={selectedTags}
      onCategoryChange={setSelectedCategory}
      onTagToggle={toggleTag}
      onReset={resetFilters}
      discoveryControl={discoveryControl}
    />
  );

  return (
    <div className="cnooc-agents-page flex size-full min-w-0 flex-col">
      <header className="cnooc-page-heading px-4 pt-3 pb-1 sm:px-6 sm:pt-4 sm:pb-1">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
            <div className="flex min-h-11 shrink-0 items-center gap-2">
              <SidebarTrigger className="-ml-2 shrink-0 md:hidden" />
              <ModulePageTitle icon={BotIcon} title={t.agents.title} />
            </div>
            <Button
              onClick={() => router.push("/workspace/agents/new")}
              size="sm"
              className="ml-auto shrink-0 rounded-lg"
            >
              <PlusIcon data-icon="inline-start" />
              <span className="hidden sm:inline">{t.agents.newAgent}</span>
              <span className="sm:hidden">{t.agents.createAgent}</span>
            </Button>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Tabs
              value={tab}
              onValueChange={(value) => setTab(value as GalleryTab)}
              className="shrink-0"
            >
              <TabsList>
                <TabsTrigger value="square">
                  <StoreIcon />
                  {t.agents.agentSquare}
                </TabsTrigger>
                <TabsTrigger value="mine">
                  <UserIcon />
                  {t.agents.myAgents}
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative min-w-0 flex-1">
              <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
              <Input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t.agents.searchPlaceholder}
                className="h-9 bg-white pl-9 shadow-none dark:bg-slate-950"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              className="lg:hidden"
              onClick={() => setFiltersOpen(true)}
            >
              <FilterIcon />
              {t.agents.filters}
              {activeFilterCount > 0 ? (
                <Badge className="ml-1 size-5 justify-center rounded-full p-0 text-[10px]">
                  {activeFilterCount}
                </Badge>
              ) : null}
            </Button>
          </div>
        </div>
      </header>

      <main
        ref={contentRef}
        className="cnooc-page-content app-scroll-region min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5 lg:overflow-hidden"
      >
        <div className="mx-auto grid w-full max-w-6xl min-w-0 gap-5 lg:h-full lg:min-h-0 lg:grid-cols-[17rem_minmax(0,1fr)] lg:items-stretch">
          <aside
            data-testid="desktop-filter-panel"
            className="bg-card relative hidden min-h-0 overflow-hidden rounded-xl border shadow-sm lg:block"
          >
            <div className="app-scroll-region absolute inset-0 overflow-y-auto p-3.5 [scrollbar-gutter:stable]">
              {filterPanel}
            </div>
          </aside>

          <section
            className="min-w-0 lg:flex lg:min-h-0 lg:flex-col"
            data-testid="employee-list-panel"
          >
            {tab === "square" && directory.isError ? (
              <div role="alert" className="mb-3 rounded-lg border p-3 text-sm">
                <p>{t.employeeDiscovery.loadError}</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={() => void directory.refetch()}
                >
                  {t.employeeDiscovery.retry}
                </Button>
              </div>
            ) : null}
            <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
              <p className="text-muted-foreground text-xs">
                {t.agents.resultCount.replace(
                  "{count}",
                  String(filteredAgents.length),
                )}
              </p>
              {query || activeFilterCount > 0 ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setQuery("");
                    resetFilters();
                  }}
                >
                  <RotateCcwIcon />
                  {t.agents.clearAll}
                </Button>
              ) : null}
            </div>

            {agentsApiDisabled && tab === "mine" ? (
              <EmptyState
                icon={BotIcon}
                title={t.agents.featureDisabledTitle}
                description={t.agents.featureDisabledDescription}
              />
            ) : isLoadingCurrent ? (
              <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                {t.common.loading}
              </div>
            ) : tab === "square" && hotOnly && visibleAgents.length === 0 ? (
              <div data-testid="hot-empty-state">
                {!directory.isError && (
                  <EmptyState
                    icon={StoreIcon}
                    title={t.employeeDiscovery.emptyTitle}
                    description={t.employeeDiscovery.emptyHint}
                  />
                )}
                <div className="flex justify-center">
                  <Button onClick={showAll}>
                    {t.employeeDiscovery.showAll}
                  </Button>
                </div>
              </div>
            ) : visibleAgents.length === 0 ? (
              <EmptyState
                icon={tab === "mine" ? BotIcon : StoreIcon}
                title={
                  query || activeFilterCount > 0
                    ? t.agents.noResults
                    : tab === "mine"
                      ? t.agents.emptyTitle
                      : t.agents.squareEmptyTitle
                }
                description={
                  query || activeFilterCount > 0
                    ? t.agents.noResultsHint
                    : tab === "mine"
                      ? t.agents.emptyDescription
                      : t.agents.squareEmptyDescription
                }
              />
            ) : (
              <div className="min-h-0 lg:flex lg:flex-1 lg:flex-col">
                <div
                  ref={setListSlot}
                  data-testid="employee-card-slot"
                  data-page-size={pageSize}
                  className="grid gap-2.5 lg:min-h-0 lg:flex-1 lg:[grid-template-rows:repeat(var(--employee-page-size),minmax(104px,1fr))] lg:overflow-y-auto"
                  style={
                    { "--employee-page-size": pageSize } as React.CSSProperties
                  }
                >
                  {visibleAgents.map((agent) => (
                    <AgentCard
                      key={`${agent.source ?? tab}:${agent.name}`}
                      agent={agent}
                      trackSquareLaunch={tab === "square"}
                      hotPending={directory.update.isPending}
                      onToggleHot={
                        tab === "square" &&
                        directory.isAdmin &&
                        directory.isSuccess &&
                        !!agent.hot_identity &&
                        !directory.isError
                          ? (selected) => {
                              directory.update.mutate(selected, {
                                onSuccess: () =>
                                  toast.success(t.employeeDiscovery.saved),
                                onError: (error) =>
                                  toast.error(
                                    error.message === "conflict"
                                      ? t.employeeDiscovery.conflict
                                      : t.employeeDiscovery.saveError,
                                  ),
                              });
                            }
                          : undefined
                      }
                      categoryLabel={
                        agent.category_id
                          ? categoryById.get(agent.category_id)
                          : undefined
                      }
                      showPublish={tab === "mine"}
                      showDelete={tab === "mine"}
                      onEdit={tab === "mine" ? setEditingAgent : undefined}
                      showUnpublish={
                        tab === "square" &&
                        !teamAgentsApiDisabled &&
                        agent.external_url == null &&
                        agent.published_by_current_user === true
                      }
                    />
                  ))}
                </div>
                {totalPages > 1 ? (
                  <nav
                    aria-label={t.agents.paginationNavigation}
                    data-testid="digital-employee-pagination"
                    className="mt-3 flex shrink-0 flex-wrap items-center justify-center gap-1.5"
                  >
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={currentPage === 1}
                      aria-label={t.agents.previousPage}
                      onClick={() => goToPage(currentPage - 1)}
                    >
                      <ChevronLeftIcon aria-hidden="true" />
                      <span className="hidden sm:inline">
                        {t.agents.previousPage}
                      </span>
                    </Button>
                    {paginationItems.map((item) =>
                      typeof item === "number" ? (
                        <Button
                          key={item}
                          type="button"
                          variant={item === currentPage ? "default" : "outline"}
                          size="icon-sm"
                          aria-current={
                            item === currentPage ? "page" : undefined
                          }
                          aria-label={t.agents.pageNumber.replace(
                            "{page}",
                            String(item),
                          )}
                          onClick={() => goToPage(item)}
                        >
                          {item}
                        </Button>
                      ) : (
                        <span
                          key={item}
                          aria-hidden="true"
                          className="text-muted-foreground flex size-8 items-center justify-center text-sm"
                        >
                          …
                        </span>
                      ),
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={currentPage === totalPages}
                      aria-label={t.agents.nextPage}
                      onClick={() => goToPage(currentPage + 1)}
                    >
                      <span className="hidden sm:inline">
                        {t.agents.nextPage}
                      </span>
                      <ChevronRightIcon aria-hidden="true" />
                    </Button>
                    <span className="text-muted-foreground ml-1 text-xs tabular-nums">
                      {t.agents.pageSummary
                        .replace("{page}", String(currentPage))
                        .replace("{total}", String(totalPages))}
                    </span>
                  </nav>
                ) : null}
              </div>
            )}
          </section>
        </div>
      </main>

      <Sheet open={filtersOpen} onOpenChange={setFiltersOpen}>
        <SheetContent side="left" className="w-[88vw] max-w-sm overflow-hidden">
          <SheetHeader>
            <SheetTitle>{t.agents.filters}</SheetTitle>
            <SheetDescription>{t.agents.filtersHint}</SheetDescription>
          </SheetHeader>
          <div
            data-testid="mobile-filter-scroll"
            className="app-scroll-region min-h-0 flex-1 overflow-y-auto px-4 pb-6 [scrollbar-gutter:stable]"
          >
            {filterPanel}
          </div>
        </SheetContent>
      </Sheet>

      <DigitalEmployeeSettingsDialog
        agent={editingAgent}
        categories={categories}
        open={editingAgent !== null}
        onOpenChange={(open) => {
          if (!open) setEditingAgent(null);
        }}
      />
    </div>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof BotIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-card flex min-h-56 flex-col items-center justify-center gap-3 rounded-xl border px-5 text-center">
      <div className="bg-muted flex size-12 items-center justify-center rounded-full">
        <Icon className="text-muted-foreground size-6" />
      </div>
      <div>
        <p className="font-medium">{title}</p>
        <p className="text-muted-foreground mt-1 max-w-md text-sm">
          {description}
        </p>
      </div>
    </div>
  );
}
