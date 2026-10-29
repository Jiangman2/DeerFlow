"use client";

import Link from "next/link";
import { useState } from "react";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export function WorkspaceHeader({ className }: { className?: string }) {
  const { state, isMobile } = useSidebar();
  const [suppressCollapsedHover, setSuppressCollapsedHover] = useState(false);
  const releasePointerFocus = (event: React.MouseEvent<HTMLButtonElement>) => {
    // After a pointer-triggered collapse the control moves into the collapsed
    // brand slot. Releasing its focus immediately restores the logo at rest;
    // keyboard-triggered toggles keep focus for accessible navigation.
    if (event.detail > 0) {
      if (state === "expanded") {
        setSuppressCollapsedHover(true);
      }
      const target = event.currentTarget;
      target.blur();
      requestAnimationFrame(() => target.blur());
    }
  };
  return (
    <>
      <div
        className={cn(
          "group/workspace-header flex h-12 flex-col justify-center",
          className,
        )}
      >
        {state === "collapsed" && !isMobile ? (
          <div
            className="cnooc-collapsed-brand-slot group-has-data-[collapsible=icon]/sidebar-wrapper:-translate-y flex w-full cursor-pointer items-center justify-center"
            data-suppress-hover={suppressCollapsedHover || undefined}
            onPointerLeave={() => setSuppressCollapsedHover(false)}
          >
            <BrandLogo
              variant="mark"
              priority
              className="cnooc-collapsed-brand-mark block size-7 -translate-y-px object-contain"
            />
            <SidebarTrigger
              className="cnooc-collapsed-brand-trigger"
              onClick={releasePointerFocus}
            />
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2">
            {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
              <Link href={brand.links.home} className="ml-2 flex items-center">
                <BrandLogo variant="full" priority className="h-8 w-auto" />
              </Link>
            ) : (
              <div className="ml-2 flex cursor-default items-center">
                <BrandLogo variant="full" priority className="h-8 w-auto" />
              </div>
            )}
            <SidebarTrigger onClick={releasePointerFocus} />
          </div>
        )}
      </div>
    </>
  );
}
