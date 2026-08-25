"use client";

import {
  ChevronsUpDown,
  InfoIcon,
  LayoutDashboardIcon,
  Settings2Icon,
  SettingsIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";

import { useWorkspaceSettingsDialog } from "./settings/workspace-settings-dialog-provider";

function NavMenuButtonContent({
  isSidebarOpen,
  t,
}: {
  isSidebarOpen: boolean;
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <>
      <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
        <SettingsIcon className="cnooc-settings-icon" />
      </span>
      <span className="cnooc-settings-label">
        {isSidebarOpen ? t.workspace.settingsAndMore : t.common.settings}
      </span>
      {isSidebarOpen ? (
        <ChevronsUpDown className="cnooc-settings-chevron ml-auto" />
      ) : null}
    </>
  );
}

export function WorkspaceNavMenu() {
  const [mounted, setMounted] = useState(false);
  const { open: isSidebarOpen, isMobile, setOpenMobile } = useSidebar();
  const { openSettings } = useWorkspaceSettingsDialog();
  const { user } = useAuth();
  const { t } = useI18n();
  const isAdmin = user?.system_role === "admin";

  useEffect(() => {
    setMounted(true);
  }, []);

  const showSettings = (section: "account" | "about") => {
    if (!isMobile) {
      openSettings(section);
      return;
    }

    setOpenMobile(false);
    // The mobile sidebar closes with a 300 ms Sheet transition. Opening the
    // settings portal after that boundary avoids two modal overlays briefly
    // occupying the same viewport on older enterprise browsers. The provider
    // sits outside the Sheet, so this deferred open survives Sidebar unmount.
    openSettings(section, 320);
  };

  return (
    <>
      <SidebarMenu className="w-full">
        <SidebarMenuItem>
          {mounted ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton
                  size="lg"
                  className="hover:bg-sidebar-accent data-[state=open]:bg-sidebar-accent data-[state=open]:text-primary data-[state=open]:shadow-sm"
                >
                  <NavMenuButtonContent isSidebarOpen={isSidebarOpen} t={t} />
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                className="cnooc-settings-menu"
                data-collapsed={!isSidebarOpen}
                data-mobile={isMobile || undefined}
                align={isMobile ? "start" : "end"}
                side={isMobile ? "top" : isSidebarOpen ? "top" : "right"}
                sideOffset={isMobile ? 12 : 8}
                collisionPadding={isMobile ? 16 : 8}
              >
                <DropdownMenuGroup>
                  {isAdmin && (
                    <>
                      <DropdownMenuItem asChild>
                        <Link
                          href="/workspace/admin/dashboard"
                          onClick={() => {
                            if (isMobile) setOpenMobile(false);
                          }}
                        >
                          <LayoutDashboardIcon />
                          看板
                        </Link>
                      </DropdownMenuItem>
                    </>
                  )}
                  <DropdownMenuItem onClick={() => showSettings("account")}>
                    <Settings2Icon />
                    {t.common.settings}
                  </DropdownMenuItem>
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => showSettings("about")}>
                  <InfoIcon />
                  {t.workspace.about}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <SidebarMenuButton
              size="lg"
              className="hover:bg-sidebar-accent pointer-events-none"
            >
              <NavMenuButtonContent isSidebarOpen={isSidebarOpen} t={t} />
            </SidebarMenuButton>
          )}
        </SidebarMenuItem>
      </SidebarMenu>
    </>
  );
}
