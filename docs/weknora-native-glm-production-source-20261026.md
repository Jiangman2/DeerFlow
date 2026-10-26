# WeKnora 已上线 native GLM 源码归档（2026-10-26）

本次将生产镜像已经使用、但目标分支缺失的源码补回
`codex/enterprise-consolidation-20260716`。不切换镜像，不调整生产配置，不执行数据库迁移。

## 结论与来源

- 生产镜像：`deerflow.local/weknora-app:20261016-native-glm-runtimefix-fc44d25744194ece`。
- 上游记录：`https://github.com/Tencent/WeKnora`，v0.8.0，
  `1edcd54b43606d9079bb36650efe3f68707a79ea`。这是基础版本记录，企业补丁另计。
- 企业归档：`https://github.com/Jiangman2/DeerFlow`，历史分支
  `codex/pending-hotupdates-20261017`，完整提交
  `99fb9464f8253bee7f3cfe059729a3df3ef553f2`。
- 缺失来自后续回退提交 `28a9f821b476cc3c378c3b3c6ff26ccf9619b42a`。
  对比目标提交 `5df040bc058d697bf77e9c35667a285733318a20`，构建清单中的
  1,918 个仓库文件一致，6 个缺失，`vlm.go` 少了 11 行。
- 恢复后 1,925 个仓库源码文件逐字节匹配历史清单；锁定的 AnyDoc 0.1.9
  源码包经过原有单行补丁得到另外 74 个构建文件，共 1,999 个，全部匹配。

历史构建使用目录快照，没有独立保留构建瞬间的 Git HEAD 与工作区状态。
`99fb9464…` 是构建后的归档提交，不能称为“当时无未提交改动的构建 HEAD”。
标签末尾 `fc44d25744194ece` 是最终运行时构建上下文 SHA256 的前 16 位，
不是 Git commit，也不是程序摘要。

## 恢复的代码

目录：`vendor/weknora-app-20260912/`。

| 文件 | 已上线行为 |
| --- | --- |
| `internal/models/vlm/enterprise_glm.go` | 单图、图像在前的 GLM 请求；强制 SSE；有界解析；只返回可见内容；每次调用一次 POST，无自动重试 |
| `internal/models/vlm/enterprise_glm_config.go` | 显式 wire profile、模型与端点校验；保留 SSRF、CA 和主机名校验；直连、禁止重定向 |
| `internal/models/vlm/enterprise_glm_core_test.go` | 请求、响应、限额与错误路径测试 |
| `internal/models/vlm/enterprise_glm_stream_test.go` | 流式内容、完成标记与隐藏推理处理测试 |
| `internal/models/vlm/enterprise_glm_test.go` | 原生工厂选择、配置及 SSRF 回归测试 |
| `cmd/glm-vlm-probe/main.go` | 历史受控验收探针，默认不会由本次 CI 发起生产调用 |
| `internal/models/vlm/vlm.go` | 恢复 11 行显式 profile 接入与空配置检查；继续使用已有并发控制和跟踪包装 |

这些文件直接恢复自归档提交，没有混入新功能，也没有为通过 CI 修改已上线运行逻辑。

## 构建位置与实际步骤

历史构建主机是 `datax-01`，Linux ARM64、Debian 12、glibc 2.36。
原编译目录为 `/opt/weknora-glm-dwarf-fix-qYPHEQjp/rebuild/build1`；
最终运行时打包目录为 `/opt/weknora-glm-runtime-fix-8meva2vp/rebuild`；
验收回执为 `/opt/weknora-glm-verified-ysyi3g70/BUILD-RESULT.json`。

必要的历史脚本与测试位于
[`release/mineru-provider-switch-20261014/glm-native/`](../release/mineru-provider-switch-20261014/glm-native/README.md)。
45 个文件保留原始字节，锁中逐份记录摘要。共享 BuildKit/OCI 工具沿用仓库现有的
`release/production-build-alignment-20260928/r4-build/`，9 个文件与原构建计划一致。

1. `prepare_public_inputs.py`、`assemble_offline_context.py` 收集锁定依赖、制作源码清单；
   AnyDoc 0.1.9 仅将 `document_to_markdown` 的现有导入改为公开导出。
2. `package_offline.py`、`build_weknora.py`、`Dockerfile.offline` 与 `build_native.sh`
   构建完整服务和两个探针。Go 1.26.8，Rust 1.99.1，`CGO_ENABLED=1`、`-tags anydoc`。
3. `repair_sqlite_build.py` 补充构建容器所需的锁定 SQLite 开发包；生产数据库仍为
   PostgreSQL。`repair_dwarf_build.py` 加入 `GOEXPERIMENT=nodwarf5`，保留编译优化，
   修复当时编译器 DWARF5 崩溃。仅运行初版 Dockerfile 并不等于执行了最终构建过程。
4. `prepare_runtime_finish.py` 从已完成且摘要核验通过的编译输出取出三个程序，
   `Dockerfile.runtime-finish` 加入五份 gojieba v1.4.7 词典，并设置
   `JIEBA_DICT_DIR=/usr/local/share/weknora/jieba`。保留原基础镜像 20 层及入口逻辑，
   最终镜像 24 层。
5. `finish_runtime_build.py`、`verify_runtime_candidate.py`、
   `qualify_runtime_candidate_fix1.py` 形成历史运行时检查和最终回执。

构建器为 `golang:1.26.8-bookworm` ARM64/v8，manifest
`sha256:581f0292073059289b736d46d572ba4c515e0b511f8645963e0b0adf232680a1`。
运行时基础镜像为 `deerflow.local/weknora-app:0.8.0-enterprise-proxy1-anydoc1-arm64-20260912`，
manifest `sha256:579198308537bd130e9daa79173a3ae56897cdd520abc58d88df1efe043095ef`。

## 可核验记录

主锁文件：[`weknora-native-glm-source-lock-20261026.json`](../deploy/enterprise/weknora-native-glm-source-lock-20261026.json)。
证据目录：[`release/production-source-20261026/weknora-native-glm/`](../release/production-source-20261026/weknora-native-glm/)。
保留原始 `SOURCE.SHA256`、`INPUTS.SHA256`、`CONTEXT.json`、`BUILD-PLAN.json`、
183,823 字节的公开 AnyDoc 源码包，以及操作员返回的脱敏构建回执。
源码包内保留原许可证；大体积 OCI、编译产物和缓存不提交 Git。

| 对象 | SHA256（镜像值省略 `sha256:` 前缀） |
| --- | --- |
| 原始 1,999 文件源码清单 | `43b8484b6ef5a18ed76a6a13ab3faf8a13eca8a76589d454ae0687293971e1b0` |
| 最终运行时上下文 | `fc44d25744194ece953249237305856c1c4e7e13fbed739dadc35fb9bb76ca10` |
| 最终镜像 manifest | `3bc43f4e4643fd069f59fdcd6a824ed6edb6603c3f2b957ff5b5451b853f4709` |
| 最终镜像 config | `22b73c727cfb445ecf9fabe97f693efd5ceb7cbcb449b593c219e6407aef64c5` |
| 最终镜像归档 | `471ba1fecc997ce0785da151dac47da313d256506ece353275d04cdcff168f51` |
| `/app/WeKnora` | `3b5a43b229460df3283aa1336ec48223f34a81832c6b59c642dc8bc83890499e` |
| 服务器原始 BUILD-RESULT.json | `cb92bb8ebff2b7caabddbc090578d6e427613a6eea1ddd42daa228d1a8892e78` |

操作员确认服务器历史构建回执与原归档摘要一致，且回执内程序摘要与抽样现网程序一致。
这是对所采样程序的关联核验，不是所有副本、整个容器文件系统或生产数据库配置的审计。

## 验证与后续变更

```bash
python deploy/enterprise/verify-weknora-native-glm-source.py --ref HEAD
python -B -m unittest discover -s deploy/enterprise -p test_weknora_native_glm_source.py -v
```

校验覆盖全部 1,999 个源文件、历史脚本和共享构建器摘要、AnyDoc 补丁以及回执和程序摘要的关联。
本次本地 Linux 隔离环境已通过完整 VLM 包（带 `anydoc` 标签）、SSRF 指定回归测试、
`go vet`，以及 113 项历史构建工具测试和 9 项源码锁测试；没有生产模型请求。
GitHub 的专用工作流重复源码核验和构建工具测试，并运行完整 VLM 包的普通 CGO 测试及 vet。
GitHub 测试不是原 ARM64 完整镜像的重新构建，不据此宣称镜像可逐字节复现。

这次源码恢复本身不新增迁移或配置变更。未来真正升级时仍须检查原镜像入口的迁移和恢复行为，
核对实际 `AUTO_MIGRATE`、`AUTO_RECOVER_DIRTY` 及数据库状态；当前值不由这份源码锁推断。
模型默认值、文档引用策略、数据库触发器和挂载配置属于另行管理的部署状态，镜像回滚不能自动还原它们。
保留此历史清单；以后有意修改这些源码应新增构建记录和版本锁，不能覆盖旧清单来掩盖差异。

本次没有修改 WeKnora UI，同事维护的 UI 基础镜像源码对应关系仍应单独确认。
