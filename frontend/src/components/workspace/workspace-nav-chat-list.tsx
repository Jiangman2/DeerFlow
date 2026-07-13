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

interface RuntimeConfig {
  weknoraFrontendUrl?: string | null;
}

export function WorkspaceNavChatList() {
  const { t } = useI18n();
  const pathname = usePathname();
  const [weknoraFrontendUrl, setWeKnoraFrontendUrl] = useState<string>();
  const { enabled: agentsEnabled } = useAgentsApiEnabled();

  useEffect(() => {
    let active = true;
    void fetch("/api/runtime-config", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((config: RuntimeConfig | null) => {
        const url = config?.weknoraFrontendUrl?.trim();
        if (active && url) {
          setWeKnoraFrontendUrl(url);
        }
      })
      .catch(() => {
        if (active) {
          setWeKnoraFrontendUrl(undefined);
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
            className="text-[#4f5967] hover:bg-white/80 hover:text-[#222832] data-[active=true]:bg-white data-[active=true]:text-[#222832] data-[active=true]:shadow-sm [&>svg]:text-[#8b939d]"
            asChild
          >
            <Link href="/workspace/chats">
              <MessagesSquare />
              <span>{t.sidebar.chats}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          {agentsEnabled ? (
            <SidebarMenuButton
              isActive={pathname.startsWith("/workspace/agents")}
              className="text-[#4f5967] hover:bg-white/80 hover:text-[#222832] data-[active=true]:bg-white data-[active=true]:text-[#222832] data-[active=true]:shadow-sm [&>svg]:text-[#8b939d]"
              asChild
            >
              <Link href="/workspace/agents">
                <BotIcon />
                <span>{t.sidebar.agents}</span>
              </Link>
            </SidebarMenuButton>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="block w-full cursor-not-allowed">
                  <SidebarMenuButton
                    className="text-[#4f5967]/50 [&>svg]:text-[#8b939d]/50"
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
            isActive={pathname.startsWith("/workspace/knowledge")}
            className="text-[#4f5967] hover:bg-white/80 hover:text-[#222832] data-[active=true]:bg-white data-[active=true]:text-[#222832] data-[active=true]:shadow-sm [&>svg]:text-[#8b939d]"
            asChild
          >
            <Link href="/workspace/knowledge">
              <BookOpenIcon />
              <span>知识库</span>
            </Link>
          </SidebarMenuButton>
          {weknoraFrontendUrl && (
            <SidebarMenuAction
              asChild
              className="text-[#8b939d] hover:bg-white/90 hover:text-[#222832]"
              title="打开 WeKnora 前端"
            >
              <a
                href={weknoraFrontendUrl}
                target="_blank"
                rel="noreferrer noopener"
                aria-label="打开 WeKnora 前端"
              >
                <ExternalLinkIcon />
              </a>
            </SidebarMenuAction>
          )}
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton
            isActive={pathname.startsWith("/workspace/scheduled-tasks")}
            className="text-[#4f5967] hover:bg-white/80 hover:text-[#222832] data-[active=true]:bg-white data-[active=true]:text-[#222832] data-[active=true]:shadow-sm [&>svg]:text-[#8b939d]"
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
