import type { AnchorHTMLAttributes } from "react";

import {
  isSandboxInternalURL,
  parseLivePreviewURL,
  previewTrailingProse,
} from "@/core/artifacts/live-preview";
import {
  artifactPathFromHref,
  resolveArtifactURL,
} from "@/core/artifacts/utils";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";

function isExternalUrl(href: string | undefined): boolean {
  return !!href && /^https?:\/\//.test(href);
}

/**
 * Sandbox processes report their own loopback URL (usually 127.0.0.1:5050).
 * That address is meaningful only inside the Sandbox Pod, so message links
 * must use the authenticated Gateway proxy when they are rendered in a chat.
 */
export function resolveLivePreviewURL(
  href: string | undefined,
  threadId: string | undefined,
): string | null {
  if (!href || !threadId) {
    return null;
  }
  const parsed = parseLivePreviewURL(href);
  if (!parsed) return null;
  const prefix = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/live-preview/${parsed.port}`;
  return `${prefix}${parsed.pathname || "/"}${parsed.search}${parsed.hash}`;
}

/**
 * Builds the `a` renderer shared by message content and generic markdown.
 * Passing a `threadId` also resolves `/mnt/` artifact links; without it those
 * links fall through to the default external-link handling.
 */
export function createMarkdownLinkComponent(
  threadId?: string,
  onArtifactClick?: (filepath: string) => void,
  livePreviewLabel?: string,
) {
  return function MarkdownLink({
    href,
    ...props
  }: AnchorHTMLAttributes<HTMLAnchorElement>) {
    const livePreviewURL = resolveLivePreviewURL(href, threadId);
    if (!livePreviewURL && isSandboxInternalURL(href)) {
      return (
        <span
          className="text-muted-foreground cursor-not-allowed"
          aria-disabled="true"
        >
          {typeof props.children === "string" &&
          isSandboxInternalURL(props.children)
            ? (livePreviewLabel ?? "预览不可用")
            : props.children}
        </span>
      );
    }
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
    const artifactPath = threadId ? artifactPathFromHref(href, threadId) : null;
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
    if (livePreviewURL) {
      const { className, target, rel, children, ...rest } = props;
      const trailingProse =
        typeof children === "string"
          ? previewTrailingProse(children)
          : undefined;
      const visibleChildren =
        typeof children === "string" && parseLivePreviewURL(children)
          ? (livePreviewLabel ?? "预览")
          : children;
      const previewLink = (
        <a
          {...rest}
          href={livePreviewURL}
          className={cn(
            "text-primary decoration-primary/30 hover:decoration-primary/60 underline underline-offset-2 transition-colors",
            className,
          )}
          target={target ?? "_blank"}
          rel={rel ?? "noopener noreferrer"}
        >
          {visibleChildren}
        </a>
      );
      return trailingProse ? (
        <>
          {previewLink}
          {trailingProse}
        </>
      ) : (
        previewLink
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
