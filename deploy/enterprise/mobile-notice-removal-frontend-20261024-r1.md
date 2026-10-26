# 移动端移除非密红字提示

用户于 2026-10-24 要求移除移动端主对话页红色提示并上线。桌面端继续保留完整红字。沿用现有 768px 响应式断点；新对话和已有主对话一致，移动布局不为提示保留空白。

## 候选范围

- 从已上线 Frontend 227 的封存源码构建，基线镜像 `sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638`。
- 只改变 `frontend/src/app/workspace/chats/[thread_id]/page.tsx`：删除手机提示节点及未使用的导入。
- 桌面提示组件、语言文案、问数实时进度、GLM Token 显示、员工目录及所有 API 保持基线代码。
- 镜像标签 `deerflow.local/deer-flow-frontend:20261024-mobile-notice-removal-r1`。
- 下方保留候选准备过程；现场已收到 Frontend 228 切换成功回执，浏览器显示验收仍待确认。历史 production-source-lock 保留，新增 20261024 源码锁记录本次已发布镜像。

## 发布方式

1. 新的只读部署回执确认 frontend UID、generation、镜像及两副本状态。
2. 在 datax-02 和 ecs-zdz-pro-0015 分别预载完整 OCI 包；保留旧镜像引用，检查实际磁盘布局和余量，失败不自动重试。
3. 收到两台节点的真实成功回执后，使用 `package-mobile-notice-removal-frontend-activation.py` 绑定包、编译静态文件和回执生成独立切换脚本。
4. datax-01 切换前校验前版成功报告、节点身份、候选镜像实际启动；以完整 Deployment spec 和 resourceVersion 为条件只替换前端镜像。
5. 核对两副本镜像、静态文件、HTTP 及其余 Deployment/Service 未改变；最后刷新手机及桌面浏览器验收。

新切换不依赖历史 `new_image_not_observed_in_kubelet_cache` 失败报告；NodeStatus.Images 有数量上限，因此继续采用 `imagePullPolicy=Never` 的临时检查 Job 获得实际执行依据。没有 Gateway 更新、数据库迁移或 OA 回调。

## 已完成本地检查

- 封存的 940 个构建输入中仅一个运行时文件改变。
- `pnpm check`（完整 ESLint + TypeScript）通过；修改页面在规范化 Windows 换行后通过 Prettier，规范化前后文字及代码相同。
- Chromium 渲染验证 7 个视口、14 个新旧对话页面状态：1440×900、320×700、390×844、412×915、640×360、767×900、768×900。检查桌面完整红字、移动隐藏且无占位、无水平溢出、可编辑输入框、手机导航打开关闭；无页面异常或控制台错误。
- 浏览器使用本地模拟 API，不调用模型。Browser plugin not available，因此复用现有 Playwright。首次开发页冷编译超时，编译完成后完整重跑全部通过。
- 既有预载测试 9 项通过；新切换包装器 15 项合成检查通过。实际新包在 Windows 和无网络的 Linux Python 3.7.17 环境各通过 9 项检查，包含真实包摘要/层校验、两种文件系统余量、错误主机和一次性记录拦截。
- 候选镜像独立启动、SSO 模拟跳转、静态资源和本地 HTTP 均通过；完整 OCI 校验通过。编译后核对桌面提示、GLM 用量等待/更新提示、问数进度标记。

## 本轮基线和交付包

用户于 2026-10-24 从 datax-01 贴回只读结果：Frontend UID 与 227 基线一致，generation/observed 227，desired/updated/ready 均为 2，镜像仍为 `b37036e9…`。该命令没有提供完整 spec；激活工具执行时仍需重新读取并验证 spec。

- 完整包目录：`C:/Users/15285/Downloads/portal-mobile-notice-removal-20261024-r1/`。
- 镜像 manifest：`sha256:0af4bb16a0924a02f6cfcb927c137642329b5831d01a5eded8c4f61ee8ad43f7`。
- 镜像 config：`sha256:536ff74930ed2ff55dbc5f00f1b337a7c6f1e99850d764cb4937a0d4f019eee9`。
- 完整包 127,313,408 字节（约 121.4 MiB），SHA256 `76dec78d40d44524ef88fcfb98b8f790596bfa02606d5af5553e32e6c67abebd`。
- 构建、源码、静态资源和包清单分别记录在同名前缀的 build/source/assets/package JSON 中。

## 预载回执与切换工具

已收到 datax-02 与 ecs-zdz-pro-0015 的真实预载成功摘要：import、unpack、CRI 均 PASS，manifest/config 一致，旧引用保留。原摘要记录在 `mobile-notice-removal-frontend-preloads-field-20261024-r1.json`；它们由操作员贴回，并非本地直接读取节点报告。

- datax-02 报告：72,022 字节，SHA256 `4e566c08c1b0b9982f50e474f5056b7ef260bd070bc15ca4cdb8482bc2cecc2f`。
- ecs-zdz-pro-0015 报告：66,411 字节，SHA256 `c877c83e25f0f2c81b318188376b4b436de54131310ffc28d7626de054e0b511`。
- 切换工具：`C:/Users/15285/Downloads/activate-portal-frontend-mobile-notice-removal-20261024-r1.py`，79,291 字节，SHA256 `953501d3928298f2be015d1d9d08937efb40ebe87e5d6a4e8d710581ef80ee95`。
- 实际生成工具通过 15 项合成检查；无网络 Linux Python 3.7.17 中通过文件摘要、主机及内嵌模块加载、两份回执绑定、基线与目标镜像校验、错误主机在任何写入前拒绝的检查。

将切换工具复制到 datax-01 `/home/cnooc/` 后执行：

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-frontend-mobile-notice-removal-20261024-r1.py --activate-frontend-mobile-notice-removal-once
```

本次仅滚动两个前端副本，generation 227 → 228。以下现场记录已确认切换完成，不需要重跑上述命令。

## 现场切换已通过，浏览器显示待确认

操作员贴回状态 `FRONTEND_MOBILE_NOTICE_REMOVAL_RUNTIME_ACCEPTED_BROWSER_PENDING`，`exitcode=0`、`stage_passed=true`。两个新副本分别在 datax-02 和 ecs-zdz-pro-0015 通过源码、三个静态资源和 HTTP 检查，临时 Job 也已实际执行候选镜像。仅发出一次前端 patch，其他 Deployment 和 Service 保持原样。

- 操作：`1912b2e76fa64581ac6cfa171513330a`。
- Frontend 228 spec SHA256：`2949a3564a7de10eb3630e82bfaa62c1829664b86be35f566abe6ddab304ddf1`。
- 原报告：`/var/lib/portal-frontend-mobile-notice-removal-activation-1912b2e76fa64581ac6cfa171513330a/REPORT.json`，32,173 字节，SHA256 `584fccd523377cda7ff4e8f7c1321d7f17a8ee3aff5a9626a95a06558258e41e`。
- 完整贴回摘要保存在 `mobile-notice-removal-frontend-activation-field-20261024-r1.json`；这是操作员提供的证据，本地未直接读取服务器原报告。
- 新源码锁 `production-source-lock-20261024.json` 继承后端已验收记录，前端 940 个构建输入中仅主对话 page.tsx 有内容变化。原构建字节及 Git LF 规范化摘要分别保留。

用户浏览器刷新后的手机无红字、桌面保留红字仍待确认；运行检查不替代这一显示验收。

归档后以 Python 3.12.14 执行 `python -B deploy/enterprise/verify-release-source-20261023.py`，2073 个锁定源文件通过：Gateway 582、Runner 551、Frontend 940。校验器默认使用新 20261024 锁；检查旧提交时显式指定相应历史 `--lock`。最初本机 Python 3.11 无法解析后端既有的 Python 3.12 泛型语法，换用项目适用版本后完整校验通过，没有修改后端源码。
