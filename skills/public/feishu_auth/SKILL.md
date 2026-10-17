---
name: auto-auth
description: 自动鉴权。完成飞书 OAuth 授权登录：拼装授权页 → 用户点击授权 → 回调页展示 code → 用户贴回 code → curl 换 token → 返回本次可用的 user_access_token。本 Skill 不缓存 token，每次需要权限都重新完成一次授权。当用户要求登录飞书/完成授权，或业务报「未登录」「登录已过期」「未绑定日历」时使用。
---

# 自动鉴权（auto-auth）

agent 执行日历/任务/文档操作的**前置鉴权流程**：完成飞书 OAuth 授权登录 → 拿到本次可用的 `user_access_token` → 回到原业务。

## 设计要点

1. **不缓存 token**：不落库、不持久化、不使用 refresh token。每次需要权限都重新走一遍授权，token 只在当次会话内使用。
2. **回调页面接收 code**：授权完成后浏览器跳转到 `login.html` 回调页，页面自动解析并展示 `code`，用户复制贴回对话即可。
3. **所有 API 调用用 curl 一行完成**：无需写 Python 脚本，agent 直接在沙箱 bash 中执行 curl 命令。
4. **state 防伪**：agent 生成随机 state 参数拼入授权 URL，回调页原样返回，agent 可选校验。

## 应用凭证配置（唯一维护点）

| 参数 | 当前值 | 说明 |
|---|---|---|
| `APP_ID` | `cli_aa3b9ac9dfb8dd25` | 飞书开放平台 → 应用 → 凭证与基础信息 |
| `APP_SECRET` | `LYN4U1q1jGyJp37Ba2fmLhgbvwHGMswX` | 报 `invalid_client` 时在此填入新 Secret |
| `REDIRECT_URI` | `https://cnoocclaw.cnooc.com.cn/auth/login/login.html` | 必须与飞书后台「安全设置 → 重定向 URL」登记的完全一致 |
| `BASE_URL` | `https://open.feishu.cn` | 飞书官方公网域名 |
| `AUTHORIZE_URL` | `https://accounts.feishu.cn/open-apis/authen/v1/authorize` | 授权页地址 |
| `SCOPE` | `calendar:calendar task:task:write docx:document` | 不加 `offline_access`；不要加 `task:task:read`（未开通） |

## 工作流程

### 步骤 1：构造授权地址并发给用户

agent 生成随机 `state`（如 `openssl rand -base64 18`），拼装授权 URL：

```
https://accounts.feishu.cn/open-apis/authen/v1/authorize?client_id={APP_ID}&response_type=code&redirect_uri={REDIRECT_URI}&scope={SCOPE}&state={STATE}
```

实际示例（一行构造）：

```bash
STATE=$(openssl rand -base64 18 | tr -d '\n' | sed 's/+/-/g;s/\//_/g'); echo "https://accounts.feishu.cn/open-apis/authen/v1/authorize?client_id=cli_aa3b9ac9dfb8dd25&response_type=code&redirect_uri=https%3A%2F%2Fcnoocclaw.cnooc.com.cn%2Fauth%2Flogin%2Flogin.html&scope=calendar%3Acalendar%20task%3Atask%3Awrite%20docx%3Adocument&state=${STATE}"
```

把输出的授权地址发给用户，提示：「请点击链接完成授权，授权后页面会显示一个 code，请复制发回给我。」

### 步骤 2：用户在浏览器完成授权

用户点击授权地址 → 飞书授权页 → 点同意 → 浏览器跳转到 `login.html?code=xxx&state=xxx` → 页面自动展示 code → 用户复制 code 贴回对话。

### 步骤 3：用 code 换取 user_access_token（一行 curl）

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/authen/v2/oauth/token" -H "Content-Type: application/json" -d '{"grant_type":"authorization_code","client_id":"cli_aa3b9ac9dfb8dd25","client_secret":"LYN4U1q1jGyJp37Ba2fmLhgbvwHGMswX","code":"<用户贴回的CODE>","redirect_uri":"https://cnoocclaw.cnooc.com.cn/auth/login/login.html"}'
```

成功返回示例：

```json
{"code":0,"access_token":"u-xxx","token_type":"Bearer","expires_in":7200,"refresh_token":"..."}
```

从返回 JSON 中提取 `access_token` 即为本次可用的 `user_access_token`。

### 步骤 4：获取用户身份（一行 curl）

```bash
curl -s -X GET "https://open.feishu.cn/open-apis/authen/v1/user_info" -H "Authorization: Bearer <ACCESS_TOKEN>"
```

返回 `name`、`user_id`、`open_id`、`email` 等用户信息。

### 步骤 5：绑定主日历（一行 curl，可选）

```bash
curl -s -X GET "https://open.feishu.cn/open-apis/calendar/v4/calendars?page_size=50" -H "Authorization: Bearer <ACCESS_TOKEN>"
```

从返回的 `calendar_list` 中取 `type == "primary"` 的 `calendar_id` 作为主日历。

### 步骤 6：使用 token 继续业务

拿到 `user_access_token` 后，在本次会话内用于后续飞书 OpenAPI 调用（`Authorization: Bearer <token>`），继续原来的业务操作。

### （可选）发送授权卡片到飞书会话

如需在飞书 IM 内发送带「点击授权」按钮的卡片，先取 tenant_access_token：

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" -H "Content-Type: application/json" -d '{"app_id":"cli_aa3b9ac9dfb8dd25","app_secret":"LYN4U1q1jGyJp37Ba2fmLhgbvwHGMswX"}'
```

再发卡片（将 `<AUTH_URL>` 替换为步骤 1 的授权地址，`<CHAT_ID>` 替换为当前会话 ID）：

```bash
curl -s -X POST "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id" -H "Authorization: Bearer <TENANT_TOKEN>" -H "Content-Type: application/json" -d '{"receive_id":"<CHAT_ID>","msg_type":"interactive","content":"{\"config\":{\"wide_screen_mode\":true},\"header\":{\"title\":{\"tag\":\"plain_text\",\"content\":\"飞书授权登录\"},\"template\":\"blue\"},\"elements\":[{\"tag\":\"div\",\"text\":{\"tag\":\"lark_md\",\"content\":\"点击下方按钮完成飞书授权\"}},{\"tag\":\"action\",\"actions\":[{\"tag\":\"button\",\"text\":{\"tag\":\"plain_text\",\"content\":\"点击授权\"},\"type\":\"primary\",\"url\":\"<AUTH_URL>\"}]}]}"}'
```

## IM / 远程触发的交互模式

由于授权需要用户在浏览器操作，agent 必须**分两个回合**：

**回合 A**：agent 构造授权地址 → 发给用户 → 结束本回合（不阻塞等待）

**回合 B**：用户贴回 code → agent 执行步骤 3~5 的 curl → 拿到 token → 继续业务

> 关键点：agent 发完授权地址后**不要阻塞等待**，直接结束回合让用户操作。

## 错误处理

| 错误场景 | curl 返回特征 | 处理方式 |
|---|---|---|
| code 已使用/过期 | `"code":20029` 或含 `invalid code` | 让用户重新授权取新 code（回到步骤 1） |
| App Secret 失效 | `"error":"invalid_client"` | 到「应用凭证配置」区更新 `APP_SECRET` |
| scope 含未申请权限 | `"code":20027` | 从 `SCOPE` 去掉未开通的权限（如 `task:task:read`） |
| redirect_uri 不匹配 | `"code":20029` | 确认飞书后台白名单已登记当前 `REDIRECT_URI` |
| token 过期（业务调用时） | HTTP 401 或 `"code":99991663` | 重新执行完整授权流程（步骤 1~5） |
| 用户未绑定日历 | 业务报错 | 确保步骤 5 执行且取到 `calendar_id` |

## 沙箱路径合规

agent 在 DeerFlow 沙箱中执行 curl 命令时：
- curl 是容器自带命令，直接可用，无需写脚本文件
- 如需临时存储 token，写入 `/mnt/user-data/workspace/`（线程级可写目录）
- `/mnt/skills` 为只读，不能写入
- 命令中不要出现白名单外的绝对路径

## 汇报结果

告知用户：当前登录用户姓名、日历绑定情况、可执行的后续操作。**不要把 `user_access_token` 明文贴进对话**（仅在用户明确要求时展示）。

## 注意事项

- 每次需要权限都重新授权，不缓存 token
- 浏览器授权必须由用户完成，agent 不可跳过
- 拿到 token 之前不要断言已登录
- `login.html` 部署在 `https://cnoocclaw.cnooc.com.cn/auth/login/login.html`，需确保飞书后台白名单一致
- state 参数由 agent 随机生成，回调页原样返回，agent 可选校验防止 CSRF
