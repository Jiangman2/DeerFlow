# 前端平滑流式显示补丁：20261003-r2

本包基于服务器分支 `fix/upload_test_env` 的提交
`e2510d6c6f9287f314ebdfc87c6691fe5a77aa5c` 制作。
包含 6 个前端源码文件、4 个测试文件、前端模块指南，以及本次功能与安装说明和只读校验程序。
不包含依赖、凭证、其他本地修改、数据库、Redis 配置或镜像。

相关 137 项测试（含 10 项真实 React DOM 生命周期测试）、全项目 TypeScript 检查、
修改文件的 ESLint 和 Prettier 检查通过；尚未构建完整生产镜像或部署。

以下命令在服务器 Bash 中执行。每段成功后再执行下一段。

## 1. 校验上传文件

将 ZIP 上传至 `/home/cnooc`，执行：

```bash
patch=/home/cnooc/deerflow-frontend-smooth-streaming-20261003-r2.zip
sha256sum "$patch"
unzip -t "$patch" | tail -n 2
```

SHA-256 必须与交付消息一致，ZIP 必须显示无损坏。

## 2. 检查服务器源码是否适合覆盖

先只解压校验程序到独立临时目录，不修改 `/opt/DeerFlow`：

```bash
check_dir=$(mktemp -d /home/cnooc/frontend-smooth-check-XXXXXX)
unzip "$patch" 'docs/frontend-smooth-streaming-20261003-r2-check.py' -d "$check_dir"
python3 "$check_dir/docs/frontend-smooth-streaming-20261003-r2-check.py" \
  --repo /opt/DeerFlow --archive "$patch"
```

看到 `PREFLIGHT OK` 才继续。若出现 `SOURCE VERSION MISMATCH`、缺少文件、
权限问题或其他 `STOP`，不要强制覆盖；先提供输出并核对服务器版本。
校验容忍 Windows CRLF 与 Linux LF 行尾差异，也允许本次补丁已完整应用的文件。
不要在检查和解压期间同时拉代码或修改这些文件。

## 3. 备份并解压

此段保存所有将被覆盖且当前存在的文件；新文件在回滚时需要逐一核对。
备份成功后才会解压：

```bash
cd /opt/DeerFlow
backup=/home/cnooc/deerflow-before-smooth-streaming-r2-$(date +%Y%m%d-%H%M%S).tar.gz
targets=(
  frontend/src/components/workspace/messages/markdown-content.tsx
  frontend/src/components/workspace/messages/message-group.tsx
  frontend/src/components/workspace/messages/message-list-item.tsx
  frontend/src/components/workspace/messages/message-list.tsx
  frontend/src/core/streamdown/streaming-text-buffer.ts
  frontend/src/core/streamdown/use-smooth-streaming-text.ts
  frontend/tests/unit/components/workspace/messages/markdown-content.test.ts
  frontend/tests/unit/components/workspace/messages/markdown-streaming.test.ts
  frontend/tests/unit/core/streamdown/streaming-text-buffer.test.ts
  frontend/tests/unit/core/streamdown/use-smooth-streaming-text.dom.test.tsx
  frontend/AGENTS.md
  docs/frontend-smooth-streaming-20261003-r2.md
  docs/frontend-smooth-streaming-20261003-r2-manifest.json
  docs/frontend-smooth-streaming-20261003-r2-check.py
  docs/frontend-smooth-streaming-20261003-r2-install.md
)
existing=()
for f in "${targets[@]}"; do
  if [ -e "$f" ]; then existing+=("$f"); fi
done
tar -czf "$backup" -- "${existing[@]}" &&
  unzip -o "$patch" -d /opt/DeerFlow
printf 'backup=%s\n' "$backup"
```

没有目录写权限时，请在有权限的终端操作，并重新设置 `patch` 变量；
不要通过修改整个仓库权限来解决。

## 4. 构建前端镜像

沿用现有网络、企业 CA、代理和 registry 的构建参数。标准仓库命令是：

```bash
cd /opt/DeerFlow
export RELEASE_TAG=dev-20261003-smooth-streaming-r2
sudo docker build -f frontend/Dockerfile --target prod \
  -t "deer-flow-frontend:${RELEASE_TAG}" .
```

构建失败请保留完整错误，不要关闭类型检查或删除测试来绕过。
构建成功也不会自动更新 Kubernetes；镜像导入和 Deployment 更新应作为下一步
按实际容器名称、节点和当前镜像确认后执行。此功能不需要重启 Gateway 或 Redis。
更换前端 Pod 会中断其上的前端请求，因此请在没有压测进行时发布。

## 5. 发布后验证

单次中文回答应平滑显示；停止时立即补齐已收到文字。切换会话、后台标签页、
代码块、表格、文件链接和工具状态应正常。长时间上游停顿仍可能出现，前端
不会伪造尚未收到的文字，也不会新增逐字请求或 Redis 写入。
