"use client";

import { Grid2X2Icon } from "lucide-react";
import Image from "next/image";

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

function PortalArtwork({ src, scale }: { src: string; scale: number }) {
  return (
    <span
      className="cnooc-action-artwork cnooc-portal-artwork"
      aria-hidden="true"
    >
      <Image
        src={src}
        width={48}
        height={48}
        alt=""
        className="size-full object-contain"
        style={{ transform: `scale(${scale})` }}
      />
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
        data-testid={`portal-tool-${tool.id}`}
      >
        <PortalArtwork src={tool.artwork} scale={tool.artworkScale} />
        <span>{t.portalTools[tool.id]}</span>
        <span className="sr-only">{t.common.openInNewWindow}</span>
      </a>
    </Button>
  );
}

export function PortalToolMenuItem({ tool }: { tool: PortalTool }) {
  const { t } = useI18n();
  return (
    <DropdownMenuItem
      asChild
      className="min-h-12 min-w-0 gap-1 rounded-lg px-1 py-0.5"
    >
      <a
        href={tool.href}
        target="_blank"
        rel="noopener noreferrer"
        data-testid={`portal-tool-${tool.id}`}
      >
        <PortalArtwork src={tool.artwork} scale={tool.artworkScale} />
        <span className="min-w-0 text-xs leading-4 whitespace-normal">
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
          <PortalArtwork src="/images/portal-more.png" scale={709 / 924} />
          <span>{t.common.more}</span>
        </Suggestion>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="top"
        sideOffset={8}
        collisionPadding={12}
        className="cnooc-portal-menu w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl p-1.5"
        data-testid="portal-tools-menu"
      >
        <DropdownMenuGroup>
          <DropdownMenuLabel className="px-2 py-1 text-xs font-medium">
            {t.portalTools.title}
          </DropdownMenuLabel>
          <DropdownMenuGroup className="grid grid-cols-2 gap-x-1 gap-y-0.5">
            {PORTAL_MORE_TOOLS.map((tool) => (
              <PortalToolMenuItem key={tool.id} tool={tool} />
            ))}
          </DropdownMenuGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
