# 首页提示与移动端间距联合修正

用户要求将尚未发布的移动端间距修正与桌面首页提示调整合并预载。这是基于 Frontend 228 构建的联合版本，替代未上线的 `mobile-chat-spacing-20261024-r1`，无需单独发布前者。操作员现已贴回成功切换回执：Frontend 229 运行验收通过，生产浏览器验收待确认。

## 行为

- 桌面新对话首页：完整红字位于第一行品牌/导航内容（智启海油、海能 AI）下方。
- 按回车提交后：跟随现有 `isWelcomeMode` 立即卸载提示，不等待服务器创建线程或模型回复。
- 历史对话：无红字和原先的红字顶部占位；桌面对话标题栏恢复顶部坐标 0。
- 移动端：首页和对话均不显示红字；消息列表去掉额外 40px 顶部占位，直接衔接正常文档流中的标题栏。

## 精确源码范围

从 `frontend-mobile-notice-removal-20261024-r1a` 封存输入（现场 Frontend 228）生成候选。940 个构建输入仅改变主对话路由的 `layout.tsx` 和 `page.tsx`：将提示从共用布局移到页面的欢迎状态标题栏中，并把消息列表顶部间距限定为 `md:pt-10`。

不修改认证、提交请求、流式协议、后端或模型逻辑。生产源码锁 `production-source-lock-20261024.json` 已依据成功切换回执更新为 229；此前 228 锁保存在 `production-source-lock-20261024-frontend228.json`。

## 验证与发布

本地界面验证使用临时 QA 目录和模拟 API；Browser plugin not available，使用现有 Chromium Playwright。入口为 `http://127.0.0.1:3154/workspace/chats/new`，覆盖新对话 → 回车 → 新线程请求等待期间 → 模拟回答 → 历史对话 → 新对话。页面和截图证据位于本机 `C:/Users/15285/.codex/tmp/chat-notice-layout-20261024/`。

测试夹具对流式响应提供 SDK 所需 `Content-Location`，并使用第二个线程 ID 保留长回答历史，不把模拟创建接口对第一线程的更新误认为页面回归。开发服务器仅在临时本地进程使用测试认证模式，发布镜像不包含这项环境配置。

完整 lint/TypeScript 检查、两份修改文件的 Prettier 检查已通过。切换模板继承已验证的部署、摘要和并发更改保护，15 项合成测试通过。正式激活工具将绑定新的两台节点预载回执，当前无生产操作。

320×700、390×844、640×360、767×900、768×900、1024×600、1024×512、1440×900 共 8 个尺寸的上述流程通过。所有视口验证了提示状态、按回车后立即隐藏、历史对话无提示、返回首页后按屏幕宽度显示、长回答滚动、输入框可编辑和无文档水平溢出；手机导航打开/关闭通过，未观察到页面异常或控制台错误。真实 iOS WebView 尚未验证。

正常桌面 1440px 下：首页标题栏为 0–68px，红字为 68–108px；进入对话后标题栏为 0–48px，滚动区顶部为 40px。手机390px下标题栏底部与滚动区顶部均为76px。1024×512桌面横屏原有的58px主区域内边距保留，测试断言按已有横屏样式计入这项内边距，并单独复核该视口及正常桌面通过；没有借本次变更调整既有横屏布局。窄桌面（768px）既有品牌栏/按钮拥挤仍在本次范围之外，红字完整换行且不覆盖正文或输入框。

候选 ARM64 构建、独立容器 HTTP/静态资源及完整 OCI 层与 diffID 校验通过。实际交付包在 Windows 和 Linux Python 3.7 隔离环境分别通过 9 项检查（主机绑定、容量不足拒绝、一次性标记、保留当前镜像引用及真实归档摘要），未调用生产服务。

交付目录：`C:/Users/15285/Downloads/portal-chat-notice-layout-20261024-r1/`。完整归档为 `frontend.chat-notice-layout.full.oci.tar`，127,322,112 字节，SHA256 `fbd02ec9f6fe4c75aac656ec316401711d745dd7d78e97da59bf3d354a60597d`。

- 镜像：`deerflow.local/deer-flow-frontend:20261024-chat-notice-layout-r1`
- Manifest：`sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298`
- Config：`sha256:77df652bd3e42b47a6a38a606c7e65c30ea53af0f948a80a35c36908866759df`
- 源码封存摘要：`97f3d3d229fca1becc5c78d1f05a471034f835a7f00f77a928b39f0b9063a011`

构建、编译产物和交付摘要分别归档为同前缀的 `build`、`assets`、`package` JSON。生产前端已切换；真实 SSO、生产浏览器和 iOS WebView 交互验收仍待确认。

## 现场预载与切换工具

操作员贴回两台预载摘要，均为 `COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED`、exitcode 0，导入、展开及 CRI 可见性通过，旧镜像引用保留。摘要归档为 `chat-notice-layout-frontend-preloads-field-20261024-r1.json`；这是操作员提供的回执，不能表述为本机直接读取节点报告。

- ecs-zdz-pro-0015：操作 `351624066ce7423a8f17c73df54a6489`，报告 71,647 字节，SHA256 `df96f400405f3285041d645baa108efa72179a5bb4fa325e71290d25ab03b031`。
- datax-02：操作 `5a2f456d2eb243fca9c0c057a7b75b7d`，报告 77,259 字节，SHA256 `d460e881844980b5b95b3f8b0873db64c2547c2eddb8af3e94f7fb0506dc9d28`。

已生成 `C:/Users/15285/Downloads/activate-portal-frontend-chat-notice-layout-20261024-r1.py`，79,269 字节，SHA256 `613a1b384fc76a6a28ae509f4b8c5d6900de7c554d91321da33964d3e42a05a5`。它绑定 Frontend 228 的已验收报告及两台预载摘要，重新核对节点身份和状态，并用两台 `imagePullPolicy: Never` 检查 Job 运行候选镜像，然后以完整 spec/resourceVersion 条件补丁仅替换前端镜像，核验两个副本和静态资源，保留其他服务配置。

实际生成工具在 Windows 通过 15 项合成回归；Linux Python 3.7 通过 14 项，Node 运行探针一项因该 Python 镜像不含 Node 跳过（本机已通过）。另在断网 Linux 容器验证实际文件摘要、内嵌代码、真实回执绑定、228 基线、候选镜像及错误主机在写入前拒绝。测试不连接生产集群。切换包记录见 `chat-notice-layout-frontend-activation-package-20261024-r1.json`。

已执行的历史命令（成功后不要重跑）：

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-frontend-chat-notice-layout-20261024-r1.py --activate-frontend-chat-notice-layout-once
```

## 现场切换结果

操作员提供的完整摘要已归档为 `chat-notice-layout-frontend-activation-field-20261024-r1.json`。它来自本次附件，未被表述为本机直接查询生产环境。

- 状态：`FRONTEND_CHAT_NOTICE_LAYOUT_RUNTIME_ACCEPTED_BROWSER_PENDING`，exitcode 0，stage_passed true。
- 前端：228 → 229，spec SHA256 `246ac63a5af90c03950bf5c16b4dcb88c5bd267fed9028e2754be696787fd40f`。
- datax-02 和 ecs-zdz-pro-0015 均通过候选镜像实际启动核对；两个更新后的前端副本通过文件摘要、3 个静态资源和 HTTP 307 登录跳转检查。
- Gateway 386、PPT Runner 55、Nginx 116、Provisioner 85 的部署绑定前后一致，Service 保持不变；无模型请求、无数据库迁移。
- 操作 `83cdf8a87e0747e7b8c1ffac4c2fcd6d`，服务端报告 `/var/lib/portal-frontend-chat-notice-layout-activation-83cdf8a87e0747e7b8c1ffac4c2fcd6d/REPORT.json`，32,149 字节，SHA256 `b2f81c39253c6238b589cdf86d2a784159c7e72eca203a98d5cdc2436f476594`。

归档时重新核对 940 个封存前端输入与工作树内容（仅记录 Git LF 规范化）；相比 228，仅上述两个页面文件改变。源码锁更新为实际已上线的镜像摘要。生产浏览器验收仍为 pending：需要刷新后确认桌面首页提示位置、提交后及历史对话无提示、移动端无提示且无额外顶部间距。
