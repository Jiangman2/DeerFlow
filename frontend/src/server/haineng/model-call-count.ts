import { createHash } from "node:crypto";

// Use the HTTPS request example in the supplied document. The shorter path
// in its overview remains configurable via HAINENG_MODEL_CALL_COUNT_URL.
export const DEFAULT_HAINENG_MODEL_CALL_COUNT_URL =
  "https://ai-dev.cnooc.com.cn/gaixc/hy-dashboard/api/listedModelService/modelCallCount";

export type ModelCallCount = {
  todayModelCallCount: number;
  totalModelCallCount: number;
};

export function buildHainengAuthHeaders(
  appId: string,
  // This is the original secret allocated by Haineng, not a signed header.
  appKey: string,
  timestamp: number,
) {
  const signature = createHash("md5")
    .update(`${appId}UZ${appKey}UZ${timestamp}`, "utf8")
    .digest("hex");

  return {
    "APP-ID": appId,
    "APP-KEY": `${signature}.${timestamp}`,
  };
}

export function parseModelCallCount(payload: unknown): ModelCallCount | null {
  if (typeof payload !== "object" || payload === null || !("code" in payload)) {
    return null;
  }
  if (payload.code !== 200 || !("data" in payload)) return null;

  const data = payload.data;
  if (typeof data !== "object" || data === null) return null;
  if (!("todayModelCallCount" in data) || !("totalModelCallCount" in data)) {
    return null;
  }

  const { todayModelCallCount, totalModelCallCount } = data;
  if (
    !Number.isSafeInteger(todayModelCallCount) ||
    !Number.isSafeInteger(totalModelCallCount) ||
    (todayModelCallCount as number) < 0 ||
    (totalModelCallCount as number) < 0
  ) {
    return null;
  }

  return {
    todayModelCallCount: todayModelCallCount as number,
    totalModelCallCount: totalModelCallCount as number,
  };
}
