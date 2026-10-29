# V4.1 配置准备与 Gateway 滚动分开执行

用户要求现在不滚动 Gateway，晚些时候统一滚动。本流程取代前一版一步启用命令。
旧 `activate-portal-v41-glm-20261029-r1.py` 保留作历史记录，请勿执行。

## 第一步：现在只准备配置

交付：`prepare-portal-v41-glm-config-20261029-r1.py`，复制到 datax-01 `/home/cnooc/`：

```bash
sudo python3 -I -B -u /home/cnooc/prepare-portal-v41-glm-config-20261029-r1.py --prepare-v41-config-only-once
```

核对已经通过的七项模型测试回执、Gateway 389 基线、实际挂载配置、已安装的适配器和 CA。
在独立短时进程中验证新增模型配置，不向模型服务发起请求。只创建一个新的、不可变的
ConfigMap；不修改原 ConfigMap，不修改 Deployment，也不执行任何滚动、重启或删除 Pod。
新配置暂时不被 Gateway 挂载，即使现有 Pod 因正常生命周期被替换，仍使用原配置。
V4.1 在这个阶段仍未生效，当前默认模型及正在运行的应用配置不变。

准备成功状态：`V41_CONFIG_PREPARED_NOT_ACTIVATED`。
回执及私有备份在 `/var/lib/portal-v41-glm-preparation-20261029-r1`，请完整保留。
若创建回复丢失或检查未通过，保存结果，不重跑、不删除单次标记。

## 第二步：晚些时候挂载并滚动

交付：`rollout-portal-v41-glm-config-20261029-r1.py`。现在仅保存，到计划切换时才执行：

```bash
sudo python3 -I -B -u /home/cnooc/rollout-portal-v41-glm-config-20261029-r1.py --rollout-prepared-v41-once
```

它要求第一步成功，并验证准备回执、文件摘要、同批工具标识、ConfigMap UID 和内容。
重新读取生产 Deployment，使用此刻的 resourceVersion 生成比较并交换补丁，避免使用准备时的
过期版本。仅资源状态导致的 resourceVersion 更新可接受；部署 spec、generation、旧配置或候选
ConfigMap 身份发生变化会在滚动前拒绝，不覆盖期间的新改动。

先执行服务端 dry run，再仅更换 Gateway 的配置卷引用。保持镜像、默认模型、密钥、部署策略
和其他服务不变；只提交一次真实补丁，回复不确定时通过读取确认，不重复提交。
等待并逐个验收 70 个 Gateway 副本及服务端点。此阶段会触发滚动更新，可能中断运行中任务。
V4.1 成为新增可选模型，默认仍是 `deepseek-v4-flash`，使用现有 `GLM_5_3_FLASH_API_KEY`。

第二步单次回执目录：`/var/lib/portal-v41-glm-rollout-20261029-r1`。
若期间准备了其他 Gateway 更新，应以最新基线合并为一次切换计划；不能绕过这个工具的基线检查，
也不能用普通 `kubectl rollout restart` 代替挂载候选配置的步骤。

## 验证与边界

两份交付脚本独立封装固定阶段，不能通过命令行参数将准备脚本改成滚动脚本。
准备脚本不包含 Gateway patch/rollout 实现，命令守卫也拒绝工作负载修改、Secret 读取及任意 exec。
共享的配置验证与 70 副本验收代码继承已验证版本，历史源码及能力验收回执未修改。

`test_split.py` 对两份实际打包脚本执行合成集群测试：Windows 14 项通过、1 项 POSIX 权限检查
跳过；Linux Python 3.7.17 全部 15 项通过。Linux 测试使用无网络、只读根文件系统容器及只读
测试目录，仅在临时目录保存合成回执。包含准备不滚动、延时后重新生成 CAS、70 副本验收、
候选被替换、期间部署变化、文件篡改、回包丢失、最后一个副本失败以及阶段隔离。

这些是工具验证，不是生产执行记录。操作者随后于 2026-10-29 返回第一步成功回执，
归档摘要见 `preparation-field-summary.json`。新配置已创建，Gateway 仍为 389，
`patch_request_count=0`、`gateway_rollout_started=false`、`v41_enabled=false`。
两份交付工具的 bundle 标识均与现场准备回执一致。第二步按用户要求延后，尚未执行。
请保留准备目录及回执，不重复执行第一步。
登录后模型选择与真实业务对话验收仍在实际滚动完成之后进行。
