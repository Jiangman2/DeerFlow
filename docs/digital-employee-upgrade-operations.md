# 数字员工完整升级与运维手册

## 功能和权限

| 功能 | 个人用户 | 管理员 | 服务端约束 |
| --- | --- | --- | --- |
| 个人数字员工 | 创建、查看、修改、删除自己的记录 | 同样按记录归属操作 | SQL 按 `user_id + name` 隔离 |
| 业务域分类 | 查看和选择 | 与个人用户相同 | 服务端固定“统/分”目录，不提供在线增删改 |
| 员工标签 | 对自己记录手动增删改，或使用智能推荐 | 同个人用户 | 标签随员工记录存储，广场动态聚合 |
| 专属推理配置 | 设置自己员工的模型/采样/思考默认值 | 同个人用户 | 请求显式值 > 员工默认值 > 全局默认值 |
| Browser Live | 仅当平台开启，且该员工含 `browser` 工具组时可用 | 同左 | 同源鉴权、线程归属、Origin/SSRF 校验 |

“智能推荐标签”是基于名称、简介、业务域和能力的可重现本地规则，不发起额外模型请求。本版本不设独立的全局“标签字典”；因此标签 CRUD 是每个数字员工内的 CRUD，而业务域目录由集团规则统一治理，服务器和管理员都不能在运行时另写一套分类。

当前收到的规则文本逐项列出了 17 个“统”业务域和 9 个“分”业务域，共 26 个；代码严格使用这些已明确名称，不用占位项猜测所称第 27 个业务域。正式补充第 18 个“统”业务域名称后，应同步扩展服务端常量和目录契约测试。

## 长度与文件限制

- 稳定内部名称：1–64 个字符，支持中文、Unicode 文字/数字、下划线和连字符，一旦创建不可变。
- 展示名称：可选，最多 80 个 Unicode 字符，拒绝控制字符和不可见格式字符；未设置时回退显示内部名称。
- 简介：最多 500 个 Unicode 字符。
- 标签：每个员工最多 8 个；每个最多 20 个 Unicode 码点；去空白、去重，支持逗号/中文逗号/换行批量输入。
- 业务域 ID：使用服务端目录中固定的安全 ID；新建员工默认归入“综合管理”，编辑界面必须选择一个业务域。
- 专属温度：0–2；最大输出 Token：1–200000；推理强度：`low` / `medium` / `high`。
- 自定义头像：PNG/JPEG/WebP，最多 5 MB，最长边不得超过 8192 像素，禁止动画，存储前转为 512×512 WebP。

上述边界在前端给出即时计数/错误提示，后端独立重新校验，不信任浏览器传入值。

## 首次热更顺序

1. 备份当前数据库、`config.yaml` 和 `.deer-flow/`，确认共享持久卷可写。
2. 应用热更源码，但先不切换 `agent_storage.backend`。
3. 安装后端依赖并预装 Chromium：

   ```bash
   cd backend
   uv sync --all-packages --extra postgres --extra browser
   uv run playwright install chromium
   ```

   生产镜像应在构建阶段完成 Chromium 安装，不要依赖 Pod 启动时临时下载。

4. 确认 `database.backend: postgres` 和 `$DATABASE_URL` 正确。完整版本由 Alembic migration `0019_digital_employee_agents` 创建表；独立热更则由下面的导入脚本仅以 `checkfirst` 创建它自己的 `agents` 表，不顺带执行其他业务迁移。
5. 预检查文件 Agent 导入：

   ```bash
   cd backend
   uv run python scripts/migrate_agents_to_db.py --dry-run
   ```

6. 预检无冲突后执行导入：

   ```bash
   uv run python scripts/migrate_agents_to_db.py
   ```

   导入是非破坏性的：原文件保留，重复运行会跳过已存在的数据库记录。

7. 把生产配置切换为：

   ```yaml
   agents_api:
     enabled: true
   agent_storage:
     backend: db
   ```

8. 启用 Browser 工具组和 `browser_*` 工具，保持 `allow_private_addresses: false`；确保 Nginx 的 Browser HTTP/WebSocket 路由已同步。
9. 构建前端，重启 Gateway/Frontend/Nginx；Gateway 会幂等迁移数据库个人员工、文件个人员工、团队广场及旧共享目录中的旧分类。开启 Browser Live 时保持 `GATEWAY_WORKERS=1` 且 Gateway Pod 副本数为 1。
10. 按本文“发布后验证”完成烟雾测试后再恢复流量。

## 回滚

1. 暂停新建/编辑数字员工，执行热更包的 `rollback-hotfix.sh`。
2. 把 `agent_storage.backend` 切回 `file`，重启 Gateway。因为导入不删除原文件，可立即恢复原读取路径。
3. 数据库中的新 Agent 行可保留作为取证/二次迁移数据，不必在紧性回滚时删除。
4. Browser Live 可通过移除 `browser` 工具组或其工具立即失败关闭，不影响普通会话。

如果切换到 SQL 后又创建了只存在于 SQL 的数字员工，回滚前应先导出这些记录，否则文件后端不会看到它们。

## 发布后验证

- 用普通用户创建中文展示名数字员工，分别选择“统”和“分”业务域、手动/智能加标签、选预制头像并上传一张自定义头像。
- 确认分类目录默认收起，展开后以单列展示全部业务域，每项标注“统”或“分”；原管理入口消失，旧 `/api/admin/digital-employee-categories` 路由返回 404。
- 核对个人列表和数字员工广场均不再出现 `general`、`research` 等旧分类 ID；迁移后的员工均属于固定业务域。
- 确认名称/分类/标签/简介/能力模糊搜索，以及分类与标签交集筛选。
- 分别以 320/375/390/412 px 竖屏和 844×390 横屏打开广场、编辑弹窗与 Browser Live 底部抽屉，确认无水平滚动。
- 设置两个使用同一模型、不同温度/输出上限的员工，检查实际运行请求使用各自值。
- 使用含 `browser` 工具组的员工打开一个允许的 HTTPS 页面，验证导航、点击、输入、实时帧、关闭和线程终止清理。
