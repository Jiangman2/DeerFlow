# WeKnora 双副本准备：第一阶段只读清单

本工具只做 Kubernetes GET，不进行扩容、迁移、重启、重试文档、Pod exec、
数据库查询、Redis 查询或模型请求。无需旧工具链，也无需下载依赖。

在 datax-01 执行交付包的 RUN-datax01.txt；脚本及日志保存在新建的
root:root 0700 目录中。不得把 /var/lib 或旧证据目录改成 0777。
把终端最后一条完整 JSON 或本次 check.log 交回即可。不要发送 kubeconfig、
数据库配置、Secret 或整个私有证据目录。

## 输出范围与边界

- 读取部署、所属 ReplicaSet/Pod、关联 Service/Endpoints、已挂载 PVC/PV，
  HPA/PDB/NetworkPolicy 清单及 datax-01/02/03 节点元数据。
- 只输出配置的数字/枚举白名单、对象身份、卷类型/挂载点、调度约束摘要、
  资源 request/limit 和节点 allocatable；不输出原始 spec/env/日志或凭据。
- Secret/ConfigMap 引用只记录来源类型和引用身份摘要，不读取其对象内容。
- Kubernetes API 返回的部署模板可能本来含有明文 env；工具仅在内存解析，
  不输出未知 env 或这些值，不把完整 API JSON 写入文件。
- 最后重新读取采集对象并校验配置、Pod 身份、就绪状态及路由是否变化。
  无权限/超限/超时显式报告，不把无法读取当作“没有配置”。
- 每条命令最长约 25 秒，总流程约 4 分钟上限加最后一条命令的退出时间；
  不自动重试。结束码 0 只表示采集完成且窗口内基线稳定。
- `ready_to_scale` 永远为 false。这不是扩容执行器或放行报告。

必须另外验证：跨节点实际文件访问与历史排队任务路径、数据库/Redis连接预算、
实时节点资源、后台调度与恢复的跨实例行为、身份/加密/会话共享、模型全局限额、
双实例消费与故障恢复、迁移和回滚窗口。env 数值只是模板值，不能代替数据库
系统设置或运行时有效值。双实例槽位算术只是提示，不是建议直接翻倍。

## 官方依据（2026-10-17 查询 main，非现网版本验收）

1. [Helm 生产部署](https://github.com/Tencent/WeKnora/blob/main/helm/README.md#production-installation)
   明确给出多个 App 副本配已验证 RWX 文件卷的方案；默认 RWO/local 文件
   存储建议单副本。RWO 限制的是节点，不等于只允许一个 Pod；同节点双副本
   不能提供节点故障高可用。对象存储也须核对所有相关路径。
2. [官方环境变量](https://github.com/Tencent/WeKnora/blob/main/.env.example)
   说明 Worker 池容量按实例配置，多副本累加。
3. [异步任务容量指南](https://github.com/Tencent/WeKnora/blob/main/website-docs/02-architecture/05-async-tasks.md#capacity-planning)
   建议依据扇出后的到达率、耗时、利用率和下游能力调参，而非按队列长度
   直接放大所有池。Redis/Asynq 用于标准部署。

本地源码初步检查（并非已绑定运行二进制的结论）：

- `internal/datasource/scheduler.go` 已有基于数据源和时间窗口的 Asynq TaskID
  去重及运行中同步检查，不能称为“完全没有跨实例去重”。
- housekeeping 等后台进程仍需逐项验证多实例下的互斥/幂等与配置传播。
- 主 PostgreSQL 连接初始化看到 MaxIdleConns，未在该初始化看到
  MaxOpenConns；需核实运行版本和总连接预算，不能据此直接断言生产连接耗尽。

不套用官方 Helm 安装命令覆盖本企业自定义 Deployment、PostgreSQL 镜像或卷。
不因为 main 文档声称动态配置，就假定当前定制镜像支持无重启调参。

## 本地验证

`python -B -m unittest -v test_inventory`。交付前再在无网络、只读源码挂载的
Linux Python 容器中运行同一组测试，并检查 Python 3.7 语法与 Bash 启动器。
测试使用合成 Kubernetes 对象，不读取生产环境，不是现网双副本验收。
