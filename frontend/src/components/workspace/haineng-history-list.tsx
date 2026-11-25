"use client";

import { MessageCircle, Plus } from "lucide-react";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useHainengHistory } from "@/core/haineng/history";

export function HainengHistoryList() {
  const history = useHainengHistory();
  const { isMobile, setOpenMobile } = useSidebar();
  const records = history.records.filter((r) => r.messages.length > 0);
  return (
    <SidebarGroup>
      <SidebarGroupLabel>最近对话</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              disabled={history.busy}
              onClick={() => {
                history.create();
                if (isMobile) setOpenMobile(false);
              }}
            >
              <Plus />
              <span>新建对话</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          {records.map((record) => (
            <SidebarMenuItem key={record.id}>
              <SidebarMenuButton
                disabled={history.busy}
                isActive={history.activeId === record.id}
                tooltip={`对话·${record.title}`}
                onClick={() => {
                  history.open(record.id);
                  if (isMobile) setOpenMobile(false);
                }}
              >
                <MessageCircle />
                <span className="truncate">对话·{record.title}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
          {records.length === 0 && (
            <li className="text-muted-foreground px-3 py-2 text-xs">
              暂无对话记录
            </li>
          )}
        </SidebarMenu>
        {history.storageError && (
          <p role="alert" className="text-destructive px-3 text-xs">
            {history.storageError}
          </p>
        )}
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
