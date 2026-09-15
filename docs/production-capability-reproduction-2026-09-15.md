# 生产能力等价复刻指南（2026-09-15）

本指南用于让测试环境从当前 GitHub 分支复刻已经验收的生产**能力**。
能力等价不等于复制生产数据：用户、会话、知识库内容、数据库记录、PVC
文件、Secret 值、证书私钥和回滚归档均不得进入 Git。

## 1. 唯一基线

- 分支：`codex/enterprise-consolidation-20260716`
- 机器可读能力与镜像锁：`k8s/enterprise/release-baseline.yaml`
- Kubernetes 入口：`k8s/enterprise/kustomization.yaml`
- SkillHub 非密钥值模板：`k8s/skillhub/values-deer-flow.example.yaml`
- WeKnora v0.8 完成状态：
  `docs/weknora-v0.8-full-capability-completion-2026-09-13.md`

复刻时必须从远端分支的同一个提交开始，并保存提交号：

```bash
git fetch --all --prune
git checkout codex/enterprise-consolidation-20260716
git pull --ff-only
git status --short
git rev-parse HEAD | tee release-source.commit
```

`git status --short` 必须无输出。不要把本机 `output/`、`.codex-*`、构建
缓存或历史热更包当成发布输入；它们不是源码真相。

## 2. 复刻范围

| 能力 | 目标状态 |
| --- | --- |
| DeerFlow Gateway、前端、数字员工 | 启用 |
| Browser 工具与 Browser Live | 启用 |
| OA 制度文生图 | 轮询启用，回调禁用 |
| WeKnora App/UI/API/SSO | v0.8，启用 |
| WeKnora AnyDoc | 启用；既有 XLSX 显式冻结为 `builtin` |
| WeKnora 数据库 | `schema_migrations = 90|false` |
| SkillHub | 启用，由独立 Helm release 管理 |
| DeerFlow AIO Sandbox/PPT Runner | 保持当前生产配置和独立镜像锁 |
| WeKnora 原生 Sandbox | **未进入生产，不启用** |

WeKnora 原生 Sandbox 仍处于 ARM64 RuntimeClass/隔离能力准备阶段。测试环境
若自行启用，得到的是超出当前生产基线的实验能力，不能标记为 1:1 复刻。

## 3. 前置条件

1. Kubernetes、containerd 和 ARM64 节点满足企业部署文档要求。
2. 测试集群的节点名、StorageClass、PV 路径与容量已经按自身环境审阅。
   `k8s/enterprise` 中的静态 PV 和 `datax-*` 调度约束是当前生产拓扑，不能
   在不同拓扑上未经修改直接应用。
3. `release-baseline.yaml -> production_snapshot` 中的全部镜像均可从内部
   仓库拉取，或已从受信 OCI 归档导入每个可调度节点的 `k8s.io` namespace。
4. 自定义 WeKnora、SkillHub 及历史稳定组件的构建上下文不全部位于本仓库。
   没有对应内部镜像/受信 OCI 归档时，仅凭 Git 源码不能得到字节级相同镜像，
   必须先补齐镜像供应链，禁止临时改用 `latest`。

先逐项验证镜像，不能依赖 Pod 首次调度才发现缺失：

```bash
kubectl kustomize k8s/enterprise > /tmp/deerflow-enterprise-rendered.yaml
grep -n 'image:' /tmp/deerflow-enterprise-rendered.yaml
```

渲染结果中的主 Gateway 必须是 `digitalemployee-promptfix1`，
`ppt-master-runner` 必须保留 `skillhubux-dashboard1-r2`，两者不能被 Kustomize
合并成同一个最终镜像，也不能位于同一个 Pod。Runner 必须由独立
`Deployment/ppt-master-runner` 与同名 ClusterIP Service 承载；Gateway 通过
`http://ppt-master-runner:8003` 访问它，Runner 启动前须连续三次通过数据库
连接检查。这样 Runner 的 DNS、数据库或进程故障不会移除普通 Gateway 的
Service Endpoint。

## 4. 配置与 Secret 边界

从仓库根目录的 `config.example.yaml` 生成测试环境 `config.yaml`，再按
`k8s/enterprise/README.md` 的 release gate 配置模型、工具、Sandbox、调度、
持久化和上传策略。生产同值的非密钥配置可以复用；所有密钥只允许通过环境
变量或 Kubernetes Secret 引用。

至少准备以下对象，名称必须与清单一致：

- `deer-flow-config`、`deer-flow-extensions-config` ConfigMap；
- `deer-flow-env`、`deer-flow-postgres` Secret；
- `deer-flow-dashboard-postgres`、`deer-flow-postgres-replication` Secret；
- `weknora-secret`、`weknora-neo4j-secret` Secret；
- `deer-flow-enterprise-ca` Secret，键名 `ca-bundle.crt`；
- `deer-flow-mineru` Secret；
- SkillHub 数据库、Redis、管理员及 `SKILLHUB_SESSION_BRIDGE_SECRET` 的独立
  受保护值。

可提交的例子只有 `.env.example`、
`k8s/local-pvc/secrets/*.example.yaml`、
`k8s/enterprise/dashboard-postgres.secret.example.yaml` 和 SkillHub 示例 values。
不要从生产执行 `kubectl get secret -o yaml` 后提交输出。

## 5. 离线门禁

在创建或更新工作负载前执行：

```bash
python scripts/validate_enterprise_release.py \
  --config /secure/path/config.yaml \
  --manifest /tmp/deerflow-enterprise-rendered.yaml \
  --repo-root . \
  --baseline k8s/enterprise/release-baseline.yaml

uv run pytest \
  tests/test_enterprise_release_guard.py \
  tests/test_browser_live_nginx.py \
  tests/test_oa_policy_flow_nginx.py \
  tests/test_skillhub_web_overlay.py \
  backend/tests/test_enterprise_k8s_bundle.py -q
```

第一条必须结束于 `enterprise_release_baseline=PASS`，测试必须全部通过。

## 6. 部署顺序

1. 创建命名空间、Secret、ConfigMap、存储目录/PV/PVC，并验证权限。
2. 将锁定镜像导入所有目标节点；保存 OCI SHA-256 和 manifest 摘要证据。
3. 备份测试环境数据库；确认没有需要保留的活动任务。
4. `kubectl apply -k k8s/enterprise`，按 StatefulSet、后端、Gateway/PPT
   Runner、前端、Nginx 的依赖关系等待 Ready。
5. 按 `k8s/skillhub/values-deer-flow.example.yaml` 安装独立 SkillHub release；
   server/web 使用 `production_snapshot.separately_managed_images` 的锁定版本。
6. 运行数据库迁移和幂等默认配置 Job。不要导入生产业务数据来证明能力等价。
7. 执行下一节运行时验收。

## 7. 运行时验收

```bash
sudo KUBECONFIG_FILE=/etc/kubernetes/admin.conf \
  /bin/bash scripts/verify_enterprise_runtime.sh

kubectl -n deer-flow get deployment,statefulset,pod -o wide
kubectl -n deer-flow get deployment \
  gateway frontend weknora-app weknora-frontend nginx -o wide
```

验收至少覆盖：

- 新建普通对话、数字员工对话、定时任务及中断/恢复；
- Browser 导航、快照、点击、输入、截图和 Browser Live；
- DeerFlow 到 WeKnora 的同源 SSO、10 个知识库工具、共享空间和只读权限；
- AnyDoc 新文档解析、显式 `builtin` XLSX 兼容路由，以及不自动重解析历史文档；
- OA 提交、状态轮询、结果下载、鉴权和 SHA-256，回调保持 disabled；
- SkillHub passive bootstrap、原生角色、安装/升级/禁用/卸载权限边界；
- PPT Runner、Office 预览、文件安全、生产数据与跨用户隔离。

最终证据应同时记录 Git 提交、镜像列表、配置校验结果、数据库迁移版本和
功能验收结果。只有这些门禁一致，才能称为当前生产能力的 1:1 测试复刻。
