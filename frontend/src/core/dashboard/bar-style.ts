import type { CSSProperties } from "react";

export const DASHBOARD_BAR_COLOR = "#0891b2";

export function dashboardBarStyle(
  value: number,
  maxValue: number,
): CSSProperties {
  const safeValue = Math.max(0, value);
  const safeMax = Math.max(1, maxValue);
  return {
    width: `${Math.max(safeValue > 0 ? 8 : 0, (safeValue / safeMax) * 100)}%`,
    backgroundColor: DASHBOARD_BAR_COLOR,
  };
}
