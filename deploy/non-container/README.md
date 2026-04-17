# 非容器部署（内网 Ubuntu）

## 1. 本机打包（Windows）

在项目根目录执行：

```powershell
.\deploy\non-container\package_windows.ps1
```

产物：
- `deploy/non-container/out/deerflow-transfer.zip`

如果服务器无法访问 npm（pnpm install 报 407/超时），使用离线 standalone 包（推荐）：

```powershell
.\deploy\non-container\package_windows_standalone.ps1
```

产物：
- `deploy/non-container/out/deerflow-transfer-standalone.zip`

把 zip 通过 U 盘/内网网盘/跳板机传到内网 Ubuntu 服务器，例如：`/tmp/deerflow-transfer.zip`

## 2. 服务器解压与安装依赖

```bash
sudo mkdir -p /opt/deer-flow
sudo unzip /tmp/deerflow-transfer.zip -d /opt/deer-flow
cd /opt/deer-flow
sudo chmod +x deploy/non-container/server/install.sh
sudo chmod +x deploy/non-container/server/run.sh
sudo ./deploy/non-container/server/install.sh
```

说明：
- `install.sh` 需要从项目根目录工作。它会自动识别 `ROOT_DIR=/opt/deer-flow`。
- 如果你把 zip 解压到了 `/opt/deer-flow`，安装脚本不会再做 rsync 自拷贝（避免 rsync code 24 “files vanished”）。

安装脚本会安装：
- libreoffice-writer（用于 .doc 转换）
- antiword（.doc 纯文本兜底）

并在前端执行 build、后端执行 uv sync。

## 3. 配置环境变量（必须）

编辑：
- `/etc/deerflow/deerflow.env`

复制参考：
- `/opt/deer-flow/deploy/non-container/server/deerflow.env.example`

必须检查/填写：
- `DEER_FLOW_HOME`（建议 `/opt/deer-flow-data`，需可写）
- `DEER_FLOW_CONFIG_PATH=/opt/deer-flow/config.yaml`
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH=/opt/deer-flow/extensions_config.json`
- `MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY/MINIO_BUCKET/MINIO_SECURE`
- `BETTER_AUTH_SECRET`（建议随机 32+ bytes）

## 4. 启动方式 A：手动前台启动（便于调试）

```bash
cd /opt/deer-flow
sudo ./deploy/non-container/server/run.sh
```

端口：
- 前端：3000
- Gateway：8001
- LangGraph：2025

## 5. 启动方式 B：systemd 自启（推荐）

复制 unit 文件：

```bash
sudo cp /opt/deer-flow/deploy/non-container/server/systemd/deerflow-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deerflow-gateway deerflow-langgraph deerflow-frontend
```

查看状态与日志：

```bash
systemctl status deerflow-gateway deerflow-langgraph deerflow-frontend
journalctl -u deerflow-gateway -n 200 --no-pager
journalctl -u deerflow-langgraph -n 200 --no-pager
journalctl -u deerflow-frontend -n 200 --no-pager
```

## 6. MinIO 地址与访问配置怎么查

优先从运维/数据湖提供的信息获取（推荐），如果不确定：
- 看服务器环境变量：`env | grep -i minio`
- 看你配置文件：`cat /etc/deerflow/deerflow.env | grep MINIO_`
- 若 MinIO 在 k8s：`kubectl get svc -A | grep -i minio`
- 若 MinIO 在 docker：`docker ps | grep -i minio`
- 若只有域名但不通：`nslookup <minio-domain>` / `curl -I http://<endpoint>/minio/health/ready`

## 7. 迁移后的常见问题

- 读不到 `.env`：systemd 不会自动加载 `.env`，要用 `/etc/deerflow/deerflow.env`
- 线程数据没落盘：确认 `DEER_FLOW_HOME` 可写
- .doc 仍无法解析：确认 `soffice --headless --version` 可执行；检查 libreoffice-writer 是否安装
- pnpm install 报 407/超时：改用 standalone 包，服务器端无需安装 node_modules
- 前端启动失败：确认服务器 `node -v` 符合 Next.js 要求（建议 Node 18.18+ 或 20+）
- 端口不通：防火墙/安全组放行 3000/8001/2025（或统一用 Nginx 反代再开放 80/443）
