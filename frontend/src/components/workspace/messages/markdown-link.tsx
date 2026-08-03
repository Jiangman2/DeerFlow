import type { AnchorHTMLAttributes } from "react";

import {
  artifactPathFromHref,
  resolveArtifactURL,
} from "@/core/artifacts/utils";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";

function isExternalUrl(href: string | undefined): boolean {
  return !!href && /^https?:\/\//.test(href);
}

/**
 * Builds the `a` renderer shared by message content and generic markdown.
 * Passing a `threadId` also resolves `/mnt/` artifact links; without it those
 * links fall through to the default external-link handling.
 */
export function createMarkdownLinkComponent(
  threadId?: string,
  onArtifactClick?: (filepath: string) => void,
) {
  return function MarkdownLink({
    href,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement>) {
    if (typeof props.children === "string") {
      const match = /^citation:(.+)$/.exec(props.children);
      if (match) {
        const [, text] = match;
        return (
          <CitationLink {...props} href={href}>
            {text}
          </CitationLink>
        );
      }
    }
    const artifactPath = threadId
      ? artifactPathFromHref(href, threadId)
      : null;
    if (threadId && artifactPath) {
      const artifactURL = resolveArtifactURL(artifactPath, threadId);
      return (
        <a
          {...props}
          // Message artifact links are preview controls, not document
          // navigations. Keeping the raw file URL in href lets a very early
          // click (before React hydration) replace the whole chat page with a
          // binary/download response. The real URL remains available to the
          // artifact panel through the selected path and as inert metadata.
          href={onArtifactClick ? "#artifacts" : artifactURL}
          data-artifact-href={artifactURL}
          onClick={(event) => {
            if (!onArtifactClick) {
              return;
            }
            event.preventDefault();
            event.stopPropagation();
            onArtifactClick(artifactPath);
          }}
        />
      );
    }
    const { className, target, rel, ...rest } = props;
    const external = isExternalUrl(href);
    return (
      <a
        {...rest}
        href={href}
        className={cn(
          "text-primary decoration-primary/30 hover:decoration-primary/60 underline underline-offset-2 transition-colors",
          className,
        )}
        target={target ?? (external ? "_blank" : undefined)}
        rel={rel ?? (external ? "noopener noreferrer" : undefined)}
      />
    );
  };
}
