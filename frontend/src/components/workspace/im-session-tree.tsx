"use client";

import { ChevronRight, MessagesSquare, Users } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Skeleton } from "@/components/ui/skeleton";
import { useI18n } from "@/core/i18n/hooks";
import { imSessionCopy } from "@/core/i18n/im-session-tree";
import {
  useImCapabilities,
  useImGroupThreads,
  useImSessionTree,
} from "@/core/im/hooks";
import {
  groupDisplayName,
  groupStatusNotice,
  threadDisplayTitle,
  treeIsEmpty,
} from "@/core/im/tree";
import type { ImGroupNode, ImThreadItem } from "@/core/im/types";
import { pathOfThread } from "@/core/threads/utils";

import { ImSessionSearch } from "./im-session-search";

const INITIAL_GROUP_LIMIT = 5;
const ALL_GROUP_LIMIT = 200;
const INITIAL_THREADS_PER_GROUP = 5;
const EXPANDED_THREAD_LIMIT = 50;

function ImThreadRow({
  thread,
  currentLabel,
}: {
  thread: ImThreadItem;
  currentLabel: string;
}) {
  const pathname = usePathname();
  const href = pathOfThread(thread.thread_id);
  return (
    <SidebarMenuItem className="group/side-menu-item">
      <SidebarMenuButton
        asChild
        isActive={pathname === href}
        className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm"
      >
        <Link
          className="text-muted-foreground min-w-0 whitespace-nowrap"
          href={href}
          data-testid="im-session-thread"
          data-thread-id={thread.thread_id}
        >
          <MessagesSquare aria-hidden="true" className="size-3.5 shrink-0" />
          <span className="min-w-0 truncate">{threadDisplayTitle(thread)}</span>
          {thread.is_current && (
            <span
              aria-label={currentLabel}
              title={currentLabel}
              className="ml-auto size-1.5 shrink-0 rounded-full bg-green-500"
              data-testid="im-current-thread-marker"
            />
          )}
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

function ImGroupRow({ node }: { node: ImGroupNode }) {
  const { locale } = useI18n();
  const copy = imSessionCopy(locale);
  const [expanded, setExpanded] = useState(false);
  const [showAllThreads, setShowAllThreads] = useState(false);
  const hasMoreThreads = node.thread_count > node.threads.length;
  const { data: page } = useImGroupThreads(
    node.chat_id,
    { limit: EXPANDED_THREAD_LIMIT },
    showAllThreads,
  );
  const notice = groupStatusNotice(node.chat_status);
  const threads =
    showAllThreads && page?.threads && page.threads.length > 0
      ? page.threads
      : node.threads;

  return (
    <Collapsible open={expanded} onOpenChange={setExpanded} key={node.chat_id}>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <SidebarMenuButton
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
            data-testid="im-group-trigger"
            data-chat-id={node.chat_id}
          >
            <ChevronRight
              aria-hidden="true"
              className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90"
            />
            <Users aria-hidden="true" className="size-3.5 shrink-0" />
            <span className="min-w-0 truncate">{groupDisplayName(node)}</span>
            <span className="text-muted-foreground/80 ml-auto shrink-0 text-[10px]">
              {node.thread_count}
            </span>
            {notice && (
              <span className="bg-muted text-muted-foreground inline-flex h-4 shrink-0 items-center rounded px-1 text-[10px]">
                {notice === "left" ? copy.imGroupLeft : copy.imGroupDissolved}
              </span>
            )}
          </SidebarMenuButton>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarMenu>
            <div className="flex w-full flex-col gap-1 pl-4">
              {threads.map((thread) => (
                <ImThreadRow
                  key={thread.thread_id}
                  thread={thread}
                  currentLabel={copy.imCurrentThread}
                />
              ))}
              {hasMoreThreads && !showAllThreads && (
                <SidebarMenuItem>
                  <button
                    type="button"
                    className="text-muted-foreground hover:text-foreground w-full px-2 py-1 text-left text-[11px]"
                    onClick={() => setShowAllThreads(true)}
                    data-testid="im-group-load-more"
                  >
                    {copy.imLoadMore(node.thread_count)}
                  </button>
                </SidebarMenuItem>
              )}
            </div>
          </SidebarMenu>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  );
}

export function ImSessionTree() {
  const { locale } = useI18n();
  const copy = imSessionCopy(locale);
  const { data: capabilities, isLoading: capabilitiesLoading } =
    useImCapabilities();
  const enabled = capabilities?.im_session_tree_enabled === true;
  const [showAllGroups, setShowAllGroups] = useState(false);
  const {
    data: tree,
    isPending,
    isError,
    refetch,
  } = useImSessionTree(
    {
      groupsLimit: showAllGroups ? ALL_GROUP_LIMIT : INITIAL_GROUP_LIMIT,
      threadsPerGroup: INITIAL_THREADS_PER_GROUP,
      groupOffset: 0,
    },
    enabled,
  );

  if (capabilitiesLoading || !enabled) {
    return null;
  }

  if (isPending) {
    return (
      <SidebarGroup>
        <SidebarGroupLabel>{copy.privateSection}</SidebarGroupLabel>
        <SidebarGroupContent className="group-data-[collapsible=icon]:hidden">
          <div className="flex flex-col gap-2 px-2 py-1">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        </SidebarGroupContent>
      </SidebarGroup>
    );
  }

  if (isError) {
    return (
      <SidebarGroup>
        <SidebarGroupContent className="group-data-[collapsible=icon]:hidden">
          <div className="text-muted-foreground flex items-center gap-2 px-2 py-1 text-[11px]">
            <span className="min-w-0 truncate">{copy.imLoadFailed}</span>
            <button
              type="button"
              className="text-primary shrink-0 underline"
              onClick={() => void refetch()}
            >
              {copy.imRetry}
            </button>
          </div>
        </SidebarGroupContent>
      </SidebarGroup>
    );
  }

  if (treeIsEmpty(tree)) {
    return null;
  }

  const privateThreads = tree?.private.threads ?? [];
  const groups = tree?.groups ?? [];
  const groupsTotal = tree?.groups_total ?? groups.length;

  return (
    <>
      <ImSessionSearch />
      {privateThreads.length > 0 && (
        <SidebarGroup>
          <SidebarGroupLabel>{copy.privateSection}</SidebarGroupLabel>
          <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
            <SidebarMenu>
              <div className="flex w-full flex-col gap-1">
                {privateThreads.map((thread) => (
                  <ImThreadRow
                    key={thread.thread_id}
                    thread={thread}
                    currentLabel={copy.imCurrentThread}
                  />
                ))}
              </div>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      )}
      {groups.length > 0 && (
        <SidebarGroup>
          <SidebarGroupLabel>{copy.groupSection}</SidebarGroupLabel>
          <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
            <SidebarMenu>
              <div className="flex w-full flex-col gap-1">
                {groups.map((node) => (
                  <ImGroupRow key={node.chat_id} node={node} />
                ))}
                {groupsTotal > groups.length && !showAllGroups && (
                  <SidebarMenuItem>
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground w-full px-2 py-1 text-left text-[11px]"
                      onClick={() => setShowAllGroups(true)}
                      data-testid="im-show-all-groups"
                    >
                      {copy.imShowAllGroups(groupsTotal)}
                    </button>
                  </SidebarMenuItem>
                )}
              </div>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      )}
    </>
  );
}
