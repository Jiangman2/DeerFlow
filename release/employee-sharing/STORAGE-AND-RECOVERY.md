# 数字员工共享：数据保护与恢复验收

## 2026-09-24 v6 仅刷新已确认的前端部署基线

v5 已通过 Gateway 底座暂存，在 Frontend 滚动替换期间被状态门禁停止，尚未生成
候选镜像或执行生产写入。管理员确认保留双副本；12:13Z 的刷新报告
`f0889847295c3b13b02df5b9c7854a42ab203bc1916b59361efba7791c373db3`
只改变前端部署规格摘要/实例列表，其余三工作负载、全部镜像身份和 19 个源码挂载
保持一致。v6 从冻结 v5 业务增量生成，仅更新证据、包索引/清单和说明；不变更
数据库、员工定义、Skill 或生产副本。原底座归档复用，v5 失败目录保留。
新报告不是 SQL/NFS 一致性备份、运行时验收或回退成功证明，相关门禁仍未关闭。
后续上线必须保留届时已确认的前端双副本和部署配置，不能拿旧单副本清单覆盖。

## 2026-09-24 v5 仅修复构建校验器

用户实际构建已走到 Gateway OCI 暂存：固定身份匹配、345 层及对应 345 个合法
DiffID 被原有 128 层检查误拒。v5 使用有界 1024 层工作量限制和逐项错误诊断，
保留全部身份/摘要/大小/路径检查，不修改员工业务数据或生产镜像。
三份业务 delta、镜像锁和源码覆盖报告与 v4 逐字一致。旧包及失败证据不覆盖；
复用 root 私有底座归档并用新 v5 输出目录。此前 `--check-only` 成功不是资源
RUN/镜像构建/业务验收成功；存储一致性及兼容回退门禁没有因此关闭。

## 2026-09-24 实际源码覆盖报告已收到

报告 `employee-sharing-source-overlays-20260924T100314Z.json`，SHA256
`41aa33d91d030118caf160023f947c204e40e877fc5df9ec2bed9c7028a821ec`，
确认 19 个固定源码挂载及容器实际文件摘要。Gateway 的 15 个挂载来源是
`deer-flow-scheduler-repair-20260916-r8`，UID `75d5ea8a-75e8-46d2-beee-82d259219b8c`，
resourceVersion `11971154`，可变 ConfigMap。volume 名则为 `scheduler-repair-20260916-r8`。
仅启动文件与员工增量交叠，其采集源摘要与冻结候选一致；其余 14 个保持原内容。

PPT Runner 的 4 个独立补丁来自不可变 ConfigMap
`ppt-runner-startup-safety-542a3c2ca306c7a2`（deps 及迁移 0018/0019）和
`ppt-runner-execution-lease-fd-9b703c4c17847f34`（AIO 共享租约）。本次不改这两者。
Frontend 和 Provisioner 没有固定源码覆盖。新报告与已捕获的三个实际镜像身份一致。

`effective_sources.py` 已接入离线构建和整包校验：精确 OCI 身份门禁不变；
可丢弃 Gateway 构建层先重现 15 个真实运行文件，再执行 46 文件员工增量的来源检查和应用。
未在先前 97 文件报告内的 14 个挂载源码仅按本次固定字节保留，不自行加入功能改造。
PPT Runner 的 4 个私有覆盖不复制进 Gateway。构建前/过程/完成时的只读 guard
核验模板、实际 Pod、ConfigMap UID/版本/键摘要及有限的容器哈希读取，变化即中止。
这些都是构建输入/控制验收，不修改线上 ConfigMap 或实例，不是生产 rollout 实现。

普通周期仅做元数据及 ConfigMap 源码复查；容器首次观察和构建完成前才重读源码哈希。
单轮 source guard 查询预算 30 秒，每条固定读取另有 30 秒超时，超出即失败。
无法提供检查后永不变化的保证，正式发布前仍必须重新校验并建立维护停写边界。
未来发布仅允许 Gateway 启动文件使用独立不可变候选 ConfigMap，原共享调度卷、其余
文件、全部 PPT Runner 挂载均保留；当前纯本地模板逆向演练不能替代真实数据回退验证。

原 v3 包保持字节不变并停用。新构建输入仍为 `ready_for_rollout=false`，没有生产写入指令。
下面存储与旧步骤的记录保留为历史证据；不再需要重复提供这一份源码报告。

最终九文件控制工具回归：Windows **212 通过、9 跳过，127.53 秒**。
覆盖全部 15 个采集文件在临时树上的真实重现、仅 Gateway 增量应用顺序、PPT 私有文件
不混入 Gateway、后项语法错误阻止前项写入、并发变更拒绝、只读 guard 接线与有界读取、
整包索引校验、原镜像底座/构建控制回归。跳过项需要 Linux/链接权限，本轮未实跑。
定向 Ruff/格式通过。旧 v3 归档 SHA256 仍为
`95c6138a77ad9ee3e589199afce75a2d4a1cdd8bca223d61ad74bd1912974e77`。

## 2026-09-24 实际生产存储报告已收到

报告 `employee-sharing-storage-20260924T091727Z.json` 的 SHA256 为
`08684c7545b80e9a6292b0e72986a2a2b856e7ab224ee2151d2c916bb47b144c`，
采集时间为 2026-09-24 17:17:30（北京时间）。它只反映当时的只读观测，不是当前健康或恢复证明。

| 对象 | 实际位置 | 恢复边界 |
| --- | --- | --- |
| 员工定义配置 | Gateway 配置 `agent_storage=db`、`database=postgres` | 必须覆盖 PostgreSQL；只备份目录不够 |
| 应用运行目录 | `/app/backend/.deer-flow`，PVC `deer-flow-userdata-rwx-pvc`，subPath `deer-flow` | NFS `192.168.111.189:/mnt/data/deerflow-rwx/home`；结合 subPath 推得应用根为该导出下的 `deer-flow` 子目录 |
| Skill 目录 | `/app/skills`，PVC `deer-flow-skills-rwx-pvc`，Gateway 只读挂载 | NFS `192.168.111.189:/mnt/data/deerflow-rwx/skills`；RWX 声明不等于锁/rename/跨节点验证 |
| PostgreSQL 数据 | `postgres-0` 在 datax-02，PVC `data-postgres-0` | PV local 路径 `/opt/deerflow-storage/postgresql`；不能直接复制在线数据库目录充当一致备份 |

Gateway、PPT Runner 和 8 个 Running 沙箱 Pod 使用相关共享卷；报告中的 initContainers
与 Succeeded 备份 Pod 不证明它们现在仍在写入。其他 NFS 客户端、管理接口、后台任务及
数据库写者仍需维护边界确认。旧 local PVC 没被当前 Gateway 使用，不代表可以删除。
顶层 `agents`/`threads` 不存在，不代表员工丢失；报告存在 `users`、`team_agents`，且定义配置采用数据库。
没有查询数据库行数、员工正文或可用备份，不能据此宣称现有员工已经完成备份校验。

### 镜像外启动代码覆盖：新增发布阻断项

Gateway 的 `/app/backend/app/gateway/app.py` 被名为
`scheduler-repair-20260916-r8` 的 **volume** 以 subPath `gateway_app.py` 覆盖。
该卷类型为 ConfigMap，但报告未输出 ConfigMap 资源名称，不能把 volume 名当作资源名。
同卷还挂载多个已有调度及迁移补丁。员工候选增量恰好修改该启动文件来注册新接口和服务：
仅换镜像不能确保新代码生效；之前通过 exec 取得的源代码锚点也可能是挂载字节而非底座 OCI 字节。

开发版 `build_offline.py` 已加入模板和 live Pod 的挂载重叠检查，覆盖变更文件、未修改的
依赖锚点以及 Frontend 编译输出。只读挂载同样阻断；配置、CA、员工数据和 Skill 的无关挂载不阻断。
不读取或更改 ConfigMap/Secret，不提供跳过开关，也不删除已有调度补丁。
**已交付的 v3 包保持原字节，未包含此检查；其实际构建暂缓。**

下一版必须对精确的挂载来源/摘要和 OCI 底座分别建证据，明确哪些覆盖继续保留、哪些采用
候选专用挂载或经过审阅的镜像整合，并为对应挂载变更保留回退配置。
目前还没有完成这项 overlay-aware 发布实现，不应发布新的“可热更”包。

### 补充实现：只读挂载来源核对与配置层演练

`collect-source-overlays.py` 为独立 Python 3.7+ 小工具，管理员在 datax-01 执行。
它读取 Gateway、Frontend、Provisioner、PPT Runner 的 Deployment/Pod；只导出引用在
固定、只读 Python 源码文件挂载上的 ConfigMap 键，记录实际 ConfigMap 名称、UID、版本、
键名、源码和摘要。其他键、环境值、Secrets、员工数据不导出。
在已有就绪容器中执行固定的只读哈希程序，验证正在使用的源码与 ConfigMap 键一致；
ConfigMap 更新但 subPath 仍旧、实例重启/替换、模板与实际挂载不同、混合镜像 ID 均失败。
目录覆盖、动态 subPath、Secret 源码挂载等未知模式拒绝，不擅自扩展读取范围。
查询约 210 秒内有界；唯一写入是全新 0600 报告，没有 Pod 创建或服务重启。
报告包含应用补丁源码，按内部资料保管；它不替代一致性备份或发布许可。

独立交付文件 `collect-employee-source-overlays-20260924-v1.py`，SHA256
`581e8de9b1cf63d6a803edfcc4c466db7779aa859dec4c9cc191ff9c47fb5cf3`。
上传脚本和 `.sha256` 到 `/home/cnooc`，先校验，再手动执行：

```bash
cd /home/cnooc
sha256sum -c collect-employee-source-overlays-20260924-v1.py.sha256
sudo python3 -B /home/cnooc/collect-employee-source-overlays-20260924-v1.py \
  --output "/home/cnooc/employee-sharing-source-overlays-$(date -u +%Y%m%dT%H%M%SZ).json"
```

纯本地 `source_overlay_plan.py` 按真实来源和已审阅候选字节生成审阅方案：旧共享调度
ConfigMap 不改、不删；仅 Gateway 的启动文件切换至内容寻址、不可变、只含 `gateway_app.py`
的新 ConfigMap，PPT Runner 保持原挂载。提议模板只改 Gateway 镜像、该文件 mount 的 volume
引用并添加一个新 volume，随后在私有副本中反向核对整个 spec 完全相等。
遇到另一个变更文件也被挂载、源字节改变、实例镜像不符、共享键证据冲突或旧 spec 漂移均拒绝。
仅接受 digest 固定的候选镜像名，但不宣称该镜像已经存在或通过构建。
没有调用 Kubernetes 的 apply/patch，也没有自动恢复或数据回退许可。
来源报告与构建控制后续已接通，见本页开头；精确 OCI 实际构建、发布时来源复查及
维护/恢复门禁仍未实跑，不应视为已关闭。

两份新增测试最终在 Windows **42 通过**，包括真实临时文件只读哈希（仅 Linux 路径映射至
临时文件；Windows 不验证 O_NOFOLLOW 内核语义）、来源漂移负例和完整模板逆向相等。
此前与构建控制组合为 **131 通过、3 跳过**，随后新增 6 项采集用例；批次不能相加。
本轮 Docker Desktop Linux 引擎未响应，没有重启它，也没有运行 Linux/ARM64 生产验收。
第一次定向 lint 因测试 fixture 导入命名产生 F811，修正为显式 fixture 别名后复测通过。
旧 v3 包未改、未重新发布，仍不可上线。

## 当前工具边界

`collect-storage-topology.py` 由管理员在 **datax-01** 手动执行，Python 3.7+。
只读获取 Gateway、Pod、PVC/PV 元数据，并在就绪的 Gateway 内执行短暂的只读 Python 程序。
不导出环境变量、Secrets、数据库 URL、员工正文、记忆、聊天或上传文件；不加载应用、不连接数据库、不改 Deployment、不停止任务。
配置只输出 `agent_storage.backend` 和 `database.backend` 的合法枚举。
数据根只按显式 `DEER_FLOW_HOME` 读取顶层目录属性，不枚举员工和会话。
找不到显式配置/路径、遇到未解析变量或不支持的值时报告 UNKNOWN，不默认为已确认的文件存储。
Kubernetes ConfigMap 的配置文件链接可以读取；数据根及祖先链接则报告不安全，不继续遍历。

唯一写入是 `--output` 指定的**全新、0600 本机报告**。不能覆盖原报告，报告应按内部运维资料保管。
整体查询预算约 210 秒；无须安装依赖。Gateway 已有的 PyYAML 用于读取配置。
异常不会打印可能包含凭据的 YAML 行或 kubectl 原始错误。

```bash
sudo python3 -B /home/cnooc/collect-employee-storage-topology-20260924-v1.py \
  --output "/home/cnooc/employee-sharing-storage-$(date -u +%Y%m%dT%H%M%SZ).json"
```

这份报告用于确定备份和维护范围，不是在线一致性快照。PVC 的 RWX 声明不证明 NFS 锁/rename/只读挂载正确；命名空间外的 NFS 客户端和数据库写者仍未知。
`writers_quiesced=false`、`consistent_backup=false`、`ready_for_rollout=false` 保留不变。
员工 Skill 快照、授权及历史位于 `team_agents`；个人定义、草稿/删除恢复目录、头像和记忆位于用户 agents 根；会话发布版本绑定另位于 threads 根，不能遗漏后者。

## 已实现的恢复比对

`restoration_guard.py` 不执行恢复，只给隔离恢复演练比较结果。
输入是**恢复前最新状态**与恢复目标的完整员工文件、SQL 员工行（db 后端必需）及线程绑定清单，而不是只用升级前快照。
对员工行使用原始行摘要和稳定 ID/所有者，不因名称相同就当作同一员工。
对全部记录执行严格等价比较：缺失、修改和额外记录都会阻断；额外记录也可能是复活已删除员工或恢复旧授权。
`capture_bindings` 只读取精确的 `employee-publication.json`，不进入 `user-data`，检测普通扫描期变动，拒绝链接、越界及不完整证据。

已补真实本地发布/撤权/文件备份演练：发布后再编辑、下架、撤权、新建员工，旧备份恢复被拒；最新文件备份恢复保留下架、撤权、新员工与未知字段；定义备份遗漏真实会话绑定被明确拒绝。
这是文件恢复检查，不是生产 PostgreSQL 或 NFS 演练。

## 上线与回退必须满足的条件

1. 明确实际文件/数据库根及所有写者，先完成维护停写，不能把 `active_runs=0` 等同于管理接口、定时任务、渠道和后台记忆写者全部停止。
2. 获取同一维护边界下的数据库和文件快照，保存权限、所有者和共享盘元数据；目前定义备份不覆盖数据库、线程绑定、目录权限、ACL/xattr，不能单独承担生产恢复。
3. 候选在隔离数据上恢复并比对；旧镜像不能理解新发布指针/头像引用/草稿协议，镜像回退需读写兼容证明。不得仅凭数据比对相等就授权旧镜像运行。
4. 升级后若已发生新建、修改、下架或撤权，先保护最新状态，使用兼容恢复/前向修复方案；禁止直接恢复升级前快照覆盖生产。
5. 真正的放行还要合并精确 ARM64 镜像、NFS、SSO/WeKnora 多身份和普通功能回归结果。此处没有自动 rollout、恢复或删除命令。
