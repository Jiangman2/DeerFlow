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
        className="bg-[#eff6ff] border-r border-[#bfdbfe]"
        {...props}
      >
        <SidebarHeader className="bg-[#eff6ff] border-[#dbeafe] py-0 rounded-md border mx-1 mt-2 mb-1">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="bg-[#eff6ff] border-[#dbeafe] rounded-md border mx-1 mb-2">
          <WorkspaceNavChatList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter className="bg-[#eff6ff] border-[#dbeafe] rounded-md border mx-1 mb-2">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
