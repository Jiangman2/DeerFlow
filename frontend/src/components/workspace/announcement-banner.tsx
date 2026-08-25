"use client";

import { BellIcon, XIcon } from "lucide-react";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useActiveAnnouncement } from "@/core/announcements/hooks";
import type { Announcement } from "@/core/announcements/types";

const dismissedPrefix = "deerflow:announcement:dismissed:";
export const ANNOUNCEMENT_OPEN_EVENT = "deerflow:announcement:open";

export function getAnnouncementDismissedKey(announcement: Announcement) {
  return `${dismissedPrefix}${announcement.id}:${announcement.updated_at}`;
}

export function AnnouncementBanner() {
  const { data: announcement } = useActiveAnnouncement();
  const [hiddenKey, setHiddenKey] = useState<string | null>(null);
  const [checkedKey, setCheckedKey] = useState<string | null>();
  const [forcedOpen, setForcedOpen] = useState(false);

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

  useEffect(() => {
    const reopenAnnouncement = () => {
      setForcedOpen(true);
      if (!dismissedKey) return;
      setHiddenKey(null);
      setCheckedKey(dismissedKey);
    };
    window.addEventListener(ANNOUNCEMENT_OPEN_EVENT, reopenAnnouncement);
    return () => {
      window.removeEventListener(ANNOUNCEMENT_OPEN_EVENT, reopenAnnouncement);
    };
  }, [dismissedKey]);

  const checked = !announcement || checkedKey === dismissedKey;
  const dismissed = Boolean(dismissedKey && hiddenKey === dismissedKey);
  if (!forcedOpen && (!announcement || !checked || dismissed)) {
    return null;
  }

  const canDismiss = !announcement || announcement.dismissible;
  const dialogTitle = "中海油勘探开发通用智能体公告";
  const dialogContent = announcement?.content ?? "当前暂无公告。";

  const closeAnnouncement = () => {
    if (announcement?.dismissible && dismissedKey) {
      window.localStorage.setItem(dismissedKey, "1");
      setHiddenKey(dismissedKey);
    }
    setForcedOpen(false);
  };

  return (
    <aside
      className="cnooc-announcement-overlay animate-in fade-in fixed inset-0 z-50 grid place-items-center duration-300 motion-reduce:animate-none"
      data-modal="true"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cnooc-announcement-title"
    >
      <div aria-hidden="true" className="cnooc-announcement-backdrop" />
      <div
        className="cnooc-announcement-dialog relative w-full overflow-hidden border"
        data-severity={announcement?.severity ?? "info"}
      >
        <Image
          alt=""
          aria-hidden="true"
          className="cnooc-announcement-orbit"
          height={256}
          priority
          src="/images/cnooc-announcement-orbit.png"
          unoptimized
          width={256}
        />
        <header className="cnooc-announcement-header">
          <h2 id="cnooc-announcement-title">{dialogTitle}</h2>
          {canDismiss ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="cnooc-announcement-close-icon"
              onClick={closeAnnouncement}
              aria-label="关闭公告"
              title="关闭公告"
            >
              <XIcon className="size-4" />
            </Button>
          ) : null}
        </header>
        <div className="cnooc-announcement-body">
          <div className="cnooc-announcement-content whitespace-pre-wrap">
            {dialogContent}
          </div>
          {canDismiss ? (
            <div className="cnooc-announcement-footer">
              <Button
                type="button"
                className="cnooc-announcement-close-button"
                onClick={closeAnnouncement}
              >
                关闭
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

export function AnnouncementNotificationButton() {
  const { data: announcement } = useActiveAnnouncement();

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="cnooc-announcement-trigger relative"
      data-severity={announcement?.severity ?? "info"}
      aria-label={announcement ? "查看当前公告" : "暂无公告"}
      title={announcement ? "查看当前公告" : "暂无公告"}
      onClick={() => {
        window.dispatchEvent(new Event(ANNOUNCEMENT_OPEN_EVENT));
      }}
    >
      <BellIcon aria-hidden="true" />
      {announcement ? (
        <span aria-hidden="true" className="cnooc-announcement-dot" />
      ) : null}
    </Button>
  );
}
