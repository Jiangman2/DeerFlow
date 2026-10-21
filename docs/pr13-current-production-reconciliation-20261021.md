# PR #13 当前生产配置核对

依据用户返回的 `log(19).txt`，采集开始于 2026-10-21 16:46:08（北京时间），耗时 9.44 秒。37 次只读查询确认选中的工作负载 spec 和挂载 ConfigMap 在采集前后稳定。没有执行容器命令、读取 Secret 内容或修改服务。

原始脱敏结构及输入文件摘要归档于 [现场记录](../deploy/enterprise/current-production-observation-20261021.json)。它是观察记录，不能直接 `kubectl apply`；并非整个集群的原子快照。

| 服务 | generation | 就绪/期望 | 当前关键声明 |
| --- | --- | --- | --- |
| Gateway | 382 | 70/70 | 联合 Data QA / GLM 镜像 `eeeb190145dd…`；每副本请求 2 CPU / 3 GiB，上限 4 CPU / 4 GiB |
| Frontend | 225 | 2/2 | 员工目录镜像 `ee145e6357e4…`；datax-02、0015 各一副本 |
| WeKnora App | 82 | 2/2 | `20261016-native-glm-runtimefix-fc44d25744194ece`；每副本请求 2 CPU / 12 GiB，内存上限 24 GiB |
| DocReader | 20 | 1/1 | 官方 ARM64 0.8.0 镜像；请求 1 CPU / 4 GiB，内存上限 16 GiB；三个解析 worker 声明均为 1 |
| WeKnora UI | 37 | 1/1 | `preupload10-f25aea4dfb29-20261020062633`；相同镜像的 initContainer 应用 Office 预览补丁 |
| Gateway Redis | 2 | 1/1 | 单实例 StatefulSet，OnDelete；请求 1 CPU / 8 GiB，上限 4 CPU / 24 GiB |
| Dispatcher | 2 | 2/2 | `20261004-owner-cancel-r1` |
| PPT Runner | 54 | 1/1 | 镜像 `8a853325c46a…`，独立 Deployment |
| Provisioner | 85 | 1/1 | `20261005-prod-node-pool-r1` |
| Nginx | 116 | 1/1 | Service selector 为 `app=nginx-security-heai-20261012-r1` |

WeKnora 每个 App 的环境声明：core=32、postprocess=2、enrichment=64、maintenance=2、shared=2、wiki=6、model max concurrency=64。这些不是数据库覆盖值或实测吞吐量；不能据此宣称模型有效并发为 128。旧双实例记录的 enrichment=32 已不适用。Gateway embedding 并发声明为 24，也不同于旧 overlay 的 200。

## 已核实的源文件

以下两个线上 ConfigMap 的整个 `data`/`binaryData` 规范 JSON 哈希与 PR 源文件一致（按 LF 文本生成）：

- `weknora-docreader-xlsx-compat`：`7e2d1b43056ebaa61e5e92713c877a1152975ec27384ea731e394c54c30af477`，对应 `k8s/weknora/docreader-compat/{sitecustomize,web_compat,xlsx_compat}.py`。
- `weknora-v08-preview-patch`：`9c14959e63a71717c0d8fc76a0796e2c375b7607ccf8ba079ec309bf025fef71`，对应 `k8s/weknora/ui-patches/patch-legacy-office-preview.sh`。

Gateway 联合镜像的 15 项源码覆盖见[前次源码核对](pr13-deployed-source-coverage-20261021.md)。本次进一步确认 5 个节点各 14 个就绪 Gateway 使用同一摘要。就绪检查不等于业务验收。

## 仍需补齐的归档

- WeKnora UI `preupload10` 的构建源码/锁文件尚未找到匹配依据，不能用旧 UI 源码冒充。
- WeKnora App 当前镜像的完整源码构建对应关系，以及数据库中有效模型配置仍需核实。
- Nginx、WeKnora 主配置等目前只有内容摘要，不能从摘要恢复配置；需要定向脱敏配置或对应版本源文件。
- 本次采集器遗漏了以 `deer-flow-mineru-` 开头的工作负载；MinerU、Sandbox、PV/PVC、配额和网络策略不在本次完整覆盖声明内。
- Pod 镜像统计按 selector 汇总，包含非就绪历史 Pod，不能据此认定服务故障。Redis 两个 revision 字段不同也不足以证明配置未生效，需要 Pod revision/配置读回才能判定。

`k8s/enterprise` 仍是旧部署模板，不能整套用于回放当前生产状态。保留旧发布历史，当前观察记录用于逐项核对，不把未验证的主工作目录修改叠加到生产或冒称已全部归档。
