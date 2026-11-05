"use client";

import { MoreHorizontalIcon, PlusSquareIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import { useIsMobile } from "@/hooks/use-mobile";

import { ArtifactTrigger } from "./artifacts";
import { BrowserTrigger } from "./browser-view";
import { ExportTrigger } from "./export-trigger";
import { SidecarTrigger } from "./sidecar";
import { ThreadScheduledTasksLink } from "./thread-scheduled-tasks-link";

export function ChatSecondaryActions({
  threadId,
  browserEnabled,
  showScheduledTasks = false,
  allowSidecar = true,
  newChatHref,
}: {
  threadId: string;
  browserEnabled: boolean;
  showScheduledTasks?: boolean;
  allowSidecar?: boolean;
  newChatHref?: string;
}) {
  const { t } = useI18n();
  const isMobile = useIsMobile();
  const actions = (
    <>
      {newChatHref &&
        (isMobile ? (
          <DropdownMenuItem asChild>
            <Link href={newChatHref} data-testid="agent-new-chat-button">
              <PlusSquareIcon />
              {t.agents.newChat}
            </Link>
          </DropdownMenuItem>
        ) : (
          <Button
            asChild
            size="sm"
            variant="secondary"
            data-testid="agent-new-chat-button"
          >
            <Link href={newChatHref}>
              <PlusSquareIcon />
              {t.agents.newChat}
            </Link>
          </Button>
        ))}
      {showScheduledTasks && (
        <ThreadScheduledTasksLink threadId={threadId} inMenu={isMobile} />
      )}
      {allowSidecar && <SidecarTrigger inMenu={isMobile} />}
      {browserEnabled && <BrowserTrigger inMenu={isMobile} />}
      <ExportTrigger threadId={threadId} inMenu={isMobile} />
      <ArtifactTrigger inMenu={isMobile} />
    </>
  );

  if (!isMobile) {
    return (
      <div className="cnooc-secondary-actions flex shrink-0 items-center gap-2">
        {actions}
      </div>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          data-testid="chat-more-trigger"
          aria-label={t.common.more}
          className="cnooc-chat-more-trigger"
          size="icon"
          variant="ghost"
        >
          <MoreHorizontalIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="cnooc-chat-more-menu min-w-52"
        sideOffset={8}
      >
        <DropdownMenuGroup>{actions}</DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
