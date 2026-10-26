# 生产源码归档：2026-10-26

本轮从已合并 PR #15 的 `59f7c5382ebaac8d8b881b95f5a8c9ec5bde18df`
继续归档已发布变更。它不包含 Work 集成 PR #16、主工作目录的上游升级/P4
候选，也不会重放发布、成员授权、数据库迁移或回调。

## 现场依据

`deploy/enterprise/current-production-observation-20261026.json` 保存本机操作员
`log(26).txt` 中的脱敏采集结果及原文件摘要。采集开始于北京时间
2026-10-26 11:21:16，工具只读，部署与 ConfigMap 在采集前后稳定。
这是操作员执行结果，不是本次归档进程直接访问集群，也不是完整服务器源码导出。

| 组件 | Generation | 当前镜像/补丁 |
| --- | ---: | --- |
| Gateway | 387 | `ba642b5484ad6103146de433432d24de4d0243ecbbdd251d2752e8aa961c5e30` |
| Frontend | 229 | `cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298` |
| PPT Runner | 55 | `0b0cdbbbf95794ab0ce6f96e09f6a449152d05761c23b47dcda3d5c9cf2a3140` |
| WeKnora UI | 39 | 保留 `admin-upload-r2-4ccbd940db0c-20261022103101`，追加成员页 ConfigMap 补丁 |
| WeKnora App | 82 | 保留 `20261016-native-glm-runtimefix-fc44d25744194ece` |

Gateway、Frontend、Runner、WeKnora UI 分别为 70、2、1、1 个就绪副本。
完整 UID、spec SHA256、其余部署、挂载、镜像及配置摘要保存在现场记录中。
摘要内被隐藏的环境变量值不能据此还原；凭据不属于代码归档内容。

## 已发布变化

- Frontend 228/229：移动端去掉红字和多余的消息区顶部间距；桌面仅在欢迎页面
  第一行下方显示提示，提交任务进入对话后不显示。最终源码净变化为聊天页
  `layout.tsx` 和 `page.tsx`。保留中间发布记录；`mobile-chat-spacing` 独立候选
  从未单独上线，已由联合布局包替代，不能再次执行。
- Gateway 387：增加 `weknora_company_knowledge.py`，在 `weknora_identity.py`
  的普通用户 SSO 中加入指定公司空间的 viewer 策略；两项环境配置及原有 OA
  多文件开启、回调关闭状态均保留。管理员仍保留既有 SSO 旁路。
- 创建公司制度库和共享空间、普通用户分批加入、六位现有管理员补齐成员关系：
  保存工具、测试和脱敏回执。成员关系是原生服务数据，不复制名单到仓库，不重跑授权。
- WeKnora 成员设置页：按需加载、分页创建行组件、关闭释放名单、忽略迟到响应。
  同时保存三份应用源码、一份测试，以及实际部署的静态模块补丁，详见下面的边界。
- SSL/TLS 三轮工具、测试、脱敏诊断结果独立归档；没有执行证书替换，不能据此
  宣称故障已修复。集团私钥和原始凭据不入库。

PPT Runner 相对 PR #15 没有新运行源码变化。

## WeKnora 完整镜像与本轮补丁

`release/production-source-20261025/weknora-member-ui/` 保存实际发布脚本、
完整七项 ConfigMap 载荷、包摘要和源码资格记录。
实际 ConfigMap 是 `weknora-members-fix-20261025-2cc8dbfc99d4`，UID 为
`4b8a7053-9074-48ba-b904-e80b45377990`。以采集工具相同的 JSON 规范计算
`{data, binaryData}`，摘要为
`09469fc2b7b6e4f4222f9474716fb382e333fb036d1458773019a0f21730f6ed`，
与 10 月 26 日现场记录完全一致。载荷自有摘要为
`2cc8dbfc99d4c39a7aa284fbb3e7ca5b58694043097e0e4d84a6b68fb0c59540`。

补丁在原 Office 预览 initContainer 完成后处理其 emptyDir，只替换两个模块的
输入，给整个模块依赖图增加新文件名，保留旧模块供已打开的页面使用。
不应直接重新构建较旧 vendor 全树并覆盖同事镜像。发布工具运行验收通过，
操作员随后确认设置页不再卡顿；不将这条反馈扩展成全部功能业务验收。

同事基础 UI 镜像的完整构建映射仍未提供。本轮可以证明指定镜像、部署配置和
实际运行补丁的对应关系，以及本轮源码资格检查通过；不能证明整个 WeKnora
基础镜像都可从当前 vendor 源码逐字重建。保留这一范围限制，不扩大来源声明。

## 可重复核对

默认校验锁改为 `deploy/enterprise/production-source-lock-20261026.json`：

```text
python deploy/enterprise/verify-release-source-20261023.py
python deploy/enterprise/verify-release-source-20261023.py --ref HEAD
```

校验 583 个 Gateway Python、551 个单独封存的 Runner Python、940 个前端
构建输入，以及新增 WeKnora 运行补丁。既有 13 个纯格式整理文件保留原始
生产字节，继续要求摘要和 AST 等价；并非无条件声称所有工作树字节等于镜像。
旧日期锁继续表示旧版本，只在核对相应历史提交时显式选择。

新增校验把静态载荷、源码资格摘要和独立采集的 ConfigMap/部署记录相互绑定，
防止只更新锁文件中的镜像标签就错误地宣称已对齐。测试覆盖载荷、ConfigMap、
源码、现场记录、Deployment 和镜像不一致的拒绝路径。

测试与 GitHub CI 的本轮结果记录在
`deploy/enterprise/production-alignment-validation-20261026.json`。
这次归档不触发服务滚动，也不声明真实知识问答、OA 回调或 TLS 故障已完成验收。
