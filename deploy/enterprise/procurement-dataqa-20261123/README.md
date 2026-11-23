# 采购报告直接返回 + Data QA 新应用：合并更新

本包替代 `procurement-md-delivery-20261123-r1.zip`。只执行合并包，避免滚动两次。
尚未部署；本地回归测试不代表新应用的真实 SSE 响应已通过验收。

## 改动

1. 采购：取消 Markdown 报告内容校验，不再按标题、表格、风险、结论、材料名称
   拦截。当前任务成功完成并发布报告后原样返回。保留文件归属、原任务关联、
   输入快照、路径和传输完整性检查；报告必须为非空 UTF-8 文本，最多 2 MiB。
   API `succeeded` 不是业务审查“通过”。不更改数字员工审查提示词。
2. `datalake_data_qa`：通过配置使用
   `http://10.77.124.142:9080/appforge/openapi/v1/InvokeApp/app-jeg6ud59`，
   请求 `id=app-jeg6ud59`、`inputs={"hytoken":"","hyaccount":""}`，
   `query` 来自实际提问，POST、Bearer 认证、Accept 为 `text/event-stream`。
   `conversationId` 保持现有按用户和线程生成的 `SquareWorkflow_…`，
   不固定复制示例会话号。复用当前 SSE 解析器。

新密钥不在包内。执行时在服务器终端隐藏输入，脚本通过 stdin 创建独立、
不可变 Kubernetes Secret，再将 Gateway 的专用 API Key 环境变量引用改到新 Secret。
不覆盖共享密钥，不在命令参数、控制台或包内保存密钥。旧 Secret 保留。

更新一个采购 Python 文件挂载、克隆当前配置 ConfigMap 并仅更新 Data QA 的四个连接配置字段，同时更新四个专用环境变量；一次 Gateway
滚动更新，不构建镜像、不更新 Nginx/前端、不迁移数据库。保留其余环境变量，
包括 DataLake Retrieve 已配置的超时，以及采购 3600 秒、Bash 80/100 限制。

## 执行

上传 zip 至 `/home/cnooc`。暂停新的采购审查提交，并尽量避开业务高峰。
root 终端执行（校验和文件与 zip 同放此目录）：

```bash
cd /home/cnooc
sha256sum -c procurement-dataqa-combined-20261123-r4.zip.sha256
unzip -q procurement-dataqa-combined-20261123-r4.zip
/opt/deerflow-tools/bin/python3.12 \
  procurement-dataqa-combined-20261123-r4/update.py \
  --execute --wait-seconds 7200
```

预检完成后出现“请输入新 Data QA 接口密钥（不回显）”，输入对应新应用密钥。
脚本检查所有 Gateway 当前代码与配置一致，使用 UID/resourceVersion 比较后
一次提交 Deployment 改动，再核对所有新副本的有效 URL、应用 ID、密钥指纹、
会话前缀和原超时。旧工具级 app_id 和密钥引用会在克隆配置中更新，其他配置语义保持一致。若代码基线改变或存在运行中的采购
任务，停止并输出原因，不强行修改。

提交滚动更新后不要重复 `--execute`。超时或中断后用输出的真实 record 目录：

```bash
/opt/deerflow-tools/bin/python3.12 \
  /home/cnooc/procurement-dataqa-combined-20261123-r4/update.py \
  --status-record /opt/df-procurement-dataqa-r4-实际记录目录 \
  --wait-seconds 7200
```

## 更新后

1. 在开启数据湖工具的会话中测试“查询作业井基本信息表中设计井深最大的2口井”，
   核对新应用实际 SSE 是否被现有解析器正常接收。发布脚本不自动调用上游。
2. 已失败的采购任务 `IMP-12345` 仍保存原失败结果，不会自动重跑或改状态。
   可随后单独恢复已有报告的交付，无需重复调用模型。

本包不含生产业务材料与密钥。`fixtures/` 仅用于本地测试，不部署到 Gateway。
本地测试：`python -m unittest discover -s . -p 'test_*.py' -v`。

## r3 就绪等待

发布前默认最多等待 300 秒（可用 `--ready-wait-seconds` 调整，最大 1800 秒），
每 10 秒重新检查状态，输出就绪/可用副本数。仍要求全部副本 Updated、Ready、
Available，且实际 Pod 全部 Ready 后才继续。持续未恢复时报
`PREFLIGHT_READINESS_TIMEOUT`，不提交变更。等待期间 Deployment 配置变化或
被暂停时停止，不自动恢复、不降低副本数。状态记录保存在 `readiness.jsonl`。

## r4 Secret 命名修复

Secret 名称改用记录路径的 SHA-256 十六进制摘要，避免随机目录后缀的下划线进入 Kubernetes 名称。创建前验证名称。失败时将已遮盖密钥的错误保存到记录目录的 `secret-create.PRIVATE.err`，不输出密钥。
