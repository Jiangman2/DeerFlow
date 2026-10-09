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
