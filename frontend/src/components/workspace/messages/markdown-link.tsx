import type { AnchorHTMLAttributes, ComponentProps } from "react";

import {
  artifactPathFromHref,
  resolveArtifactURL,
} from "@/core/artifacts/utils";
import { getBackendBaseURL } from "@/core/config";
import { cn } from "@/lib/utils";

import { CitationLink } from "../citations/citation-link";

const SAFE_HREF_PROTOCOLS = ["http:", "https:", "mailto:", "tel:"] as const;

/** Return whether an href is safe to place in a navigable anchor. */
export function isSafeHref(href: string | undefined): boolean {
  if (typeof href !== "string" || href.length === 0) {
    return false;
  }
  if (href.startsWith("#")) {
    return true;
  }
  // Protocol-relative URLs and their backslash-normalized variants can leave
  // the current origin and must not bypass the explicit scheme allowlist.
  if (/^(\/\/|\\\\)/.test(href)) {
    return false;
  }
  try {
    const parsed = new URL(href, "https://dummy.example/");
    return (SAFE_HREF_PROTOCOLS as ReadonlyArray<string>).includes(
      parsed.protocol,
    );
  } catch {
    return false;
  }
}

/**
 * Inert stand-in for a link whose href failed `isSafeHref`. It keeps the
 * visible label and marks the omission on hover and for assistive tech, so
 * every surface that applies the allowlist degrades the same way. Extra props
 * pass through for wrappers such as Radix `asChild` triggers.
 */
export function UnsafeLink({
  href,
  className,
  ...props
}: ComponentProps<"span"> & { href: string }) {
  return (
    <span
      {...props}
      className={cn(
        "text-muted-foreground cursor-not-allowed underline decoration-dotted underline-offset-2",
        className,
      )}
      aria-label="Unsafe link omitted"
      title={`Unsafe link scheme in ${href}`}
    />
  );
}

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
  let parsed: URL;
  try {
    parsed = new URL(href);
  } catch {
    return null;
  }
  if (
    parsed.port !== "5050" ||
    !["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)
  ) {
    return null;
  }
  const prefix = `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/live-preview/5050`;
  return `${prefix}${parsed.pathname || "/"}${parsed.search}`;
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
    if (href !== undefined && !isSafeHref(href)) {
      const { className, children } = props;
      return (
        <UnsafeLink href={href} className={className}>
          {children}
        </UnsafeLink>
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
    const livePreviewURL = resolveLivePreviewURL(href, threadId);
    if (livePreviewURL) {
      const { className, target, rel, children, ...rest } = props;
      const visibleChildren =
        typeof children === "string" &&
        /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):5050(?:\/|\b)/i.test(
          children,
        )
          ? (livePreviewLabel ?? children)
          : children;
      return (
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
