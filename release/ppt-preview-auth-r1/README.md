# PPT Master Confirm UI 5050 预览修复 R1

这是离线**候选构建包**，不是已经预装/热更的镜像。它只修改以下三份现网镜像：

- Gateway Relay R1：为普通线程到 AIO 沙箱的 5050 代理增加服务端 Bearer；保留 DeepSeek relay。
- PPT Runner R15：为隔离 PPT 线程到 AIO 沙箱的 5050 代理增加相同鉴权；保留独立 Runner。
- Frontend Channel/PDF R2：把中文句子中的 `http://127.0.0.1:5050` 链接定向到当前线程的鉴权代理，同时保留后续说明文字。

浏览器始终通过原有线程读权限访问代理；它不接收沙箱密钥。包不修改沙箱、Provisioner、Skill、数据库、Nginx 资源或已有线程数据。旧的已释放沙箱不能用于验收；要用一个新的 Confirm UI 任务。

## 构建

仅在 `datax-01` 上、Gateway 不在该节点时构建。三个底包路径固定在 `build-datax01.sh`，指向已审核的现网镜像归档。构建在 `/opt` 独立 BuildKit 中离线运行，CPU 150%、内存 12 GiB、并发 1；根盘和 `/opt` 有余量门禁。它只产生三份 OCI 候选归档，不导入或改动任何 Deployment。

在 `datax-01:/home/cnooc/` 验证外层 SHA-256，然后解压到 `/opt/deerflow-hotfixes/ppt-preview-auth-r1`（目标必须不存在），进入该目录执行：

```bash
sha256sum -c PACKAGE.SHA256
python3 -B verify_package.py "$(pwd)"
bash build-datax01.sh --check-only
```

仅在全部 PASS 后：

```bash
PREVIEW_BUILD_ACK=YES bash build-datax01.sh --build-only
```

构建后先查看三份 `.oci.tar.sha256`、导入到所有可能调度到的节点并验证 CRI，然后只读预检、保留旧镜像和 Deployment 配置、逐服务受控滚动。任何一个候选构建失败都**不得**继续导入或 rollout。生产生效必须再验收两个 URL：线程代理 `/api/threads/<新线程ID>/live-preview/5050/` 不再 401，中文原文链接点击不再打开浏览器本机；还须实际完成 Stage-1 确认。

本包不把 `http://127.0.0.1:5050` 直接暴露给外部浏览器，不关闭沙箱认证，不扩大线程读权限。构建成功不等于生产验收成功。
