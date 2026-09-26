中国海油通用智能体 / 海能 Work + PDF 预览：候选构建包 R2
========================================================

这是离线候选镜像构建包，不是已经生产验收的镜像，更不是 rollout 包。
仅 Gateway、Frontend；不涉及 PPT Runner、Sandbox、Redis、WeKnora 或数据库。

R2 根据用户提供的 20260923T113615Z 源码审计重建精确补丁，不读取本地 HEAD。
Gateway 基线：20260923-enterprise-arm64-rc15-ppt-master-v660-r15
Frontend 基线：20260921-enterprise-arm64-rc15-conversation-runtime4

三处修正：
- manager.py：保持生产旧版锁实现，只增加产品名常量引用并修改两条绑定提示。
- en-US.ts / zh-CN.ts：保留已在生产改好的产品名，只修改飞书渠道描述。
- 其他16个文件继续沿用已审核的R1功能修改；已生效的常量作为哈希核对项保留。

Gateway 每个目标文件先检查审计SHA256、精确替换及目标SHA256、Python语法；
Frontend 每个目标文件先检查审计SHA256、精确替换及目标SHA256。
所有文件准备成功后才写入候选镜像。源码改变则停止，不能强行绕过。
两份基线镜像先分别完成 source-check 阶段，再运行 Gateway 原生PDF冒烟与前端build。
不要把源码MATCH、静态PASS或校验和通过当成生产可发布证明。

离线与资源边界：
- 不安装新依赖；全部Dockerfile RUN使用 --network=none。
- datax-01不得有Gateway Pod（含终止中）；本脚本不会迁移生产服务。
- /opt必须独立，至少100GiB可用；根分区至少20GiB，可用内存至少16GiB。
- 隔离BuildKit在 /opt/deerflow-buildkit/channel-pdf-preview-r2；
  CPU150%、内存12GiB、最大并发1、低I/O优先级。
- 输入镜像须预先存在；校验导出的镜像manifest/config身份与审计记录一致。

保留旧R1目录与失败记录，不修补原包，不删除原来的partial或缓存。
R2使用全新目录 /opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r2。
因此会重新导出两份基线；这些是独立于旧失败构建的回滚镜像存档。

解压并验证后，在datax-01以root执行：
  cd /opt/deerflow-hotfixes/deerflow-channel-pdf-preview-20260923-r2
  sha256sum -c PACKAGE.SHA256
  python3 verify-package.py .
  chmod 0755 *.sh
  set -o pipefail
  ./build-candidate-datax-01.sh 2>&1 | tee /home/cnooc/channel-pdf-preview-r2-build.log
  RESULT=("${PIPESTATUS[@]}")
  printf 'build_exit_code=%s\ntee_exit_code=%s\n' "${RESULT[0]}" "${RESULT[1]}"

成功输出 channel_pdf_candidate_build=PASS 后，仍需反馈日志并验证镜像、
候选与回滚镜像预装、活动任务和服务健康，再另行准备rollout命令。
本包不导入候选镜像，不重启/缩放服务，不改变渠道配置或Secret。
中途失败保留日志及partial，不自动删除，不改为联网安装或关闭安全扫描。

用户审计中的完整源码与quote.pdf均不打入包；BASELINE_LOCK只包含文件/镜像哈希。

后续独立运维脚本（不包含在已交付、不可变的R2构建归档中）：
- validate-candidates.py：四节点临时候选验证，见 CANDIDATE_VALIDATION.md。
- rollout-candidates.py：通过候选验证后的短维护切换及精确回退，见 ROLLOUT.md。
  仅更新两个主容器镜像，临时使用Gateway Recreate策略并恢复；不绕过活动任务检查。
