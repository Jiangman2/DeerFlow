"use client";

import { useEffect, useState, type ReactNode } from "react";

import { useHainengHistory } from "@/core/haineng/history";
import { useI18n } from "@/core/i18n/hooks";

import { HainengChat } from "./haineng-chat";

/** Keep both platforms mounted: switching must not reset a running chat. */
export function PlatformSwitcher({
  children,
  preview = false,
}: {
  children: ReactNode;
  preview?: boolean;
}) {
  const { openVersion, workVersion } = useHainengHistory();
  useEffect(() => {
    if (workVersion > 0) setActive("a");
  }, [workVersion]);
  useEffect(() => {
    if (openVersion > 0) {
      setHasOpenedB(true);
      setActive("b");
    }
  }, [openVersion]);
  const { locale } = useI18n();
  const chinese = locale === "zh-CN";
  const [active, setActive] = useState<"a" | "b">("a");
  const [hasOpenedB, setHasOpenedB] = useState(false);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
      <nav
        aria-label={chinese ? "平台切换" : "Platform switcher"}
        className="flex shrink-0 justify-center px-4 py-3"
      >
        <div className="bg-muted/70 border-border/60 relative isolate grid w-56 grid-cols-2 rounded-full border p-1 shadow-inner">
          <span
            aria-hidden="true"
            className="bg-background pointer-events-none absolute top-1 bottom-1 left-1 -z-10 w-[calc(50%-4px)] rounded-full shadow-sm ring-1 ring-black/5 transition-transform duration-300 ease-out motion-reduce:transition-none"
            style={{
              transform: active === "b" ? "translateX(100%)" : "translateX(0)",
            }}
          />
          <span
            aria-hidden="true"
            className="bg-primary pointer-events-none absolute top-1/2 left-5 size-2 -translate-y-1/2 rounded-full transition-transform duration-300 ease-out motion-reduce:transition-none"
            style={{
              transform:
                active === "b"
                  ? "translate(108px, -50%)"
                  : "translate(0, -50%)",
            }}
          />
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
              className="text-muted-foreground aria-pressed:text-foreground relative min-h-10 rounded-full py-2 pr-3 pl-6 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              {platform === "a"
                ? chinese
                  ? "工作"
                  : "Work"
                : chinese
                  ? "对话"
                  : "Chat"}
            </button>
          ))}
        </div>
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
          <HainengChat preview={preview} />
        </div>
      )}
    </div>
  );
}
