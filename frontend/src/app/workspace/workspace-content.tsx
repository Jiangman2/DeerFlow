import { cookies } from "next/headers";
import { Toaster } from "sonner";

import { QueryClientProvider } from "@/components/query-client-provider";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { AnnouncementBanner } from "@/components/workspace/announcement-banner";
import { CommandPalette } from "@/components/workspace/command-palette";
import { GatewayOfflineBanner } from "@/components/workspace/gateway-offline-banner";
import { WorkspaceSettingsDialogProvider } from "@/components/workspace/settings/workspace-settings-dialog-provider";
import { WorkspaceSidebar } from "@/components/workspace/workspace-sidebar";

function parseSidebarOpenCookie(
  value: string | undefined,
): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export async function WorkspaceContent({
  children,
  gatewayUnavailable = false,
}: Readonly<{
  children: React.ReactNode;
  gatewayUnavailable?: boolean;
}>) {
  const cookieStore = await cookies();
  const initialSidebarOpen = parseSidebarOpenCookie(
    cookieStore.get("sidebar_state")?.value,
  );

  return (
    <QueryClientProvider>
      <WorkspaceSettingsDialogProvider>
        <SidebarProvider
          className="app-viewport app-safe-area bg-background min-h-0 overflow-hidden"
          defaultOpen={initialSidebarOpen}
        >
          <WorkspaceSidebar />
          <SidebarInset className="border-border bg-background min-h-0 min-w-0 overflow-hidden bg-[url('/images/workspace-bg-light.png')] bg-cover bg-center md:border-l dark:bg-none">
            <GatewayOfflineBanner gatewayUnavailable={gatewayUnavailable} />
            {!gatewayUnavailable && <AnnouncementBanner />}
            {children}
          </SidebarInset>
        </SidebarProvider>
        <CommandPalette />
      </WorkspaceSettingsDialogProvider>
      <Toaster
        position="top-center"
        toastOptions={{
          className: "max-w-[calc(100vw-2rem)] [overflow-wrap:anywhere]",
        }}
      />
    </QueryClientProvider>
  );
}
