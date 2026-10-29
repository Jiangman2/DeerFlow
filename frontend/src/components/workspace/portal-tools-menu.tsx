"use client";

import { Grid2X2Icon } from "lucide-react";
import type { ComponentProps } from "react";

import { Suggestion } from "@/components/ai-elements/suggestion";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/core/i18n/hooks";
import { PORTAL_MORE_TOOLS, type PortalTool } from "@/core/portal-links";

import { PortalToolIcon } from "./portal-tool-icon";

export function PortalArtwork({
  id,
}: {
  id: ComponentProps<typeof PortalToolIcon>["id"];
}) {
  return (
    <span
      className="cnooc-action-artwork cnooc-portal-artwork"
      aria-hidden="true"
    >
      <PortalToolIcon id={id} />
    </span>
  );
}

export function PortalQuickAction({ tool }: { tool: PortalTool }) {
  const { t } = useI18n();
  return (
    <Button
      asChild
      variant="outline"
      size="sm"
      data-slot="suggestion"
      className="text-muted-foreground dark:bg-background h-auto max-w-full cursor-pointer rounded-full px-4 py-2 text-center text-xs font-normal whitespace-nowrap"
    >
      <a
        href={tool.href}
        target="_blank"
        rel="noopener noreferrer"
        title={t.common.openInNewWindow}
        data-testid={`portal-tool-${tool.id}`}
      >
        <PortalArtwork id={tool.id} />
        <span>{t.portalTools[tool.id]}</span>
        <span className="sr-only">{t.common.openInNewWindow}</span>
      </a>
    </Button>
  );
}

export function PortalToolMenuItem({ tool }: { tool: PortalTool }) {
  const { t } = useI18n();
  return (
    <DropdownMenuItem asChild className="cnooc-portal-menu-item">
      <a
        href={tool.href}
        target="_blank"
        rel="noopener noreferrer"
        title={t.common.openInNewWindow}
        data-testid={`portal-tool-${tool.id}`}
      >
        <span className="cnooc-portal-menu-icon" aria-hidden="true">
          <PortalArtwork id={tool.id} />
        </span>
        <span className="cnooc-portal-menu-label">
          {t.portalTools[tool.id]}
        </span>
        <span className="sr-only">{t.common.openInNewWindow}</span>
      </a>
    </DropdownMenuItem>
  );
}

export function PortalToolsMenu() {
  const { t } = useI18n();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Suggestion
          className="whitespace-nowrap"
          data-testid="portal-tools-trigger"
          icon={Grid2X2Icon}
          suggestion={t.common.more}
        >
          <PortalArtwork id="more" />
          <span>{t.common.more}</span>
        </Suggestion>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="center"
        side="top"
        sideOffset={8}
        collisionPadding={12}
        className="cnooc-portal-menu cnooc-portal-menu-compact"
        data-testid="portal-tools-menu"
      >
        <DropdownMenuGroup>
          <DropdownMenuLabel className="cnooc-portal-menu-title">
            {t.portalTools.title}
          </DropdownMenuLabel>
          <DropdownMenuGroup className="cnooc-portal-menu-list">
            {PORTAL_MORE_TOOLS.map((tool) => (
              <PortalToolMenuItem key={tool.id} tool={tool} />
            ))}
          </DropdownMenuGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
