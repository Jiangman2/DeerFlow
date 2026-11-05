export function monitor(minutes = 15) {
  const at = Date.parse("2026-11-02T08:00:00Z") / 1000;
  const series = [
    { kind: "text", model: "DeepSeek-V4.1-Flash", operation: "invoke" },
    { kind: "image", model: "平台生图模型", operation: "invoke" },
    { kind: "video", model: "平台视频模型", operation: "submit" },
    { kind: "text", model: "PPT DeepSeek", operation: "relay" },
    { kind: "integration", model: "oa-policy-flow", operation: "submit" },
    { kind: "integration", model: "oa-policy-flow", operation: "poll" },
    { kind: "integration", model: "oa-policy-flow", operation: "download" },
    { kind: "integration", model: "oa-policy-flow", operation: "job" },
  ].map((r, i) => ({
    ...r,
    key: `${r.kind}:${r.operation}:${r.model}`,
    active: 12 - i,
    started: 200 - i * 25,
    success: 180 - i * 25,
    error: 2,
    timeout: 1,
    cancelled: 1,
    duration_seconds: 5600,
    peak_concurrency: 24 - i,
    average_seconds: 40,
  }));
  return {
    status: "observed",
    generated_at: at,
    reporting_processes: 70,
    oa_jobs: {
      status: "observed",
      running: 7,
      queued: 1,
      freshness_seconds: 900,
    },
    dropped_observations: 0,
    series,
    trend: Array.from({ length: minutes }, (_, i) => ({
      at: at - (minutes - 1 - i) * 60,
      series:
        i < 2
          ? null
          : Object.fromEntries(
              series.map((r) => [
                r.key,
                {
                  started: 10 + (i % 4),
                  success: 8 + (i % 4),
                  error: 0,
                  timeout: 0,
                  cancelled: 0,
                  duration_seconds: 80,
                },
              ]),
            ),
      concurrency:
        i < 2
          ? null
          : Object.fromEntries(
              series.map((r, j) => [r.key, 8 + ((i * 3 + j) % 15)]),
            ),
    })),
  };
}
