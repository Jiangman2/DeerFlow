import { useMemo, type ReactNode } from "react";

import { toolWebHref } from "@/core/messages/tool-display";

import { createMarkdownLinkComponent } from "./markdown-link";

/** Keep blocked tool URLs visible as inert labels, like unsafe Markdown links. */
export function ToolResultLink({
  href,
  children,
  className,
  threadId,
  previewLabel,
}: {
  href: unknown;
  children: ReactNode;
  className?: string;
  threadId?: string;
  previewLabel?: string;
}) {
  const Link = useMemo(
    () => createMarkdownLinkComponent(threadId, undefined, previewLabel),
    [threadId, previewLabel],
  );
  const safeHref = toolWebHref(href);
  if (!safeHref) {
    return (
      <>
        <span className={className}>{children}</span>
        {typeof href === "string" && href.trim() && (
          <span
            role="img"
            aria-label="Unsafe link omitted"
            title={`Unsafe link scheme in ${href}`}
          >
            ⚠
          </span>
        )}
      </>
    );
  }
  // The shared renderer keeps Sandbox loopback URLs behind the authenticated
  // thread preview proxy and preserves the established artifact/link handling.
  return (
    <Link
      href={safeHref}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
    >
      {children}
    </Link>
  );
}
