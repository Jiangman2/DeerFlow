中国海油通用智能体 / 海能 Work + PDF 预览，合并候选构建包 R1
=========================================================

这是离线候选镜像构建包，不是可直接 rollout 的成品镜像包。
不包含生产发布脚本；须先发回 ARM64 构建结果，再校验镜像预装、回滚和活动任务。

变更：
1. 修复正常 WeasyPrint 压缩对象流 PDF 被误拦截；保留安全扫描、原文件和下载策略。
2. 预览区检查 HTTP 状态，错误显示友好提示和重试，不再显示 JSON。
3. 渠道飞书显示为海能 Work，使用用户给定图标；连接提示使用中国海油通用智能体。
4. 保留 feishu provider ID、App ID/Secret、用户绑定和全部现有配置。

基线固定为用户实际查询的 Gateway PPT Master R15 + Frontend conversation-runtime4。
只应用精确匹配的局部源码修改，不覆盖整个模块，不升级其他组件。
未匹配到基线会停止构建，不得跳过。保留现有 Python/Node 依赖和 PDFium。
所有 Dockerfile RUN 断网；不下载任何 Python/npm/apt/浏览器包。

构建前：
- datax-01 必须无 Gateway Pod（包括终止中的 Pod）；不要为构建自动迁移生产。
- 需要上述两种基线镜像已在 datax-01 containerd 中。
- /opt 必须独立于根分区；/opt 至少100GiB、根分区至少20GiB可用。
- 内存可用至少16GiB；节点健康、没有其他镜像导入或构建。
- 隔离 BuildKit：/opt，1个构建并发，CPU150%，内存12GiB，低I/O优先级。

在 datax-01 以 root 执行（路径以实际解压目录为准）：
  cd /opt/deerflow-hotfixes/deerflow-channel-pdf-preview-20260923-r1
  sha256sum -c PACKAGE.SHA256
  python3 verify-package.py .
  chmod 0755 *.sh
  set -o pipefail
  ./build-candidate-datax-01.sh 2>&1 | tee /home/cnooc/channel-pdf-preview-r1-build.log
  RESULT=("${PIPESTATUS[@]}")
  printf 'build_exit_code=%s\ntee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"

输出：/opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r1
包含两个 candidate.oci.tar、两个 base.oci.tar 和各自 SHA256。
脚本不导入候选镜像、不滚动服务、不改生产数据库，不删除旧镜像和缓存。
如中断留下 .partial，请先检查，不要自动重跑或清理。
如失败请发日志；不要用更换依赖、关闭安全扫描或联网安装绕过错误。

本地源码回归：后端441项、渠道前端单测23项、页面回归21项通过。
另用用户 quote.pdf 完成桌面/窄屏预览验收。用户 PDF 不包含在包里。
这些结果不等同于 ARM64 镜像或生产验收；需逐阶段确认。

生产上线范围仅 Gateway、Frontend；Runner/Provisioner/Sandbox/WeKnora/Redis、
渠道 ConfigMap/Secret 及数据库均不改动。活动任务是否可中断要在发布前重新确认。
