# 油气与金融市场日报 · 企业 Agent 迁移包 v1.0

冻结日期：2026-11-09。最终样式以 `reference/approved-2026-11-07.html` 和同名PNG为准；数据日期2026-11-06，发布期次2026-11-07。不要让模型重新设计版式。使用 `app/report_renderer.py` 原样渲染。

## 移交清单

| 位置 | 用途 |
| --- | --- |
| `agent/SKILL.md`、`agent/AGENT_PROMPT.md` | 企业agent的技能入口、可直接复制的任务提示词 |
| `docs/01-STYLE.md` | 最终栏目、配色、字体、尺寸、图表及禁止恢复的旧内容 |
| `docs/02-API.md` | API鉴权配置、参数、14个代码、缺失值和历史取数规则 |
| `docs/03-ACQUISITION.md` | 金融行情、新闻的来源、时间、身份标注、去重规则 |
| `docs/04-EXECUTION.md` | 手动和定时流程、成功条件、锁、重试、恢复及权限边界 |
| `docs/05-DATA-CONTRACT.md` | 文件和字段协议、示例、错误处理 |
| `docs/06-DEPLOYMENT.md` | 环境安装、字体复现、网络白名单、验收与已知限制 |
| `app/` | 可运行脚本、当前样例快照和原始证据；无需旧Excel |
| `reference/` | 当前最终HTML、PNG、JSON，以及最初设计参考图 |
| `config/` | 已开启场景的调度配置、环境、字体指纹、凭证配置样例 |
| `MANIFEST.sha256.json` | 交付文件完整性校验 |
| `acceptance/` | 本机验收结果、字体与浏览器检查、像素差异结果 |
| `provenance/` | 原技能及原运行说明，仅供追溯，不作为当前执行规则 |

优先级：用户后续明确要求 > 本包agent与docs中的最终规范 > 冻结代码和样例 > provenance历史记录。发现冲突时不自行恢复旧版布局。

## 最短复现路径（离线，无需API凭证）

在已具备依赖、Microsoft YaHei/Arial、Microsoft Edge的Windows机器上，进入本包根目录：

```powershell
python tools/verify_manifest.py
python tools/check_environment.py
python tools/reproduce.py --strict
```

第三步生成 `reproduced/report.html`、`report.png`、`comparison.json`。它直接使用冻结JSON和同一渲染器，与最终样例逐字节比较HTML、逐像素比较PNG。严格模式只有两者一致才通过。字体/浏览器不同导致的图片差异会明确报告，不会声称完美一致。

也可用日期快照完整走数据装配流程（离线）：

```powershell
python app/validate_inputs.py --date 2026-11-07
python app/generate_report.py --date 2026-11-07 --png
```

## 移交给企业agent

1. 解压整个包，保持相对目录结构。将 `agent/AGENT_PROMPT.md` 作为工作流提示词，注册 `agent/SKILL.md`，授权该目录文件读写、Python和浏览器、企业搜索工具。
2. 按 `docs/06-DEPLOYMENT.md` 配置环境，先通过上述离线验收。
3. 将包外单独交付的 `api-credential.private.json` 导入企业凭证库。运行时注入 `JKX_API_PASSWORD`；不要把凭证放入新闻、模型提示词、报告、日志或版本库。公开配置样例不含实际值。
4. 手动运行新一期时，agent先获取API快照，再按检索规则准备金融、新闻快照，通过校验后出图。搜索工具由企业agent提供；本包未把任何特定企业模型/搜索接口伪装为已经接通。
5. **本包按企业定时已开启场景配置。** `config/schedule.json` 的enabled=true。企业部署人员按 `docs/04-EXECUTION.md` 注册调度：北京时间周二至周六06:00—23:55，每5分钟触发完整agent工作流。配置文件本身不是调度服务，必须在企业平台完成注册并开启。此交付场景不改变原电脑Codex任务的暂停状态。

## 复现边界

同一输入快照与冻结模板可以确定性复现；未来新闻检索属于agent判断，不能保证不同模型选出一字不差的文章。每期必须先固化已核实快照，再由模板渲染。逐像素一致要求相同字体、浏览器和渲染环境；Linux或替代字体可以运行，但不能未经比较就宣称像素一致。

字体文件及浏览器安装包未随ZIP分发。字体清单、文件哈希和环境版本已提供；企业应从有授权的Windows镜像或字体来源配置。原始API完整响应、金融证据和新闻原文已随样例归档，离线验收不依赖互联网内容仍然存在。
