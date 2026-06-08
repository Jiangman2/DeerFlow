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
        className="border-r border-[#bfdbfe] bg-[#eff6ff]"
        {...props}
      >
        <SidebarHeader className="mx-1 rounded-md border border-[#dbeafe] bg-[#eff6ff] py-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="mx-1 rounded-md border border-[#dbeafe] bg-[#eff6ff]">
          <WorkspaceNavChatList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter className="mx-1 rounded-md border border-[#dbeafe] bg-[#eff6ff]">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
