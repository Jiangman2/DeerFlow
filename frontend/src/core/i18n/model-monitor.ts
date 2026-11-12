export function modelMonitorCopy(locale: string) {
  return locale.startsWith("zh")
    ? {
        title: "模型调用监控",
        subtitle: "平台已接入采集的模型调用 · 独立刷新",
        refresh: "刷新",
        model: "选择模型",
        window: "统计窗口",
        last15: "最近 15 分钟",
        last60: "最近 1 小时",
        current: "当前未结束调用",
        starts: "区间发起调用",
        success: "区间成功调用",
        errors: "区间错误 / 超时",
        calls: "每分钟发起调用",
        concurrency: "并发采样峰值",
        unavailable: "模型监控暂时不可用，其他看板模块可继续使用。",
        notConfigured:
          "模型调用采集尚未启用。启用后开始记录，历史并发无法补算。",
        stale: "暂未收到新的采集心跳；不将缺失数据显示为零。",
        empty: "已连接采集，暂未记录到模型调用。",
        loading: "正在加载监控…",
        coverage:
          "统计已接入采集的 Gateway 应用调用，不等于运行中任务数或模型服务端实际并发。一个任务可包含主 Agent、子 Agent 和辅助调用，等待模型响应也计入在途。已取消的调用计入取消，不计入成功。并发每 5 秒采样，可能遗漏短时尖峰；最长在途按最新采样计算，长请求不会因时长自动清零。旧版本采集的历史计数可能包含未清理的调用。未接入的外部系统、服务端内部重试及远端视频任务执行并发不在此统计内。",
        label: "模型 / 操作",
        active: "在途",
        started: "发起",
        completed: "成功",
        failed: "错误",
        timeout: "超时",
        cancelled: "取消",
        peak: "采样峰值",
        average: "平均耗时",
        oldest: "最长在途",
        seconds: "秒",
        processes: "报告进程",
        dropped: "丢弃观测",
        updated: "采集时间",
        missing: "缺失",
        operations: {
          invoke: "调用",
          submit: "提交",
          poll: "状态轮询",
          session: "会话",
          relay: "PPT 上游请求",
          download: "结果下载",
        } as Record<string, string>,
      }
    : {
        title: "Model monitoring",
        subtitle: "Instrumented platform calls · refreshes independently",
        refresh: "Refresh",
        model: "Select model",
        window: "Window",
        last15: "Last 15 minutes",
        last60: "Last hour",
        current: "Current unfinished calls",
        starts: "Calls started",
        success: "Successful calls",
        errors: "Errors / timeouts",
        calls: "Calls started per minute",
        concurrency: "Sampled peak concurrency",
        unavailable:
          "Model monitoring is unavailable. Other dashboard panels remain available.",
        notConfigured:
          "Model telemetry is not enabled. Recording starts when enabled; historical concurrency cannot be reconstructed.",
        stale:
          "No fresh collector heartbeat. Missing observations are not zero usage.",
        empty: "Collector connected; no model calls observed yet.",
        loading: "Loading monitoring…",
        coverage:
          "Instrumented Gateway calls, not running tasks or provider-side concurrency. A task may include lead, subagent and auxiliary calls; waiting for a model response counts as in flight. Cancelled calls are not successes. Concurrency is sampled every 5 seconds and may miss brief peaks. Oldest age is measured at the latest sample; long calls are not expired by age. Historical counts from older collectors may include unclosed calls. Uninstrumented services, provider-internal retries and remote video jobs are excluded.",
        label: "Model / operation",
        active: "In flight",
        started: "Started",
        completed: "Success",
        failed: "Error",
        timeout: "Timeout",
        cancelled: "Cancelled",
        peak: "Sampled peak",
        average: "Mean duration",
        oldest: "Oldest in flight",
        seconds: "s",
        processes: "Reporting processes",
        dropped: "Dropped observations",
        updated: "Collected",
        missing: "Missing",
        operations: {
          invoke: "Invoke",
          submit: "Submit",
          poll: "Status poll",
          session: "Session",
          relay: "PPT upstream request",
          download: "Result download",
        } as Record<string, string>,
      };
}

export function oaMonitorCopy(locale: string) {
  return {
    ...modelMonitorCopy(locale),
    ...(locale.startsWith("zh")
      ? {
          title: "OA 文生图接口监控",
          subtitle: "制度文生图 · 提交、状态查询与结果下载分别统计",
          model: "接口操作",
          current: "当前执行请求",
          starts: "接口调用量",
          success: "成功响应",
          errors: "失败 / 超时响应",
          calls: "每分钟接口调用",
          concurrency: "接口在途请求采样峰值",
          label: "接口 / 操作",
          empty: "已连接采集，暂未记录到 OA 文生图接口调用。",
          unavailable: "OA 接口监控暂时不可用，其他看板模块可继续使用。",
          coverage:
            "统计 OA 制度文生图接口的 HTTP 请求，包含鉴权失败。提交成功（202）表示已受理，不代表生成完成；当前执行数为尚未完成响应的请求，不等于后台排队或生成任务数。并发每 5 秒采样，耗时包含响应传输；不与底层模型调用合并计数。",
        }
      : {
          title: "OA diagram API monitoring",
          subtitle: "Policy diagrams · submissions, status polls and downloads",
          model: "API operation",
          current: "Current in-flight requests",
          starts: "API requests",
          success: "Successful responses",
          errors: "Failed / timed-out responses",
          calls: "API requests per minute",
          concurrency: "Sampled in-flight API requests",
          label: "API / operation",
          empty:
            "Collector connected; no OA diagram API requests observed yet.",
          unavailable:
            "OA API monitoring is unavailable. Other dashboard panels remain available.",
          coverage:
            "OA policy diagram HTTP requests, including authentication failures. A successful 202 submission means accepted, not generation completed. In-flight requests are not queued or executing background jobs. Concurrency is sampled every 5 seconds; duration includes response transfer. Counted separately from underlying model calls.",
        }),
  };
}
