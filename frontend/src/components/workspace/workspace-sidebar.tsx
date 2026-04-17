"use client";

import {
  Sidebar,
  SidebarHeader,
  SidebarContent,
  SidebarFooter,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";

import { RecentChatList } from "./recent-chat-list";
import { WorkspaceHeader } from "./workspace-header";
import { WorkspaceNavChatList } from "./workspace-nav-chat-list";
import { WorkspaceNavMenu } from "./workspace-nav-menu";

export function WorkspaceSidebar({
  ...props
}: React.ComponentProps<typeof Sidebar>) {
  const { open: isSidebarOpen } = useSidebar();
  return (
    <>
      <Sidebar
        variant="sidebar"
        collapsible="icon"
        className="bg-blue-50/35 backdrop-blur-xs border-r border-black/10"
        {...props}
      >
        <SidebarHeader className="bg-blue-50 border-blue-100 py-0 rounded-md border backdrop-blur-xs mx-1 mt-2 mb-1">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="bg-blue-50 border-blue-100 rounded-md border backdrop-blur-xs mx-1 mb-2">
          <WorkspaceNavChatList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter className="bg-blue-50 border-blue-100 rounded-md border backdrop-blur-xs mx-1 mb-2">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
