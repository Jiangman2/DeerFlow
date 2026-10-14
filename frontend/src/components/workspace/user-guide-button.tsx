"use client";

import { BookOpenIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import { USER_GUIDE_URL } from "@/core/portal-links";
import { cn } from "@/lib/utils";

export function UserGuideButton({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <Button
      asChild
      variant="ghost"
      size="icon"
      className={cn("shrink-0", className)}
    >
      <a
        href={USER_GUIDE_URL}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={t.portalTools.userGuide}
        title={t.portalTools.userGuide}
        data-testid="user-guide-link"
      >
        <BookOpenIcon aria-hidden="true" />
      </a>
    </Button>
  );
}
