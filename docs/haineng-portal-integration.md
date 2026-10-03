# 海能门户集成

本次改动限于前端工程及必要的服务端接口配套，不修改 Gateway、数据库、
数字员工知识库绑定或标准门户。

## 页面入口与展示

- 普通聊天首页右上角增加“海能”平台入口：`https://ai.cnooc.com.cn/`。
- 数字员工下方增加“智能体广场”：
  `https://ai-dev.cnooc.com.cn/#/inferencePlatform/hyAppSquare`。
  侧栏沿用上图标、下文字的布局，图标使用当前侧栏颜色和 24 单位尺度。
- 平台数字员工共享目录同步纳入 12 个大赛成果外链入口，名称、简介与网址
  来自需求表，复用既有业务域，每人最多 3 个标签；缺少应用网址的条目不创建。
  详见 [大赛成果配置清单](contest-digital-employees-20260928.md)。
- 普通聊天首页底部保留版权与地址，在地址右侧显示总使用量、今日使用量、联系我们。
- 联系我们悬浮框在鼠标移入时打开、移出时关闭，不使用点击开关。
  支持键盘聚焦，弹层打开时提升页脚层级，避免被创建、小惊喜等快捷入口遮挡。
  不展示服务群或二维码。
- 联系信息：电话 `18810388532`、邮箱 `shiyl19@cnooc.com.cn`、
  业务负责人姜彬、运维负责人姜曼、服务时间 `8:30 ～ 17:30`。
  意见反馈链接打开该客服邮箱。
- 不替换已有聊天、数字员工聊天、标准门户或登录流程。

## 使用量接口与配置

总使用量和今日使用量由同一个 GET 接口返回。浏览器只请求已登录校验的
`/integrations/haineng/model-call-count`，Next.js 服务端签名后访问海能，
仅将两个统计数字返回浏览器。首页每 60 秒刷新，失败时显示 `—`，不伪造零值。

本地在 `frontend/.env.local` 配置以下服务端变量；示例占位也见
`frontend/.env.example`。不要使用 `NEXT_PUBLIC_` 前缀或提交真实凭据。

```dotenv
HAINENG_MODEL_CALL_COUNT_URL="https://ai-dev.cnooc.com.cn/gaixc/hy-dashboard/api/listedModelService/modelCallCount"
HAINENG_MODEL_CALL_COUNT_APP_ID="填写对方分配的 appid"
HAINENG_MODEL_CALL_COUNT_APP_KEY="填写对方分配的原始 app-key"
```

原始 APP-KEY 不等于临时签名。每次请求以 UTF-8 计算
`MD5(appId + "UZ" + 原始appKey + "UZ" + 毫秒时间戳)`，请求头为
`APP-ID: appId` 和 `APP-KEY: 小写MD5值.同一毫秒时间戳`。
文档中接口概述与 curl 示例路径不同，默认使用 curl 示例路径；
对方确认后可通过 URL 配置覆盖，不自动跨路径重试。

接口只允许 HTTPS 且禁止重定向，8 秒超时，不回传凭据或上游原始错误内容。
若需要内部 CA，配置 `NODE_EXTRA_CA_CERTS`，不得关闭 TLS 校验。
响应不缓存；未登录返回 401，未配置返回 503，上游错误返回 502，超时返回 504。

## 发布与检查

改动全部位于前端工程，另有根目录和前端目录的 Docker 忽略规则保护环境文件。
本机 `.env.local` 与预览脚本不属于发布代码。生产部署需更新前端构建/镜像，
由运行环境 Secret 注入 APP_ID/APP_KEY，URL 使用环境变量配置。
本地 `next dev` 可热更新，无需每次重建镜像。

回归测试：`frontend/tests/unit/server/haineng/` 与
`frontend/tests/e2e/haineng-portal-integration.spec.ts`。
