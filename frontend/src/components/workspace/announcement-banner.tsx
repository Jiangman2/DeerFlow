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

const severityStyles: Record<
  AnnouncementSeverity,
  { shell: string; accent: string; icon: string; label: string }
> = {
  info: {
    shell:
      "border-sky-300/70 bg-gradient-to-r from-sky-100 via-cyan-50 to-blue-100 text-sky-950 dark:border-sky-700/70 dark:from-sky-950 dark:via-cyan-950 dark:to-blue-950 dark:text-sky-50",
    accent: "bg-sky-500",
    icon: "bg-sky-600 text-white shadow-sky-500/40",
    label: "系统公告",
  },
  success: {
    shell:
      "border-emerald-300/70 bg-gradient-to-r from-emerald-100 via-teal-50 to-green-100 text-emerald-950 dark:border-emerald-700/70 dark:from-emerald-950 dark:via-teal-950 dark:to-green-950 dark:text-emerald-50",
    accent: "bg-emerald-500",
    icon: "bg-emerald-600 text-white shadow-emerald-500/40",
    label: "好消息",
  },
  warning: {
    shell:
      "border-amber-300/80 bg-gradient-to-r from-amber-100 via-orange-50 to-yellow-100 text-amber-950 dark:border-amber-700/70 dark:from-amber-950 dark:via-orange-950 dark:to-yellow-950 dark:text-amber-50",
    accent: "bg-amber-500",
    icon: "bg-amber-500 text-white shadow-amber-500/40",
    label: "重要提醒",
  },
  critical: {
    shell:
      "border-rose-300/80 bg-gradient-to-r from-rose-100 via-red-50 to-orange-100 text-rose-950 dark:border-rose-700/70 dark:from-rose-950 dark:via-red-950 dark:to-orange-950 dark:text-rose-50",
    accent: "bg-rose-500",
    icon: "bg-rose-600 text-white shadow-rose-500/40",
    label: "紧急公告",
  },
};

function SeverityIcon({ severity }: { severity: AnnouncementSeverity }) {
  if (severity === "success") return <CheckCircle2Icon className="size-4" />;
  if (severity === "warning" || severity === "critical") {
    return <AlertTriangleIcon className="size-4" />;
  }
  return <InfoIcon className="size-4" />;
}

export function getAnnouncementDismissedKey(announcement: Announcement) {
  return `${dismissedPrefix}${announcement.id}:${announcement.updated_at}`;
}

export function AnnouncementBanner() {
  const { data: announcement } = useActiveAnnouncement();
  const [hiddenKey, setHiddenKey] = useState<string | null>(null);
  const [checkedKey, setCheckedKey] = useState<string | null>();

  const dismissedKey = useMemo(
    () => (announcement ? getAnnouncementDismissedKey(announcement) : null),
    [announcement],
  );

  useEffect(() => {
    if (!dismissedKey) {
      setHiddenKey(null);
      setCheckedKey(null);
      return;
    }
    setHiddenKey(
      window.localStorage.getItem(dismissedKey) ? dismissedKey : null,
    );
    setCheckedKey(dismissedKey);
  }, [dismissedKey]);

  if (
    !announcement ||
    checkedKey !== dismissedKey ||
    (dismissedKey && hiddenKey === dismissedKey)
  ) {
    return null;
  }

  const style = severityStyles[announcement.severity];
  const urgent = announcement.severity === "critical";

  return (
    <aside
      className="animate-in fade-in slide-in-from-top-3 relative z-30 px-3 pt-3 duration-500 motion-reduce:animate-none sm:px-5"
      role={urgent ? "alert" : "status"}
      aria-live={urgent ? "assertive" : "polite"}
    >
      <div
        className={cn(
          "relative mx-auto max-w-7xl overflow-hidden rounded-2xl border shadow-lg shadow-black/8",
          style.shell,
        )}
      >
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 skew-x-[-20deg] bg-white/35 blur-md motion-safe:animate-[announcement-shine_4s_ease-in-out_2] dark:bg-white/8"
        />
        <div className={cn("absolute inset-y-0 left-0 w-1", style.accent)} />
        <div className="relative flex items-start gap-3 px-4 py-3.5 sm:items-center sm:px-5">
          <div className="relative mt-0.5 shrink-0 sm:mt-0">
            <span
              aria-hidden="true"
              className={cn(
                "absolute inset-0 rounded-xl opacity-40 blur-sm motion-safe:animate-[ping_1.2s_ease-out_3]",
                style.accent,
              )}
            />
            <span
              className={cn(
                "relative grid size-10 place-items-center rounded-xl shadow-lg",
                style.icon,
              )}
            >
              <MegaphoneIcon className="size-5 motion-safe:animate-[wiggle_2.4s_ease-in-out_3]" />
            </span>
          </div>

          <div className="min-w-0 flex-1">
            <div className="mb-0.5 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-full bg-white/65 px-2 py-0.5 text-[11px] font-bold tracking-wide dark:bg-black/20">
                <SeverityIcon severity={announcement.severity} />
                {style.label}
              </span>
              <h2 className="text-sm font-bold sm:text-base">
                {announcement.title}
              </h2>
            </div>
            <p className="text-xs leading-relaxed whitespace-pre-wrap opacity-85 sm:text-sm">
              {announcement.content}
            </p>
          </div>

          {announcement.dismissible && dismissedKey ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 rounded-full bg-white/30 hover:bg-white/65 dark:bg-black/10 dark:hover:bg-black/25"
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
    </aside>
  );
}
