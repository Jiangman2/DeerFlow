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
            className="text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground [&>svg]:text-current"
            asChild
          >
            <a
              href="https://ai.cnooc.com.cn/#/inferencePlatform/hyAppSquare"
              target="_blank"
              rel="noopener noreferrer"
              onClick={closeMobileSidebar}
            >
              <span aria-hidden="true" className="cnooc-sidebar-icon-tile">
                <AgentSquareIcon />
              </span>
              <span>{t.sidebar.agentSquare}</span>
            </a>
          </SidebarMenuButton>
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

function AgentSquareIcon() {
  return (
    <svg
      aria-hidden="true"
      // Match the 24-unit scale of the other sidebar icons instead of
      // shrinking the drawing inside a padded 32-unit canvas.
      viewBox="4 3 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M11 13v-3a5 5 0 0 1 10 0v3" />
      <path d="M9.5 13h13a2 2 0 0 1 2 1.8l.8 9.2a2 2 0 0 1-2 2H8.7a2 2 0 0 1-2-2l.8-9.2a2 2 0 0 1 2-1.8Z" />
      <circle cx="12.5" cy="18.5" r=".9" fill="currentColor" stroke="none" />
      <circle cx="19.5" cy="18.5" r=".9" fill="currentColor" stroke="none" />
      <path d="M13.5 22a3.5 3.5 0 0 0 5 0" />
    </svg>
  );
}
