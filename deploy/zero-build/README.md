# DeerFlow 零构建迁移（容器）

## 目标
在一台可联网并安装 Docker 的“构建机”上生成离线部署包；在公司内网 Ubuntu 服务器上仅执行解压 + 启动命令即可运行 DeerFlow。

## 构建机（有 Docker）

```bash
chmod +x deploy/zero-build/build_bundle.sh
./deploy/zero-build/build_bundle.sh
```

输出文件：
- `deploy/zero-build/out/deerflow-bundle.tgz`
- `deploy/zero-build/out/images/deerflow-images.tar`

## Ubuntu 服务器（内网）

1) 复制 `deerflow-bundle.tgz` 到服务器（示例）

```bash
scp deploy/zero-build/out/deerflow-bundle.tgz user@server:/tmp/
```

2) 解压到固定目录（示例 /opt/deer-flow）

```bash
sudo mkdir -p /opt/deer-flow
sudo tar -xzf /tmp/deerflow-bundle.tgz -C /opt/deer-flow
```

3) 启动（要求服务器已安装 docker + docker compose）

```bash
cd /opt/deer-flow
chmod +x deploy/zero-build/server_up.sh
sudo ./deploy/zero-build/server_up.sh
```

访问：
- `http://server-ip:2026`（默认）

