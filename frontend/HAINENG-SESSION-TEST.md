# 临时个人 SESSION 网页联调

仅用于 feat/haineng-session-test，基于 059ed7275。不要将此临时认证机制合并回 feat/frontend-haineng-merge。

## 获取当前登录账号的内部 ID

1. 使用你自己的账号登录真实 DeerFlow 网页。
2. 打开 F12 → Network，刷新后找到 GET /api/v1/auth/me；响应中的顶层 id 就是平台内部 ID。不要使用用户名、邮箱、组织 ID、海能账号或海能 sessionId。
3. 如果列表中没有该请求，可在当前平台网页的 Console 执行：

```javascript
fetch('/api/v1/auth/me', { credentials: 'include', cache: 'no-store' })
  .then(async r => { if (!r.ok) throw new Error('请先登录平台'); return r.json(); })
  .then(user => console.log('当前 DeerFlow 内部 ID:', user.id));
```

只需要复制 id，不要分享 Cookie、access_token 或完整响应。该地址与现有 AuthProvider / 服务端认证使用的用户接口一致。

## 认证规则（全部只在 Next.js 服务端读取）

- HAINENG_TEST_AUTH_ENABLED：必须为字符串 true 才开启，缺省、false 或其他值均关闭。
- HAINENG_SESSION：浏览器成功 unifiedChat 请求中 Cookie 的 SESSION 原始值，不含 SESSION= 前缀，不带引号、空格或换行。
- HAINENG_SESSION_USER_ID：上述平台内部 ID，且与该个人海能身份对应。

只有开关开启、用户匹配、凭据非空才注入 Cookie: SESSION=...。不匹配用户完全不使用该凭据；缺失凭据保持无凭据的上游请求，由原有代理准确返回上游认证错误。非法 Cookie 格式不会发送，错误不包含凭据内容。

这不是自动 UAP 认证，也不扩展到其他用户。平台登录、Origin、签名续聊令牌及归属校验全部保留。HAINENG_CONVERSATION_SECRET/BETTER_AUTH_SECRET 继续使用原有稳定的平台密钥，不替换为 SESSION。

## K8S：建立专用 Secret

在可信的 Bash 终端运行，替换 namespace。命令不会把 SESSION 放在命令参数或历史中；不要开启 set -x，不要将生成的 Secret YAML 打印或提交 Git。

```bash
read -r -s -p '输入个人海能 SESSION 原始值: ' sessionValue
printf '\n'
printf '%s' "$sessionValue" | kubectl -n <namespace> create secret generic haineng-session-test \
  --from-file=HAINENG_SESSION=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
unset sessionValue
```

Secret 与实际运行 Next.js 的 Deployment 必须在同一个 namespace。K8S Secret 的 base64 不是加密，请沿用集群既有的 RBAC 和 Secret 管理方式。不要配置到 Python Gateway 或浏览器公开环境变量中。

## K8S：修改前端 Deployment 环境

先部署本分支构建的前端镜像。保存以下不含凭据的 patch 到一个配置文件，例如 session-test-patch.yaml，替换实际前端容器名和平台内部 ID：

```yaml
spec:
  template:
    spec:
      containers:
        - name: <FRONTEND_CONTAINER>
          env:
            - name: HAINENG_TEST_AUTH_ENABLED
              value: "true"
            - name: HAINENG_SESSION_USER_ID
              value: "<YOUR_DEERFLOW_INTERNAL_ID>"
            - name: HAINENG_SESSION
              valueFrom:
                secretKeyRef:
                  name: haineng-session-test
                  key: HAINENG_SESSION
```

```bash
kubectl -n <namespace> patch deployment <FRONTEND_DEPLOYMENT> \
  --type=strategic --patch-file=session-test-patch.yaml
kubectl -n <namespace> rollout status deployment/<FRONTEND_DEPLOYMENT>
```

保留原有 Origin、签名密钥、网络和 Nginx 配置，本次不需要修改它们。

更新 Secret 后，环境变量不会自动更新到运行中的 Pod，需要：

```bash
kubectl -n <namespace> rollout restart deployment/<FRONTEND_DEPLOYMENT>
kubectl -n <namespace> rollout status deployment/<FRONTEND_DEPLOYMENT>
```

## 验收与收尾

- 从正常工作区切到“对话”，不要使用 /preview/chat（该页始终是模拟回复）。
- 用对应平台账号发送问题并继续追问，确认真实流式回复、签名多轮及最近对话恢复。
- 用另一平台账号测试，确认不会借用个人 SESSION；如果海能要求登录，该账号仍得到上游认证错误。
- SESSION 过期仍会得到明确认证错误，不自动刷新。
- 测试结束删除临时环境变量并等 Pod 更新后，再删除本次专用 Secret：

```bash
kubectl -n <namespace> set env deployment/<FRONTEND_DEPLOYMENT> \
  --containers=<FRONTEND_CONTAINER> HAINENG_TEST_AUTH_ENABLED=false HAINENG_SESSION- HAINENG_SESSION_USER_ID-
kubectl -n <namespace> rollout status deployment/<FRONTEND_DEPLOYMENT>
kubectl -n <namespace> delete secret haineng-session-test
```

本次只修改认证模块、测试和说明；不修改 Python 后端、Nginx 或 K8S 网络配置。
