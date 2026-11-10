# SkillHub 入口鉴权生产验收记录（2026-11-06，r2）

操作员回执状态：`SKILLHUB_EDGE_AUTH_ACCEPTED_BROWSER_PENDING`。操作编号 `d6c641977c0445cba97999e6926d4ec7`，观测时间 2026-11-06 12:12:17（北京时间）。

本次入口配置已生效：匿名 SkillHub 页面返回 302 到登录页；匿名标签、技能查询、SSE 和会话 bootstrap 返回 401；伪造 Bearer 的标签、技能查询和 SSE 也返回 401。入口健康检查为 200，原生 SkillHub 鉴权在变更前后均通过复核。

工具完成一次 ConfigMap CAS 更新及 Nginx 平滑重载，保留同一 master 并观察到新 worker。旧 PPT 新运行入口仍返回 409；Service 后端与受保护 Deployment 保持不变。未创建或删除 Pod，未操作沙箱、未直接查询数据库、未发送模型请求。鉴权与健康 HTTP 请求可能使现有应用访问自身数据库；已有旧 worker 连接可能继续存续。

当前配置哈希：

- 实际运行文件：`2e508ad18f4de362c7ca387ca93126296ab9c236569a7dba48718003c66d739e`
- 后续启动用 ConfigMap 模板：`388d96db7093983c7428b7731d12766befc7a24e26b3c64475cc170b3079fb21`

完整私有回执从操作员粘贴的 JSON 精确重建为 `REPORT-RECONSTRUCTED.json`：5,051 字节，SHA256 `eb7f14080c002910835e6bfcc87352bf0825927d3bdcca46147971dba35a57e4`，与操作员返回的 seal 一致；这不是从服务器下载的文件。归档仅移除 stdout 后加的 `report_bytes` 和 `report_sha256` 两项，原始操作员输出及来源哈希见 `OPERATOR-ACCEPTANCE.json`。

浏览器登录态和有效原生 Bearer 的业务验收仍待完成。本记录不包含公开域名复测结果。原 `QUALIFICATION.json`、`DELIVERY.json` 保持发布前的历史状态，未覆写。
