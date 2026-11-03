import { ExternalLinkIcon } from "lucide-react";
import type { ComponentProps } from "react";

import { Badge } from "@/components/ui/badge";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";

export function CitationLink({
  href,
  children,
  ...props
}: ComponentProps<"a">) {
  const resolvedHref = resolveCitationHref(href);
  const domain = extractDomain(resolvedHref ?? "");

  // Priority: children > domain
  const childrenText =
    typeof children === "string"
      ? children.replace(/^citation:\s*/i, "")
      : null;
  const isGenericText = childrenText === "Source" || childrenText === "来源";
  const displayText = (!isGenericText && childrenText) ?? domain;

  return (
    <HoverCard closeDelay={0} openDelay={0}>
      <HoverCardTrigger asChild>
        <a
          href={resolvedHref}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center"
          onClick={(e) => e.stopPropagation()}
          {...props}
        >
          <Badge
            variant="secondary"
            className="hover:bg-secondary/80 mx-0.5 cursor-pointer gap-1 rounded-full px-2 py-0.5 text-xs font-normal"
          >
            {displayText}
            <ExternalLinkIcon className="size-3" />
          </Badge>
        </a>
      </HoverCardTrigger>
      <HoverCardContent className={cn("relative w-80 p-0", props.className)}>
        <div className="p-3">
          <div className="space-y-1">
            {displayText && (
              <h4 className="truncate text-sm leading-tight font-medium">
                {displayText}
              </h4>
            )}
            {resolvedHref && (
              <p className="text-muted-foreground truncate text-xs break-all">
                {resolvedHref}
              </p>
            )}
          </div>
          <a
            href={resolvedHref}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary mt-2 inline-flex items-center gap-1 text-xs hover:underline"
          >
            Visit source
            <ExternalLinkIcon className="size-3" />
          </a>
        </div>
      </HoverCardContent>
    </HoverCard>
  );
}

export function resolveCitationHref(href: string | undefined) {
  if (!href) {
    return href;
  }
  const match = /^\/mnt\/user-data\/knowledge_bases\/([^/]+)(?:\/|$)/.exec(
    href,
  );
  if (!match?.[1]) {
    return href;
  }
  return `/platform/knowledge-bases/${encodeURIComponent(match[1])}`;
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    return url;
  }
}
