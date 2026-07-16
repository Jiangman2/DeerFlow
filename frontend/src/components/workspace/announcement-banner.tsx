"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  InfoIcon,
  MegaphoneIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useActiveAnnouncement } from "@/core/announcements/hooks";
import type {
  Announcement,
  AnnouncementSeverity,
} from "@/core/announcements/types";
import { cn } from "@/lib/utils";

const dismissedPrefix = "deerflow:announcement:dismissed:";

const severityStyle: Record<AnnouncementSeverity, string> = {
  info: "border-sky-200 bg-sky-50 text-sky-950",
  success: "border-emerald-200 bg-emerald-50 text-emerald-950",
  warning: "border-amber-200 bg-amber-50 text-amber-950",
  critical: "border-rose-200 bg-rose-50 text-rose-950",
};

function SeverityIcon({ severity }: { severity: AnnouncementSeverity }) {
  if (severity === "success") return <CheckCircle2Icon className="size-4" />;
  if (severity === "warning" || severity === "critical") {
    return <AlertTriangleIcon className="size-4" />;
  }
  return <InfoIcon className="size-4" />;
}

function getDismissedKey(announcement: Announcement): string {
  return `${dismissedPrefix}${announcement.id}:${announcement.updated_at}`;
}

export function AnnouncementBanner() {
  const { data: announcement } = useActiveAnnouncement();
  const [hiddenKey, setHiddenKey] = useState<string | null>(null);

  const dismissedKey = useMemo(
    () => (announcement ? getDismissedKey(announcement) : null),
    [announcement],
  );

  useEffect(() => {
    if (!dismissedKey || typeof window === "undefined") {
      setHiddenKey(null);
      return;
    }
    setHiddenKey(
      window.localStorage.getItem(dismissedKey) ? dismissedKey : null,
    );
  }, [dismissedKey]);

  if (!announcement || (dismissedKey && hiddenKey === dismissedKey)) {
    return null;
  }

  const text = `公告：${announcement.title}：${announcement.content}`;

  return (
    <div
      className={cn(
        "border-b px-4 py-2 text-sm",
        severityStyle[announcement.severity],
      )}
      role="status"
    >
      <div className="mx-auto flex max-w-7xl items-center gap-2">
        <MegaphoneIcon className="size-4 shrink-0" />
        <SeverityIcon severity={announcement.severity} />
        <div className="min-w-0 flex-1 truncate font-medium" title={text}>
          {text}
        </div>
        {announcement.dismissible && dismissedKey ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 rounded-md hover:bg-black/5"
            onClick={() => {
              window.localStorage.setItem(dismissedKey, "1");
              setHiddenKey(dismissedKey);
            }}
            aria-label="关闭公告"
            title="关闭公告"
          >
            <XIcon className="size-4" />
          </Button>
        ) : null}
      </div>
    </div>
  );
}
