# HTTP 渗透问题整改与验收

## HTTP 危险方法

企业/本地 Kubernetes、Docker 和本地前台 Nginx 都仅允许 GET、HEAD、POST、PUT、PATCH、DELETE、OPTIONS。未使用的扩展方法在入口返回 405；CONNECT 的非法请求形态可能在 Nginx 解析阶段直接返回 400。TRACE 由 Nginx 自身拒绝，不回显请求头。正常业务的 DELETE/PUT/PATCH 保留原有 Gateway/上游鉴权、资源权限及 CSRF 边界。不能仅凭一个携带有效登录凭据的 DELETE 请求成功就认定为漏洞。

更新对应 Nginx 配置源并部署；Kubernetes 更新 ConfigMap 后滚动重启 `deer-flow/nginx`，该部署在启动时复制模板为 `/tmp/nginx.conf`，仅 reload 旧临时文件不会加载新模板。宿主机/容器按实际启动配置先执行语法检查再重载。不要修改运行时文件充当持久化发布。

从授权复测机器检查（替换域名）：

```bash
curl -i -X TRACE https://cnoocclaw.cnooc.com.cn/
curl -i -X PROPFIND https://cnoocclaw.cnooc.com.cn/api/threads/test
curl -i -X DELETE https://cnoocclaw.cnooc.com.cn/api/threads/test
```

TRACE/扩展方法不能到达上游；无登录凭据的删除不能成功（401/403，或不存在资源的拒绝结果）。使用测试账号和专用测试会话验证授权删除、更新、OPTIONS、文件上传及 SSE 正常。禁止用生产会话做删除复测。源代码修改完成不等于现场整改通过。

本地真实 Nginx 回归：设置 `NGINX_TEST_BINARY`、`NGINX_TEST_CERT`、`NGINX_TEST_KEY`，运行 `python -m unittest discover -s tests -p test_http_edge_security.py -v`。测试只改监听端口、服务地址及日志/证书路径，使用模拟上游验证拦截与业务转发；真实账户授权仍需服务器复测。

## HTTP Host 头攻击

三个 Nginx 配置对原始 Host 和 absolute-form 请求目标解析出的主机名分别校验。企业公开域名为操作者确认的 `cnoocclaw.cnooc.com.cn`，允许不带端口及显式 80/443；本地调试保留 `localhost`、`127.0.0.1`、`[::1]`。其他主机及未经批准的企业域名端口返回 400，不生成 Location，也不转发。新域名/IP 必须经过运维确认后修改两个 map，禁止恢复通配白名单。通过 IP/NodePort 运维检查时，应携带批准域名 Host，例如 `curl -H 'Host: cnoocclaw.cnooc.com.cn' http://节点IP:32026/health`。

Nginx 自身重定向使用相对 Location；每个代理 location 都丢弃客户端 `Forwarded`、`X-Forwarded-Port`，覆盖 `X-Forwarded-Host` 为已经校验的 Host。设置必须在各 location 生效，不能只依赖 http 层的 `proxy_set_header`，因为 location 内已有该类指令会阻断继承。现有 TLS scheme 传递保持不变，外层反向代理也必须覆盖浏览器输入的转发头并使用批准 Host 转发；后端直接端口只允许可信代理/内部调用来源。

Nginx 的三个 Kubernetes 健康探针显式发送 `Host: localhost`，防止默认 Pod IP Host 在白名单启用后使探针失败。其余外部负载均衡/监控探针也应发送批准域名。

```bash
# 应返回400且没有指向恶意域名的Location
curl -i https://cnoocclaw.cnooc.com.cn/platform/skillhub -H 'Host: attacker.example'
# 应保持正常处理，转发头不得改变回调/重定向目标
curl -i https://cnoocclaw.cnooc.com.cn/platform/skillhub \
  -H 'Forwarded: host=attacker.example;proto=https' \
  -H 'X-Forwarded-Host: attacker.example' -H 'X-Forwarded-Port: 9999'
```

部署后验证登录、IAM/OIDC 回调、SkillHub/知识库跳转、HTTPS Cookie 和三个健康探针。保持原域名并清除注入头，不能通过修改已有回调配置来掩盖攻击。
