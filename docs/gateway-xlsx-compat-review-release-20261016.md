# Gateway XLSX 修复审查与生产上线步骤

日期：2026-10-16（香港时间）
审查提交：985be917bf51996cec297e89afc0a6c2f8cbe957
远端分支：origin/fix/gateway-xlsx-defaultcolwidthpt-compat

## 后续方案调整（2026-10-16）

用户确认改用转换入口方案：兼容逻辑已从 backend/sitecustomize.py 移至
backend/packages/harness/deerflow/utils/file_conversion.py，仅在 .xlsx 进入
MarkItDown 前按需安装。启动时不加载 openpyxl、不安装 XLSX 补丁；首次 XLSX
转换后构造函数仍在当前进程中保持兼容。原有 Windows 事件循环设置保留。

以下审查发现和首次测试数据记录的是原提交 985be917b 的历史状态。
生产步骤已按新方案更新；不再使用 sitecustomize 自动加载 marker 作为发布探针。

最终验证：上述三个相关测试文件在普通启动与 Gateway 启动路径两种模式下均为
54 passed；Ruff 静态检查、格式检查及 git diff --check 通过。原始故障文件和生产
HTTP 上传仍需在候选镜像/上线验收时验证。

## 原提交审查结论

修复方向正确，但当前提交不应直接标记为通过发布验收。需要先修复测试隔离和发布文档，再通过候选镜像及原始故障文件验收。本次未修改业务代码、未推送、未访问或变更生产集群。

最初本地没有该提交；本次已从 origin 取得指定分支，未切换当前工作区。总结中“未 push”已不符合当前远端状态。

## 审查发现

### [P2] 测试没有恢复未打补丁的构造函数，真实启动方式下有 3 项失败

定位：提交中的 backend/tests/test_gateway_sitecustomize_xlsx_compat.py:62-71、107-111。

_reset_marker() 只是把当前 __init__ 设置回它自己，没有实现注释承诺的“还原 pristine init”。PYTHONPATH 包含 backend 时，sitecustomize 在 pytest 前已安装补丁，因此新增的未打补丁复现测试不再抛异常；既有 WeKnora 测试同样受影响，安装函数返回 False 而非预期 True。

建议：把未打补丁场景放在受控子进程中，确保启动时不加载仓库 sitecustomize；或在测试局部沿 marker 和 __wrapped__ 还原原始构造函数，再用 monkeypatch 确保 teardown 恢复。两个兼容测试文件都必须处理启动已打补丁的情况。保留独立子进程验证“自动加载”，不能只手动导入模块来代替。

### [P2] 发布说明误导为无需 rollout 或旧容器重启即可生效

定位：提交中的 k8s/enterprise/README.md:452-454；backend/docs/FILE_UPLOAD.md 新增说明。

源文件在本地修改或提交后，生产旧镜像不会自动获得补丁。应明确先构建包含该文件的新镜像、分发到节点并更新工作负载镜像，再完成 rollout。若生产通过挂载覆盖源码，必须独立核对实际挂载来源。README 中 DEERFLOW_UPLOAD_DOCUMENT_REQUIRE_PARsed_TEXT 也应改为 DEERFLOW_UPLOAD_DOCUMENT_REQUIRE_PARSED_TEXT。

## 已完成验证与限制

从指定提交提取两个测试文件、sitecustomize.py 和 WeKnora shim 到临时目录，使用现有 backend/.venv 解释器及 openpyxl 3.1.5、pytest 9.0.3；没有加载项目 conftest，也没有执行完整后端套件。

| 场景 | 结果 |
| --- | --- |
| 未自动加载补丁，测试内部手动导入 | 7 passed |
| PYTHONPATH 指向候选 backend，启动时自动加载 | 3 failed / 4 passed |
| 自动加载后，受影响合成 XLSX 经 MarkItDown 转换，保留“井号”和 NB27-1-1 | PASS |
| 补丁下传入其他未知属性仍抛 TypeError | PASS |

失败项：新增 test_unpatched_openpyxl_still_reproduces_production_failure；既有 test_unpatched_openpyxl_reproduces_production_failure；既有 test_compatibility_shim_ignores_only_unsupported_formatting_attribute。

兼容层仅删除 defaultColWidthPt 参数，保留其他参数和错误，没有重写上传原件，且通过 marker 防止重复包裹。这能解释并修复已记录的故障类型。尚未取得原始故障 XLSX，不能把合成文件测试等同于原文件或生产 HTTP 上传验收。

原提交的 sitecustomize 为全进程启动钩子，会提前导入 openpyxl；当前 except Exception 会静默跳过导入失败，因此健康检查成功不能证明补丁生效。这是旧方案的风险记录；当前转换入口方案改为核对实际转换模块和 HTTP 功能，不要求启动 marker。

## 上线步骤（由生产操作人员执行，以下命令为 Linux shell）

### 1. 固定发布输入与回滚信息

修复上述审查项后形成新的候选提交；登记旧、新源码提交、镜像 tag/digest、转换模块 SHA256、测试记录和原文件验收记录。以实际生产当前镜像为基础保持已有修复，不能直接用仓库历史 kustomization 的旧镜像覆盖生产。

```bash
kubectl config current-context
kubectl -n deer-flow get deploy gateway -o jsonpath='{.spec.replicas}{"\n"}{range .spec.template.spec.initContainers[*]}init/{.name}{" "}{.image}{"\n"}{end}{range .spec.template.spec.containers[*]}{.name}{" "}{.image}{"\n"}{end}'
kubectl -n deer-flow rollout history deploy/gateway
kubectl -n deer-flow get pods -l app=gateway -o wide
```

记录实际副本数，不把文档中的 70 当作当前事实。记录 strategy、readinessProbe、启动命令、volumeMounts 和 imagePullPolicy。检查挂载是否遮蔽 /app/backend/sitecustomize.py。部署导出资料按生产运维规则保存，不提交带运行配置的完整 YAML。

### 2. 构建并验证候选镜像

使用当前生产认可的 ARM64 构建链；确保最终镜像的 harness deerflow/utils/file_conversion.py 含修复，而不是只改到构建目录。不要在生产容器临时安装依赖。运行上述两种启动模式的回归，并增加真实 MarkItDown 转换与 Gateway 上传链路验收。

企业清单采用 imagePullPolicy: Never。实际环境若仍如此，新镜像须先导入全部可调度节点的 k8s.io containerd namespace，并核对架构与镜像标识。使用新不可变 tag；不要覆盖旧 tag。同步更新受控发布清单及版本记录，避免后续声明式部署退回旧镜像。

### 3. 候选环境/隔离单副本验收

先部署候选环境，或使用独立 Service/标签且不混入生产 Service 的单副本候选。候选实例保留相同认证和解析约束。验证：标准 XLSX、合成 defaultColWidthPt XLSX、原始故障 XLSX、损坏或加密 XLSX 的既有拒绝行为。

对正常上传，检查响应包含有效的 markdown_file / markdown_virtual_path，Markdown 有真实中文字段和数据，再启动正常任务并确认能读取内容；不能只看 HTTP 200。比较原件哈希，确认未被兼容补丁改写。负例不应生成伪成功或启动空白任务。

### 4. 更新生产 Gateway 镜像

安排低负载或维护窗口，观察在途上传和流式任务，使用现有优雅终止机制。readiness 成功并不能保证长任务不被滚动更新打断。

以下是更新镜像的操作示例；须先填入已验收的新镜像，并将同一变更固化到生产发布清单。不要对整个历史 enterprise overlay 直接 apply。

```bash
NEW_IMAGE='替换为已验收且已分发的新Gateway镜像'
kubectl -n deer-flow set image deploy/gateway gateway="$NEW_IMAGE"
kubectl -n deer-flow rollout status deploy/gateway --timeout=30m
```

仓库 Gateway 另有 seed-extensions-config initContainer 使用 Gateway 镜像。实际部署若也如此，发布前确认其保持旧版本还是一起更新；记录理由和回滚值。不要误改独立 ppt-master-runner。

maxUnavailable: 0 / maxSurge: 1 可限制同时替换规模，但不是每批人工验收式灰度；真正的流量灰度应通过独立候选 Deployment/Service 及现有流量机制完成。set image 已触发 rollout，无需追加 rollout restart。超时只表示需诊断，不表示已经自动回滚。

### 5. 每个新 Pod 核对转换模块

逐一确认所有 Gateway Pod 已 Ready、旧 ReplicaSet 已退出、镜像标识符合新版本，
且 REQUIRE_PARSED_TEXT 仍为 true。记录实际服务解释器加载的
`deerflow.utils.file_conversion.__file__`，校验该文件的 SHA256 与候选镜像一致。
注意：导入模块不应安装兼容补丁；不能要求健康检查后的所有进程已有 marker。
只有真正处理 XLSX 的进程才会安装补丁。不要用临时 shell 手动安装补丁代替服务验收。

候选镜像中运行 `tests/test_gateway_xlsx_compat.py`、
`tests/test_weknora_docreader_xlsx_compat.py` 与 `tests/test_file_conversion.py`，
并确认标准 XLSX 和带 defaultColWidthPt 的 XLSX 都通过真实转换函数生成非空文本，
非 XLSX 路径不触发补丁，原件字节保持一致。随后必须按第 6 步经实际 HTTP 上传验证。

### 6. 生产功能与日志验收

使用有授权的测试账号，经正式 HTTPS 入口，在新会话上传原始故障文件和标准 XLSX。检查非空 Markdown、字段正确、任务正常启动；继续保持认证、授权和空解析拒绝策略。

```bash
kubectl -n deer-flow logs -l app=gateway -c gateway --since=30m --tail=-1 --timestamps=true --prefix=true |
  grep -E -C 3 'G0201010001001|defaultColWidthPt|Failed to convert|Document conversion returned no readable text|Local document conversion produced an empty companion'
```

区分发布前旧错误和发布后新请求。观察上传错误率、延迟、Pod 重启/OOM、CPU/内存和流式任务错误。成功日志缺失不能单独证明失败；验收以响应、实际 Markdown 与任务读取结果为准。

### 7. 回滚

出现新失败、内容丢失、启动探针失败或资源异常时停止推广。恢复步骤 1 记录的旧镜像；如果更新过 initContainer，必须一并恢复相应旧值。同步回退受控发布清单，避免后续自动部署重新上线故障版本。

```bash
OLD_IMAGE='替换为发布前登记的Gateway镜像'
kubectl -n deer-flow set image deploy/gateway gateway="$OLD_IMAGE"
kubectl -n deer-flow rollout status deploy/gateway --timeout=30m
```

确认旧镜像仍在所有需要的节点。回滚会恢复原 XLSX 兼容缺陷，向用户说明该限制；不要关闭 REQUIRE_PARSED_TEXT 来掩盖问题。此修复不需要数据库迁移或重启 WeKnora、Sandbox、PPT Runner。

## 参考

- Python 启动 sitecustomize：https://docs.python.org/3.12/library/site.html#sitecustomize
- Kubernetes Deployment 更新与回滚：https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
- Kubernetes 日志参数：https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/
