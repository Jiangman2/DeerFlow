2026-10-17 同事更新完成后的只读基线与源码采集（R2）

用途
先采集 deer-flow 的 Gateway、Frontend、ppt-master-runner、Provisioner 当前运行
基线；确认采集完整且稳定后，再读取用户指定的 /opt/DeerFlow 源码目录。
上一份 6960ea5a...91af5e 报告继续作为更新前历史参考，不预设本次镜像或源码相同。
本包修正了终态 Failed/Succeeded Pod 的分类，保留终态记录及其漂移检查；
ContainerStatusUnknown 不表示已验证节点上的进程清理。

执行
将压缩包中的以下三个文件放到 datax-01 的 /home/cnooc/：
  run-portal-ppt-refresh-20261017-r2.sh
  portal-ppt-baseline-20261017-r2.py
  portal-source-directory-20261017-r2.py

执行一次：
  sudo bash /home/cnooc/run-portal-ppt-refresh-20261017-r2.sh

入口将两个Python文件复制到新的root私有目录后验证SHA256，再执行固定采集步骤。
保持脚本原样；不要改镜像名、校验值、失败条件或生产文件来让采集通过。

返回
结束后终端打印：
  download_evidence=/home/cnooc/portal-ppt-refresh-20261017-r2-XXXXXXXX/EVIDENCE.tar.gz

下载该 EVIDENCE.tar.gz 并发回本聊天，同时带上终端摘要。
该私有证据包包含运行基线报告，以及本机源码快照报告和已允许范围的源文件。
若失败，同样返回已生成的证据与摘要；先审查失败原因，再决定是否重新执行。
脚本会保留每次结果，不覆盖旧证据，也不会自动重试。

范围和限制
运行基线只做固定 Kubernetes GET 和源码摘要探针，覆盖全部 Ready 实例，最多4路
并发。新增固定文件摘要用于核对 shoptype、OA/DNS/XLSX 以及前端构建输入。
终态记录保留在报告中，活动或未决实例仍受副本数、身份和前后稳定性检查。

本机源码步骤只读 /opt/DeerFlow 内列明的应用源码、构建输入和静态资源；双次清单
核对防止采集中途修改。跳过运行数据、环境/凭据文件、依赖缓存、链接和未授权子挂载，
并记录缺失及排除项。.npmrc 只允许经过严格校验的公开hoist配置，否则只留摘要。
完整范围以源码采集器及其报告为准；没有被收集的内容不会被宣称已经验证。

本次不连接业务数据库、不调用模型/网站、不构建镜像、不导入镜像、不迁移、不重启，
不修改生产配置或源码。持久写入仅为 /var/lib 与 /home/cnooc 下的新私有证据目录。
读取目录源码不等于证明它与运行镜像的完整源码树一致；所有差异都交回逐项审查。
数据库实际迁移头、镜像默认启动信息、Agent Host/Sandbox与企业ARM64端到端仍需
后续核对；本包始终保持 ready_for_build=false、ready_for_rollout=false。

本地资格与具体哈希见交付目录的 SHA256SUMS 和发布准备目录的验证记录。
旧R1执行入口和旧报告不变；本轮请使用上方R2入口。
