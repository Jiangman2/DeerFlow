# 海能集成：第一阶段多用户功能

## 当前行为

正常登录、未禁用的 DeerFlow 用户均可打开“对话”并请求 POST /api/haineng/chat。平台登录认证及 Origin 校验保持开启，不再使用联调用户白名单，也不读取固定个人海能 SESSION。

服务端认证扩展点：src/core/haineng/server/auth.ts 的 resolveHainengAuthentication(user)。第一阶段默认返回空认证头，真实请求海能；上游返回 HTTP 401/403、登录重定向或 HTTP 200 + ret:302 时，前端收到明确认证错误，不伪造问答成功。上游没有允许匿名调用时，需要海能提供正式授权方式才能得到真实回答。

第二阶段仅预留该接口：后续按平台用户独立获取、校验、更新海能凭据。禁止将个人 SESSION 配置成所有用户的公共凭据；旧的 HAINENG_SESSION 和 HAINENG_TEST_USER_IDS 配置不再生效，可从部署环境移除。

## 平台会话隔离

浏览器继续使用 sessionId 字段，但其中保存的是平台签名的会话令牌。令牌绑定当前 DeerFlow 用户，服务端校验所有者后才还原海能会话 ID。不能把甲用户令牌用于乙用户，也不能提交原始上游 ID绕过归属校验。

配置 HAINENG_CONVERSATION_SECRET（至少32字符），或复用现有 BETTER_AUTH_SECRET（至少32字符）。这是平台自己的签名密钥，不是海能 SESSION。生产多实例必须使用相同且稳定的签名密钥。旧版本保存的未签名海能 sessionId 无法继续使用；原历史内容仍可查看，请新建对话。更换签名密钥也会使旧续聊令牌失效。

## 最近对话

侧栏只保留一份最近对话列表，合并工作与海能记录，海能标题显示“对话·首条问题”，工作标题保持原样。保持演示和置顶工作记录优先，其余按更新时间排序。点击工作记录切到工作，点击海能记录切到对话。

当前海能记录仍按 DeerFlow 用户 ID 分开保存在本浏览器 localStorage（预览与真实环境分开），没有迁移到服务器数据库。不支持跨设备同步；清理浏览器数据会移除这些记录。同机存储命名空间不是服务端访问控制，服务端的续聊归属由签名校验保证。

## 部署

- HAINENG_APP_ORIGIN：平台完整访问源，如 https://cnoocclawtest.com。
- HAINENG_CHAT_URL：可选，默认 https://heq.cnooc.com.cn/agent/unified-entrance/v1/unifiedChat。
- HAINENG_CONVERSATION_SECRET 或 BETTER_AUTH_SECRET：稳定的平台签名密钥，至少32字符。
- 不配置公共个人海能 SESSION，不需要测试用户白名单。

可叠加 docker/docker-compose.haineng.yaml。前端容器需能够解析和访问海能域名；该文件保留经联调可用的 extra_hosts，HAINENG_HOST_IP 可覆盖 10.74.5.6。海能 URL 保持域名，保留证书校验。Nginx /api/haineng/ 必须转发到 frontend:3000，并关闭响应缓冲，读取超时应覆盖长回答。

仅在开发环境可用的 /preview/chat 仍使用本地模拟回复，不代表上游认证已完成。

## 独立临时联调分支

仅 feat/haineng-session-test 增加按平台用户绑定的可选个人 SESSION 注入。默认关闭，不改变其他用户的流程。参见 HAINENG-SESSION-TEST.md；不要将该临时机制合并回正式集成分支。
