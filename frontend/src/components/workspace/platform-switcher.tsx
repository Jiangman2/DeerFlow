"use client";

import { useState, type ReactNode } from "react";

import { useI18n } from "@/core/i18n/hooks";

const HAINENG_URL = "https://heq.cnooc.com.cn/#/home";

/** Keep both platforms mounted: switching must not reset a running chat. */
export function PlatformSwitcher({ children }: { children: ReactNode }) {
  const { locale } = useI18n();
  const chinese = locale === "zh-CN";
  const [active, setActive] = useState<"a" | "b">("a");
  const [hasOpenedB, setHasOpenedB] = useState(false);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <nav
        aria-label={chinese ? "平台切换" : "Platform switcher"}
        className="border-border flex shrink-0 items-center gap-2 border-b px-4 py-2"
      >
        {(["a", "b"] as const).map((platform) => (
          <button
            key={platform}
            type="button"
            aria-pressed={active === platform}
            aria-controls={`platform-${platform}-panel`}
            data-testid={`platform-${platform}-switch`}
            onClick={() => {
              if (platform === "b") setHasOpenedB(true);
              setActive(platform);
            }}
            className="border-border text-foreground hover:bg-accent min-h-11 rounded-md border px-4 py-2 text-sm font-medium aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {platform === "a"
              ? "A · DeerFlow"
              : chinese ? "B · 海能 AI" : "B · Haineng AI"}
          </button>
        ))}
      </nav>
      <div
        id="platform-a-panel"
        data-testid="platform-a-panel"
        hidden={active !== "a"}
        className="min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
        style={{ display: active === "a" ? "flex" : "none" }}
      >
        {children}
      </div>
      {hasOpenedB && (
        <div
          id="platform-b-panel"
          data-testid="platform-b-panel"
          hidden={active !== "b"}
          className="min-h-0 min-w-0 flex-1 overflow-hidden"
          style={{ display: active === "b" ? "flex" : "none" }}
        >
          <iframe
            data-testid="haineng-frame"
            title={chinese ? "海能 AI" : "Haineng AI"}
            src={HAINENG_URL}
            className="h-full min-h-0 w-full flex-1 border-0 bg-white"
            referrerPolicy="strict-origin-when-cross-origin"
          />
        </div>
      )}
    </div>
  );
}
