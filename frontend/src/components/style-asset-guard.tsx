"use client";

import { useEffect, useRef, useState } from "react";

const RECOVERY_QUERY = "_css_recovery";

/**
 * Recovers once when the HTML shell loads but its hashed stylesheet does not.
 * The probe is hidden only by the external Tailwind stylesheet.
 */
export function StyleAssetGuard() {
  const probeRef = useRef<HTMLSpanElement>(null);
  const [recoveryFailed, setRecoveryFailed] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const probe = probeRef.current;
      if (!probe) return;

      const stylesLoaded = window.getComputedStyle(probe).display === "none";
      const url = new URL(window.location.href);

      if (stylesLoaded) {
        if (url.searchParams.has(RECOVERY_QUERY)) {
          url.searchParams.delete(RECOVERY_QUERY);
          window.history.replaceState(window.history.state, "", url);
        }
        return;
      }

      if (url.searchParams.get(RECOVERY_QUERY) === "1") {
        setRecoveryFailed(true);
        return;
      }

      url.searchParams.set(RECOVERY_QUERY, "1");
      window.location.replace(url.toString());
    }, 450);

    return () => window.clearTimeout(timer);
  }, []);

  return (
    <>
      <span
        ref={probeRef}
        className="hidden"
        data-testid="style-asset-health-probe"
        aria-hidden="true"
      />
      {recoveryFailed ? (
        <div
          role="alert"
          style={{
            position: "fixed",
            zIndex: 2147483647,
            top: 12,
            right: 12,
            left: 12,
            padding: "12px 16px",
            border: "1px solid #e7a645",
            borderRadius: 8,
            background: "#fff8e8",
            color: "#573b12",
            font: '14px/1.5 "Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
            boxShadow: "0 8px 24px rgba(69, 50, 17, 0.18)",
          }}
        >
          页面样式资源未能加载。请按 Ctrl+F5 强制刷新；如使用 360
          浏览器，请切换到极速模式。 Page styles failed to load; refresh the
          page or switch to a modern browser engine.
        </div>
      ) : null}
    </>
  );
}
