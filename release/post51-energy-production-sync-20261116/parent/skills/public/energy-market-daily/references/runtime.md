# 运行环境和私有配置

最低 Python 3.10。抓取和校验只用 Python 标准库；PNG 需要 Playwright、匹配的 Chromium／headless shell 或已安装的 Chrome／Edge，以及中文字体。程序优先使用显式配置或当前用户可访问的 headless shell，再回退 Playwright 默认和系统浏览器；不依赖固定 ARM64 路径，不读取其他用户的私有缓存。不可访问的缓存目录会跳过。

每次任务只执行一次 `python <Skill绝对路径>/scripts/daily.py doctor --workspace <本次项目绝对路径>`。它不联网、不启动浏览器、不复制大型运行依赖，也不输出配置值。`ready_for_report` 仅表示只读前置检查通过，不证明供应商可用或浏览器已启动；`credentials_configured` 也只证明凭据可读取。

海能生产对话和定时任务不安装依赖。`doctor` 返回 `ready_for_report=false`，或后续程序返回 `BROWSER_`／`NO_USABLE_BROWSER`／`PLAYWRIGHT_REQUIRED`，都由运维修复后再恢复原项目；模型不重新预检、读源码、安装或试启动参数。特别是已绑定的运行依赖未挂载，安装另一套 Chromium 不能修复该绑定。

## 仅供平台外首次部署的环境准备

下面示例只供操作者在通用沙箱首次部署前准备环境，不属于日报任务执行或失败恢复流程：

```sh
python -m pip install -r <Skill绝对路径>/requirements.txt
python -m playwright install chromium
```

Linux 缺系统依赖且具备安装权限时，可用 `python -m playwright install --with-deps chromium`。受限镜像应由运维预装 Chromium 和 Noto Sans CJK；没有浏览器、字体或外网权限的空沙箱不能保证出图和实时取数。无可用依赖时返回明确错误，不声称已生成 PNG。安装说明依据 Playwright 官方文档：https://playwright.dev/python/docs/browsers 。

支持 Linux x86_64／ARM64、Windows、macOS，实际可运行性还取决于该系统的浏览器支持。可用 `REPORT_BROWSER_EXECUTABLE` 指定已有可执行文件；可用 `PLAYWRIGHT_BROWSERS_PATH` 指定已有缓存。HTML/SVG 全部内联，渲染时禁止网络请求。

## 已部署环境的验收与配置

生产部署必须用普通任务的实际用户及真实挂载验证出图；root 维护容器可运行、或给维护容器额外加挂目录，都不能代替实际任务验收。浏览器及依赖应放在任务用户可访问的部署目录，通过上述变量配置。不要为取用 root 缓存而放开 `/root` 权限。运行错误会保留采集结果；环境修好后执行回执中的原项目 `resume_command` 即可。

海能部署可附带一个独立、版本固定的 ARM64 浏览器运行依赖；它不包含行情、会话或凭据。程序按部署绑定校验逐文件清单及哈希，复制到当前用户独有的临时目录执行，结束后清理，以兼容共享盘只读或禁止执行的挂载。这个依赖不会改变取数、新闻、计算和排版规则；通用代码包在其他系统上仍使用该系统配置的 Playwright／浏览器，不尝试执行 ARM64 程序。

凭据优先级：环境变量 → `--credentials`／`ENERGY_DAILY_CREDENTIALS` 指定的 JSON → Skill 内 `.private/credentials.json`。支持：

```json
{"JKX_API_URL":"供应商接口地址","JKX_API_PASSWORD":"运行时私有值"}
```

兼容作者 JSON 的 `JKX_API_ENDPOINT`，也兼容 `JKX_API_URL` 查询参数中的 `p`。环境变量可覆盖文件；密码不会进入命令参数、输出报告或日志。供应商请求禁止重定向，避免把凭据发送到另一个地址。

通用包不包含 `.private`；仅供本人使用的私有部署包可携带这个配置。私有部署包不得提交公开 Git、上传公共 SkillHub 或转发到无关沙箱。程序不会自动读取作者桌面路径或搜索磁盘上的凭据。

网络继承 `HTTP_PROXY`／`HTTPS_PROXY`／`NO_PROXY`。也可用 `ENERGY_DAILY_HTTP_PROXY` 为本程序单独指定已批准的 HTTP 代理，适用于企业沙箱不继承通用代理变量的情况。企业要求代理时由运行环境提供正确配置；程序不擅自绕过代理，也不修改 Gateway 环境。现有供应商为 HTTP 接口，沿用真实协议，不能自行改成 HTTPS 后假称可用。

可用 `--cache <绝对路径>` 复用同一信任范围的历史日缓存。默认项目内独立缓存；包内 93 天原始历史样本只用于其对应日期的历史曲线，绝不替代新鲜目标日响应。
