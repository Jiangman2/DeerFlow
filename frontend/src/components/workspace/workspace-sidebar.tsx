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

import { WorkspaceChannelsList } from "./channels/workspace-channels-list";
import { RecentChatList } from "./recent-chat-list";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { open: isSidebarOpen, isMobile, setOpenMobile } = useSidebar();
  const pathname = usePathname();
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
    <>
      <Sidebar
        variant="sidebar"
        collapsible="icon"
        className="border-sidebar-border text-sidebar-foreground border-r bg-[linear-gradient(180deg,oklch(0.94_0.04_248)_0%,oklch(0.91_0.05_250)_52%,oklch(0.88_0.06_252)_100%)] dark:bg-[linear-gradient(180deg,oklch(0.22_0.055_253)_0%,oklch(0.18_0.05_255)_100%)]"
        {...props}
      >
        <SidebarHeader className="mx-1 rounded-md border border-transparent bg-transparent py-0 group-data-[collapsible=icon]:mx-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="mx-1 rounded-md border border-transparent bg-transparent group-data-[collapsible=icon]:mx-0">
          <WorkspaceNavChatList />
          <WorkspaceChannelsList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter className="mx-1 rounded-md border border-transparent bg-transparent group-data-[collapsible=icon]:mx-0">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
