"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useImCapabilities } from "@/core/im/hooks";

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { ImSessionTree } from "./im-session-tree";
import { RecentChatList } from "./recent-chat-list";
import { ThreadDeleteDialogProvider } from "./thread-delete-dialog";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { open: isSidebarOpen, isMobile, setOpenMobile } = useSidebar();
  const pathname = usePathname();
  const { data: imCapabilities } = useImCapabilities();
  const imSessionTreeEnabled =
    imCapabilities?.im_session_tree_enabled === true;
  const previousPathnameRef = useRef(pathname);
  const hasObservedInitialPathnameRef = useRef(false);

  useEffect(() => {
    if (!hasObservedInitialPathnameRef.current) {
      hasObservedInitialPathnameRef.current = true;
      previousPathnameRef.current = pathname;
      return;
    }
    if (isMobile && previousPathnameRef.current !== pathname) {
      setOpenMobile(false);
    }
    previousPathnameRef.current = pathname;
  }, [isMobile, pathname, setOpenMobile]);
  return (
    <ThreadDeleteDialogProvider>
      <Sidebar
        variant="sidebar"
        collapsible="icon"
        className="mobile-workspace-sidebar cnooc-workspace-sidebar border-sidebar-border text-sidebar-foreground border-r"
        {...props}
      >
        <SidebarHeader className="mx-1 rounded-md border border-transparent bg-transparent py-0 group-data-[collapsible=icon]:mx-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="mx-1 rounded-md border border-transparent bg-transparent group-data-[collapsible=icon]:mx-0">
          <WorkspaceNavChatList />
          <WorkspaceChannelsList />
          {(isSidebarOpen || isMobile) && <ImSessionTree />}
          {(isSidebarOpen || isMobile) && (
            <RecentChatList hideImThreads={imSessionTreeEnabled} />
          )}
        </SidebarContent>
        <SidebarFooter className="mx-1 rounded-md border border-transparent bg-transparent group-data-[collapsible=icon]:mx-0">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </ThreadDeleteDialogProvider>
  );
}
