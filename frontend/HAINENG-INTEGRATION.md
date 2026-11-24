# 海能 B 聊天联调

B 使用本地 React 聊天页，不再嵌入海能旧网站。当前支持普通文字问答、流式回复、多轮会话及停止接收。切换 A/B 保持组件挂载；刷新会清空本地消息。未启用输入组件中的模型、附件、知识库和语音 Mock 功能。

## 前端服务的运行时环境变量

- HAINENG_SESSION：有效海能 SESSION 原始值，仅保存在服务端，不要使用 NEXT_PUBLIC_ 前缀或提交仓库。
- HAINENG_TEST_USER_IDS：允许使用该测试会话的平台用户 ID，逗号分隔。只能配置与该海能身份对应的测试人员；默认拒绝所有用户。
- HAINENG_CHAT_URL：可选，默认 https://heq.cnooc.com.cn/agent/unified-entrance/v1/unifiedChat。

这是单身份联调模式，不是生产单点登录。SESSION 过期需更新运行时环境并重启前端服务。正式上线应改为每个用户独立建立海能会话。

## 部署

重新构建 frontend，给实际运行的 Next.js 进程/容器注入上述变量，同时更新 Nginx 的 /api/haineng/ 路由，优先于 /api/ 网关通配规则。该路由必须转到 frontend:3000，关闭缓冲并保留 Host。

Node 请求不使用 curl --resolve。需给前端容器配置正确的 DNS；联调可经运维确认后配置 extra_hosts: ["heq.cnooc.com.cn:10.74.5.6"]，保持 URL 域名和 TLS 校验，不要把 URL 改成 IP。代理环境也需在实际容器中确认。

## 验收

登录白名单平台用户，切到 B，输入问题，确认正文逐段出现且正常结束。继续追问确认复用 sessionId。切回 A 再切 B，确认消息和草稿保留。测试会话过期、非白名单用户、提前断流和停止接收；停止接收不保证海能取消模型计算。
