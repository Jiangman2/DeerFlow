"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { Toaster } from "sonner";

import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";
import { getLocalSettings, useLocalSettings } from "@/core/settings";
import { WorkspaceUserProvider, type WorkspaceUser } from "@/core/user/context";

const queryClient = new QueryClient();

export default function WorkspaceLayoutClient({
  children,
  user,
}: Readonly<{ children: React.ReactNode; user: WorkspaceUser }>) {
  const [settings, setSettings] = useLocalSettings();
  const [open, setOpen] = useState(false);
  useLayoutEffect(() => {
    setOpen(!getLocalSettings().layout.sidebar_collapsed);
  }, []);
  useEffect(() => {
    setOpen(!settings.layout.sidebar_collapsed);
  }, [settings.layout.sidebar_collapsed]);
  const handleOpenChange = useCallback(
    (open: boolean) => {
      setOpen(open);
      setSettings("layout", { sidebar_collapsed: !open });
    },
    [setSettings],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <WorkspaceUserProvider value={user}>
        <SidebarProvider
          className="h-screen bg-background bg-[url('/images/workspace-bg-light.png')] bg-cover bg-center bg-no-repeat dark:bg-background"
          open={open}
          onOpenChange={handleOpenChange}
        >
          <WorkspaceSidebar />
          <SidebarInset className="min-w-0 bg-background bg-[url('/images/workspace-bg-light.png')] bg-cover bg-center bg-no-repeat dark:bg-background">
            {children}
          </SidebarInset>
        </SidebarProvider>
      </WorkspaceUserProvider>
      <Toaster position="top-center" />
    </QueryClientProvider>
  );
}
