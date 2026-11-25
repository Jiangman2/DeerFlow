"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { Sidebar, SidebarContent } from "@/components/ui/sidebar";
import { HainengChat } from "@/components/workspace/haineng-chat";
import { HainengHistoryList } from "@/components/workspace/haineng-history-list";
import { PlatformSwitcher } from "@/components/workspace/platform-switcher";
import { AUTH_DISABLED_USER } from "@/core/auth/auth-disabled-user";
import { AuthProvider } from "@/core/auth/AuthProvider";
import { HainengHistoryProvider } from "@/core/haineng/history";
export function ChatPreview() {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <AuthProvider initialUser={AUTH_DISABLED_USER}>
        <HainengHistoryProvider preview>
          <SidebarProvider className="app-viewport cnooc-workspace-shell bg-background min-h-0 overflow-hidden">
            <Sidebar>
              <SidebarContent>
                <HainengHistoryList />
              </SidebarContent>
            </Sidebar>
            <SidebarInset className="cnooc-workspace-canvas bg-background min-h-0 min-w-0 overflow-hidden">
              <div className="text-muted-foreground px-4 py-1 text-xs">
                仅前端预览 · A/B 均使用本地模拟回复
              </div>
              <PlatformSwitcher preview>
                <HainengHistoryProvider preview namespace="work">
                  <HainengChat preview />
                </HainengHistoryProvider>
              </PlatformSwitcher>
            </SidebarInset>
          </SidebarProvider>
        </HainengHistoryProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
