# 大模型压测日志（候选，默认关闭）

本功能仅适用于 RobustChatOpenAI 及继承它的适配器（包括 CNOOC V4.1）。
其他模型适配器、外部 PPT Agent 或绕过适配器的 HTTP 请求不自动覆盖。

## 开关

在部署包含此补丁的镜像时配置 Gateway 环境变量：

```text
DEERFLOW_LLM_PERFORMANCE_LOGS=true
DEERFLOW_LLM_PERFORMANCE_BATCH=20261103-evening-stage01
```

本补丁不会设置生产环境变量、构建或部署镜像。开启环境变量通常需更新 Pod，
应与待发布的内存优化镜像一起安排。默认关闭；压测结束后关闭。
不需要打开 DEERFLOW_LLM_TIMING_LOGS、RUN_TIMING 或全局 DEBUG；旧日志开关独立保留。
不要为观测并发而调整准入限流、超时、重试、模型输出或思考参数。

## 日志与口径

每个适配器调用正常输出两条 `LLM_PERF {JSON}`，不逐 token 打日志。
start 在准入成功并准备发起请求时输出；排队取消/准备失败的 start 在结束时
补记为 never_admitted，不能把它当成已发起的模型请求。进程被杀时可能没有 end，
尚在排队且从未准入的调用也可能没有日志，需由压测客户端补充统计。

* first_effective_ms：准入/请求准备完毕到首个非空思考、正文或工具参数/名称增量。
* first_reasoning_ms / first_content_ms / first_tool_ms：分别记首次思考、正文、工具输出。
  空/空白文本、role-only、仅工具 ID、usage-only 数据块不计。时间是在适配器转换后、
  callback 之前观察的；是首次有效增量的近似 TTFT，不是逐 token 解码时间。
* queue_ms：进入本次计时到请求准备完毕，包含本地准入等待和 payload 准备。
  同步/非流式路径没有该准入队列，不能把这个字段解释成上游服务排队。
* service_ms：请求准备完毕到适配器结束；包含网络、上游等待、重试、回调与下游消费。
* total_ms：queue_ms + service_ms；非流式只能测总时间，所有首输出字段为 null。
* input_tokens / output_tokens / reasoning_tokens：上游 usage 数据，缺失为 null。
  不把 SSE 数据块数当 token 数；思考 token 可能已包含在输出 token 中，不再相加。
* active / pending：同一进程、同一模型的当前计数。start 的 active 包含本次调用，
  end 的 active 已扣除本次调用。覆盖同步和异步路径，不依赖限流器开启。
  active 包含流下游消费时间，不是模型服务内部正在计算的数量。
* outcome / error_class / status_code：正常、错误、取消或关闭；不记录异常正文。
* pod + process_id + llm_call_id 标识调用，thread_id/run_id 关联业务运行。
  SDK 物理重试可能被包含在同一个适配器调用中；测单次 HTTP 时必须核对重试配置。

计时使用单调高精度时钟，跨 Pod 时间线用本次调用起点的系统时间加单调时间差。
跨节点时钟偏差依然影响集群并发复原。已知配置批次之外，还应在压测记录中保存：
镜像 digest、模型/思考设置、输入长度、输出上限、重试、限流和压测客户端设置。

不新增数据库写入、后台采样任务、调用历史缓存。进程中仅保留活动调用的标量状态，
空闲模型计数项删除。只记录标识符及数值，不记录输入、正文、向量、凭据或请求头。
标识符仍属于内部运维数据。标准日志 sink 的 I/O 可能阻塞；应配置已有日志轮转，
并通过同等负载下开关日志的对照测试评估开销，不能把单元测试当性能证明。

## 离线报告

只读收集各 Gateway 的 `LLM_PERF` 行（压测开始前至全部调用结束），保留重启前日志。
不要从管理看板的业务运行数代替模型调用数。同一运行可能多次调用模型。

```bash
python3 scripts/llm_performance_report.py /path/to/logs/*.log --out /path/to/new-report
```

产出 calls.csv（逐调用）、concurrency.csv（时间线）、summary.json（汇总）。
按批次、模型和流式模式分组；延迟分位数只统计成功且字段存在的调用，同时记录样本数。
所有错误和未完成调用独立计数，不会用零耗时填补。吞吐为观测窗口内完成数/秒。
输出 token 速率为完整 usage 样本的窗口聚合值，不是单请求生成速度。
报告只复原已收集且有结束记录的并发，未结束调用不猜结束时间，所以并发峰值为下界。
需要全部 Pod 日志和客户端统计才能说明集群整体表现，不能将各 Pod 峰值相加。
缺 start、缺 end、重复/冲突或损坏行都需检查；进程被杀不能单靠服务端日志复原。

## 验证边界

本地单元测试覆盖空包、思考、正文、工具、usage、取消、关闭、计数释放和报告。
另有真实 CNOOC 适配器 MockTransport 测试；不调用真实模型。
服务器源码回归通过也不等于镜像安装、生产启动或压测通过；原 OOM 根因仍未证明。
