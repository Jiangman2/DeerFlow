"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Archive,
  Download,
  FileJson,
  FileText,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Share2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { getAPIClient } from "@/core/api";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import {
  useInfiniteThreads,
  usePinThread,
  useRenameThread,
} from "@/core/threads/hooks";
import { loadStaticDemoThreads } from "@/core/threads/static-demo";
import type { AgentThread, AgentThreadState } from "@/core/threads/types";
import {
  channelSourceOfThread,
  isThreadPinned,
  pathOfThread,
  sortPinnedThreads,
  titleOfThread,
} from "@/core/threads/utils";
import { env } from "@/env";
import { isIMEComposing } from "@/lib/ime";

import { ThreadChannelIcon } from "./thread-channel-source";
import { useThreadDeleteDialog } from "./thread-delete-dialog";
import { useThreadArchiveAction } from "./use-thread-archive-action";

export function RecentChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const archiveAction = useThreadArchiveAction();
  const requestDelete = useThreadDeleteDialog();
  const {
    data: infiniteThreads,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteThreads({
    archived:
      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? undefined : false,
  });
  const { data: staticDemoThreads } = useQuery({
    queryKey: ["static-demo-threads"],
    queryFn: () => loadStaticDemoThreads(),
    enabled: env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true",
    staleTime: Number.POSITIVE_INFINITY,
  });
  const threads = useMemo(() => {
    const liveThreads = infiniteThreads?.pages.flat() ?? [];
    if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true") {
      return liveThreads;
    }
    const demoIds = new Set(
      (staticDemoThreads ?? []).map((thread) => thread.thread_id),
    );
    return [
      ...(staticDemoThreads ?? []),
      ...liveThreads.filter((thread) => !demoIds.has(thread.thread_id)),
    ];
  }, [infiniteThreads, staticDemoThreads]);
  const displayedThreads = useMemo(() => {
    const demoThreads = threads.filter(
      (thread) => thread.metadata?.demo === true,
    );
    const liveThreads = threads.filter(
      (thread) => thread.metadata?.demo !== true,
    );
    return [...demoThreads, ...sortPinnedThreads(liveThreads)];
  }, [threads]);

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasNextPage) {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "120px 0px 120px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  const { mutate: renameThread } = useRenameThread();
  const { mutate: updatePinnedThread } = usePinThread();

  // Rename dialog state
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameThreadId, setRenameThreadId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const handleRenameClick = useCallback(
    (threadId: string, currentTitle: string) => {
      setRenameThreadId(threadId);
      setRenameValue(currentTitle);
      setRenameDialogOpen(true);
    },
    [],
  );

  const handleRenameSubmit = useCallback(() => {
    if (renameThreadId && renameValue.trim()) {
      renameThread({ threadId: renameThreadId, title: renameValue.trim() });
      setRenameDialogOpen(false);
      setRenameThreadId(null);
      setRenameValue("");
    }
  }, [renameThread, renameThreadId, renameValue]);

  const handleTogglePin = useCallback(
    (thread: AgentThread) => {
      updatePinnedThread(
        {
          threadId: thread.thread_id,
          pinned: !isThreadPinned(thread),
        },
        {
          onError: (error) => {
            toast.error(
              error instanceof Error && error.message
                ? error.message
                : t.chats.pinChatFailed,
            );
          },
        },
      );
    },
    [t.chats.pinChatFailed, updatePinnedThread],
  );

  const handleShare = useCallback(
    async (thread: AgentThread) => {
      const baseUrl = window.location.origin;
      const shareUrl = `${baseUrl}${pathOfThread(thread)}`;
      try {
        const didCopy = await writeTextToClipboard(shareUrl);
        if (!didCopy) {
          toast.error(t.clipboard.failedToCopyToClipboard);
          return;
        }

        toast.success(t.clipboard.linkCopied);
      } catch {
        toast.error(t.clipboard.failedToCopyToClipboard);
      }
    },
    [t],
  );

  const handleExport = useCallback(
    async (thread: AgentThread, format: "markdown" | "json") => {
      try {
        const apiClient = getAPIClient(undefined, thread.thread_id);
        const state = await apiClient.threads.getState<AgentThreadState>(
          thread.thread_id,
        );
        const messages = state.values?.messages ?? [];
        if (messages.length === 0) {
          toast.error(t.conversation.noMessages);
          return;
        }
        if (format === "markdown") {
          exportThreadAsMarkdown(thread, messages);
        } else {
          exportThreadAsJSON(thread, messages);
        }
        toast.success(t.common.exportSuccess);
      } catch {
        toast.error("Failed to export conversation");
      }
    },
    [t],
  );

  if (threads.length === 0) {
    return null;
  }
  return (
    <>
      <SidebarGroup>
        <SidebarGroupLabel>对话</SidebarGroupLabel>
        <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
          <SidebarMenu>
            <div className="flex w-full flex-col gap-1">
              {displayedThreads.map((thread, index) => {
                const previousThread = displayedThreads[index - 1];
                const isDemoThread = thread.metadata?.demo === true;
                const showDemoLabel =
                  isDemoThread &&
                  (index === 0 || previousThread?.metadata?.demo !== true);
                const showRecentLabel =
                  !isDemoThread &&
                  (index === 0 || previousThread?.metadata?.demo === true);
                const isActive = pathOfThread(thread) === pathname;
                const channelSource = channelSourceOfThread(thread);
                const pinned = isThreadPinned(thread);
                return (
                  <Fragment key={thread.thread_id}>
                    {(showDemoLabel || showRecentLabel) && (
                      <div className="text-muted-foreground/70 mt-3 mb-1 px-2 text-[11px] font-medium tracking-wide first:mt-0">
                        {showDemoLabel ? "演示对话" : "最近对话"}
                      </div>
                    )}
                    <SidebarMenuItem className="group/side-menu-item">
                      <SidebarMenuButton
                        isActive={isActive}
                        className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm"
                        asChild
                      >
                        <Link
                          className="text-muted-foreground min-w-0 whitespace-nowrap group-hover/side-menu-item:overflow-hidden"
                          href={pathOfThread(thread)}
                        >
                          <ThreadChannelIcon source={channelSource} />
                          {pinned ? (
                            <Pin
                              aria-hidden="true"
                              className="text-muted-foreground size-3.5 shrink-0"
                            />
                          ) : null}
                          <span className="min-w-0 truncate">
                            {titleOfThread(thread)}
                          </span>
                          {channelSource && (
                            <span
                              className="bg-muted text-muted-foreground ml-auto inline-flex h-5 max-w-14 shrink-0 items-center rounded-md px-1.5 text-[10px] font-medium"
                              title={`${channelSource.label} channel`}
                            >
                              <span className="truncate">
                                {channelSource.label}
                              </span>
                            </span>
                          )}
                        </Link>
                      </SidebarMenuButton>
                      {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                        thread.metadata?.demo !== true && (
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <SidebarMenuAction
                                showOnHover
                                className="bg-background/50 hover:bg-background after:left-0!"
                              >
                                <MoreHorizontal />
                                <span className="sr-only">{t.common.more}</span>
                              </SidebarMenuAction>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent
                              className="w-48 rounded-lg"
                              side={"right"}
                              align={"start"}
                            >
                              <DropdownMenuItem
                                onSelect={() => handleTogglePin(thread)}
                              >
                                {pinned ? (
                                  <PinOff className="text-muted-foreground" />
                                ) : (
                                  <Pin className="text-muted-foreground" />
                                )}
                                <span>
                                  {pinned ? t.chats.unpinChat : t.chats.pinChat}
                                </span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() =>
                                  handleRenameClick(
                                    thread.thread_id,
                                    titleOfThread(thread),
                                  )
                                }
                              >
                                <Pencil className="text-muted-foreground" />
                                <span>{t.common.rename}</span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => handleShare(thread)}
                              >
                                <Share2 className="text-muted-foreground" />
                                <span>{t.common.share}</span>
                              </DropdownMenuItem>
                              <DropdownMenuSub>
                                <DropdownMenuSubTrigger>
                                  <Download className="text-muted-foreground" />
                                  <span>{t.common.export}</span>
                                </DropdownMenuSubTrigger>
                                <DropdownMenuSubContent>
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      handleExport(thread, "markdown")
                                    }
                                  >
                                    <FileText className="text-muted-foreground" />
                                    <span>{t.common.exportAsMarkdown}</span>
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      handleExport(thread, "json")
                                    }
                                  >
                                    <FileJson className="text-muted-foreground" />
                                    <span>{t.common.exportAsJSON}</span>
                                  </DropdownMenuItem>
                                </DropdownMenuSubContent>
                              </DropdownMenuSub>
                              <DropdownMenuItem
                                disabled={archiveAction.isPending}
                                onSelect={() =>
                                  archiveAction.setArchived(
                                    thread.thread_id,
                                    true,
                                  )
                                }
                              >
                                <Archive className="text-muted-foreground" />
                                <span>{t.chats.archiveChat}</span>
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                onSelect={() =>
                                  requestDelete({
                                    thread,
                                    recentThreadId:
                                      displayedThreads[0]?.thread_id,
                                  })
                                }
                              >
                                <Trash2 className="text-muted-foreground" />
                                <span>{t.common.delete}</span>
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        )}
                    </SidebarMenuItem>
                  </Fragment>
                );
              })}
              {hasNextPage && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mx-2 my-1 w-[calc(100%-1rem)] justify-center text-xs"
                    onClick={() => void fetchNextPage()}
                    disabled={isFetchingNextPage}
                    data-testid="recent-chat-list-load-more"
                  >
                    {isFetchingNextPage
                      ? t.chats.loadingMore
                      : t.chats.loadOlderChats}
                  </Button>
                  <div
                    ref={sentinelRef}
                    aria-hidden="true"
                    className="h-px w-full"
                    data-testid="recent-chat-list-sentinel"
                  />
                </>
              )}
            </div>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>

      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>{t.common.rename}</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder={t.common.rename}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isIMEComposing(e)) {
                  e.preventDefault();
                  handleRenameSubmit();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameDialogOpen(false)}
            >
              {t.common.cancel}
            </Button>
            <Button onClick={handleRenameSubmit}>{t.common.save}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
