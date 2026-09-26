# PPT Confirm UI 预览修复 R2：Frontend 构建修正

R1 在 Frontend 源码检查阶段安全停止，因为现网 Channel/PDF R2 前端没有尚未上线的 `isSafeHref` 分支。R2 使用与现网 SHA-256 完全一致的历史源码建立精确增量，只重建 Frontend。R1 已成功构建的 Gateway 和 PPT Runner 候选归档通过原有 SHA-256 复用，不改其内容，也不更新生产服务。

在 `datax-01` 上传包和外层 `.sha256`，先验证外层校验和，解压到尚不存在的 `/opt/deerflow-hotfixes/ppt-preview-auth-r2`。进入目录执行：

```bash
sha256sum -c PACKAGE.SHA256
python3 -B verify_package.py "$(pwd)"
sudo bash build-frontend-datax01.sh --check-only
```

只有看到 `preview_r2_build_check=PASS` 才执行：

```bash
sudo env PREVIEW_R2_BUILD_ACK=YES bash build-frontend-datax01.sh --build-only
```

构建使用已经停止的 R1 独立 BuildKit 缓存根及已验证的 Frontend OCI 布局，网络关闭，CPU 150%、内存 12 GiB、并发 1；仍禁止与生产 Gateway 同节点构建。R2 输出只包含新的 Frontend OCI，Gateway/Runner 候选继续保留在 R1 输出目录。构建完成不等于热更完成：尚需镜像分发、CRI 验证、受控滚动、真实新线程 Confirm UI 验收。禁止跳过任何失败门禁或直接 rollout。
