"use client";

import {
  BotIcon,
  BookOpenIcon,
  CalendarClock,
  ExternalLinkIcon,
  MessagesSquare,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useAgentsApiEnabled } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const { enabled: agentsEnabled } = useAgentsApiEnabled();
  const [knowledgeUrl, setKnowledgeUrl] = useState<string>();

  useEffect(() => {
    let active = true;
    void fetch("/api/runtime-config", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((config: { weknoraFrontendUrl?: string | null } | null) => {
        const url = config?.weknoraFrontendUrl?.trim();
        if (active && url) setKnowledgeUrl(url);
      })
      .catch(() => {
        if (active) setKnowledgeUrl(undefined);
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <SidebarGroup className="pt-1">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname === "/workspace/chats"}
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary [&>svg]:text-current data-[active=true]:shadow-sm"
            asChild
          >
            <Link href="/workspace/chats">
              <MessagesSquare />
              <span>{t.sidebar.chats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/knowledge")}
              className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary [&>svg]:text-current data-[active=true]:shadow-sm"
              asChild
            >
              <Link href="/workspace/knowledge">
                <BookOpenIcon />
                <span>{t.sidebar.knowledge}</span>
              </Link>
            </SidebarMenuButton>
            {knowledgeUrl && (
              <SidebarMenuAction
                asChild
                className="text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                title="打开知识库平台"
              >
                <a
                  href={knowledgeUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  aria-label="打开知识库平台"
                >
                  <ExternalLinkIcon />
                </a>
              </SidebarMenuAction>
            )}
          </SidebarMenuItem>
        <SidebarMenuItem>
          {agentsEnabled ? (
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/agents")}
              className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary [&>svg]:text-current data-[active=true]:shadow-sm"
              asChild
            >
              <Link href="/workspace/agents">
                <BotIcon />
                <span>{t.sidebar.agents}</span>
              </Link>
            </SidebarMenuButton>
          ) : (
            // Disabled: aria-disabled drives the sidebar CVA to suppress
            // pointer events on the button, so wrap it in a hoverable span
            // that still surfaces the "feature not enabled" tooltip for mouse
            // users. The button stays in the tab order (no tabIndex={-1}) and
            // is wired via aria-describedby to a visually-hidden reason, so
            // keyboard and screen-reader users also learn why it is disabled.
            <Tooltip>
              <TooltipTrigger asChild>
                {/* cursor-not-allowed lives on the span (the element that
                    still receives pointer events), not the inert button. */}
                <span className="block w-full cursor-not-allowed">
                  <SidebarMenuButton
                    className="text-muted-foreground/50 [&>svg]:text-current"
                    aria-disabled
                    aria-describedby="agents-disabled-reason"
                  >
                    <BotIcon />
                    <span>{t.sidebar.agents}</span>
                  </SidebarMenuButton>
                  <span id="agents-disabled-reason" className="sr-only">
                    {t.sidebar.agentsDisabledTooltip}
                  </span>
                </span>
              </TooltipTrigger>
              <TooltipContent side="right">
                {t.sidebar.agentsDisabledTooltip}
              </TooltipContent>
            </Tooltip>
          )}
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/scheduled-tasks")}
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary [&>svg]:text-current data-[active=true]:shadow-sm"
            asChild
          >
            <Link href="/workspace/scheduled-tasks">
              <CalendarClock />
              <span>{t.sidebar.scheduledTasks}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}
