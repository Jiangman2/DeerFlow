"use client";

import {
  BotIcon,
  BookOpenIcon,
  CalendarClock,
  ExternalLinkIcon,
  MessagesSquare,
  PackageSearchIcon,
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
  useSidebar,
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
  const { isMobile, setOpenMobile } = useSidebar();
  const [knowledgeUrl, setKnowledgeUrl] = useState<string>();
  const [skillHubEnabled, setSkillHubEnabled] = useState(false);
  const resolvedKnowledgeUrl = knowledgeUrl ?? "/platform/knowledge-bases";
  const closeMobileSidebar = () => {
    if (isMobile) setOpenMobile(false);
  };

  useEffect(() => {
    let active = true;
    void fetch("/api/runtime-config", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then(
        (
          config: {
            skillHubEnabled?: boolean;
            weknoraFrontendUrl?: string | null;
          } | null,
        ) => {
          const url = config?.weknoraFrontendUrl?.trim();
          if (!active) return;
          if (url) setKnowledgeUrl(url);
          setSkillHubEnabled(config?.skillHubEnabled === true);
        },
      )
      .catch(() => {
        if (active) {
          setKnowledgeUrl(undefined);
          setSkillHubEnabled(false);
        }
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
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm [&>svg]:text-current"
            asChild
          >
            <Link href="/workspace/chats" onClick={closeMobileSidebar}>
              <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                <MessagesSquare />
              </span>
              <span>{t.sidebar.chats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/knowledge")}
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm [&>svg]:text-current"
            asChild
          >
            <Link href="/workspace/knowledge" onClick={closeMobileSidebar}>
              <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                <BookOpenIcon />
              </span>
              <span>{t.sidebar.knowledge}</span>
            </Link>
          </SidebarMenuButton>
          <SidebarMenuAction
            asChild
            className="text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground"
            title="打开知识库平台"
          >
            <a
              href={resolvedKnowledgeUrl}
              target="_blank"
              rel="noreferrer noopener"
              aria-label="打开知识库平台"
            >
              <ExternalLinkIcon />
            </a>
          </SidebarMenuAction>
        </SidebarMenuItem>
        {skillHubEnabled ? (
          <SidebarMenuItem>
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/skills")}
              className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm [&>svg]:text-current"
              asChild
            >
              <Link href="/workspace/skills" onClick={closeMobileSidebar}>
                <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                  <PackageSearchIcon />
                </span>
                <span>{t.sidebar.skillCenter}</span>
              </Link>
            </SidebarMenuButton>
            <SidebarMenuAction
              asChild
              className="text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground"
              title={t.sidebar.openSkillCenter}
            >
              <a
                href="/workspace/skills"
                target="_blank"
                rel="noreferrer noopener"
                aria-label={t.sidebar.openSkillCenter}
              >
                <ExternalLinkIcon />
              </a>
            </SidebarMenuAction>
          </SidebarMenuItem>
        ) : null}
        <SidebarMenuItem>
          {agentsEnabled ? (
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/agents")}
              className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm [&>svg]:text-current"
              asChild
            >
              <Link href="/workspace/agents" onClick={closeMobileSidebar}>
                <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                  <BotIcon />
                </span>
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
                    <span
                      aria-hidden="true"
                      className="cnooc-sidebar-icon-tile"
                    >
                      <BotIcon />
                    </span>
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
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-primary data-[active=true]:shadow-sm [&>svg]:text-current"
            asChild
          >
            <Link
              href="/workspace/scheduled-tasks"
              onClick={closeMobileSidebar}
            >
              <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                <CalendarClock />
              </span>
              <span>{t.sidebar.scheduledTasks}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarGroup>
  );
}
