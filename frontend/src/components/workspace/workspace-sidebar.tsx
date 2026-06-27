"use client";

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
import { SiteNavigationList } from "./site-navigation-list";
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
        className="border-r border-[#d4dfef] bg-[linear-gradient(180deg,#eef5ff_0%,#e8f0fb_48%,#e3ecf8_100%)] text-[#4c5664]"
        {...props}
      >
        <SidebarHeader className="mx-1 rounded-md border border-transparent bg-transparent py-0">
          <WorkspaceHeader />
        </SidebarHeader>
        <SidebarContent className="mx-1 rounded-md border border-transparent bg-transparent">
          <WorkspaceNavChatList />
          <WorkspaceChannelsList />
          <SiteNavigationList />
          {isSidebarOpen && <RecentChatList />}
        </SidebarContent>
        <SidebarFooter className="mx-1 rounded-md border border-transparent bg-transparent">
          <WorkspaceNavMenu />
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </>
  );
}
