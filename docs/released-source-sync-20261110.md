# 已上线源码与发布证据同步（2026-11-10）

本次把已经交付到生产的源码和现场回执补入企业分支，不执行安装、热更、重启、数据库操作或 Work 推送。PR #42、#43 及已有前端改动不重复实现。历史封存文件保持原样，后继版本通过新增清单衔接。

## 同步范围与验收边界

| 内容 | 已部署证据 | 仍待验收 |
| --- | --- | --- |
| 财务决算 Skill | 11 月 6 日封面小号“AI自动生成”、结余外层序号；11 月 10 日支出用途说明修复。最终 31 个文件树 `0b417f6540cbab2f35ce9bbde28efe0d9d214273bf4fbf9baf47734f9ea01c85`，两次均有五节点核验及维护 Pod 清理回执 | 实际报告业务验收。旧报告不会自动改写 |
| 油气日报 | 原工程 158 文件及原 ZIP 封存不改，薄入口在任务目录执行副本。已上线对话入口修复和专用采集器代理改造，采集器为 generation 3 | 完整最新日报、新闻核实和版式业务验收；每天 7 点 Work 私聊任务尚未创建 |
| SkillHub 鉴权 | 后端 generation 15 原生 API 鉴权、Nginx 入口保护成功回执；相关产品代码已在企业分支，本次补齐构建、发布工具及证据 | 真实登录用户的浏览器业务验收 |
| Frontend 243 | PR #39 与历史输入框最多六行的发布 stdout；操作者答复“可以了” | PR #39 运行状态和文件卡片的真实任务验收不由此次输入框验收代替 |

财务原 Word 模板包含用途说明，但旧生成器在替换科目段落时将其替换成了金额和结余原因。本次归档的已上线修复恢复用途说明，保留“零预算科目省略”、金额、外委比例及后续结余原因章节。没有增加无法从材料证明的合同或合规结论。

油气采集器的现场回执确认代理路径返回 HTTP 200，目标日期 5 项有数据、9 项缺失，状态仍为 `WAIT_DATA`、`formal_report_ready=false`。这证明传输恢复，不能宣称最新完整日报已成功。凭据通过独立 Secret 接入，Git 中不包含实际凭据、Secret 实例或本机私有 JSON。公开市场样例属于原工程封存内容，不是本次新取行情。

PPT 旧公开新执行入口已退役；SkillHub 入口回执还确认其 409 保护保持。Runner 保留、普通沙箱中旧 Agent 容器的移除尚未完成，不能把本次源码同步描述为这些组件已清理。历史会话查看的业务验收也不在本次提交中补作确认。

## 证据位置

- 财务版本链：`release/research-fund-current-source-20261110/SOURCE.json`；原始回执分别保存在 `release/research-fund-report-labels-20261106-r1/` 与 `release/research-fund-expense-purposes-20261110-r1/`。
- 油气入口、代理与现场结果：`release/energy-market-released-20261110/`。保留失败尝试与修复过程，不覆盖原回执。
- SkillHub：`release/skillhub-auth-remediation-20261106-r1/`。重建 JSON 均标注 reconstructed；完整服务器报告未取得的地方，只归档操作者 stdout 和声明的服务器哈希，不冒充已下载的报告。
- Frontend 243：`release/released-sync-20261110/frontend243/`。
- 本次完整源码、文件与证据清单：`release/released-sync-20261110/SOURCE.json`。

目录中的安装器及生产命令仅为历史记录，不因进入 Git 而成为下一次执行指令。后续发布需要新的现场基线和明确操作范围。

## 可重复的离线验证

使用 Python 3.12+：

```bash
python -B deploy/enterprise/verify-released-sync-20261110.py --ref HEAD
python -B deploy/enterprise/verify-research-fund-production-source.py --ref HEAD
python -B deploy/enterprise/test_research_fund_current_source_20261110.py
```

CI 另外运行财务报告回归、采集器/薄入口/发布工具模拟测试及 SkillHub 鉴权发布边界测试。它们不联系生产、供应商或 Work。

原油气包有两份嵌套 `SKILL.md`、原 ZIP 和一个调用原生成器的 subprocess。通用扫描照常输出这六条阻断事实；CI 的 `review_energy_market_original.py` 仅在整包字节、ZIP 每个成员、生产源码哈希和完整扫描结果全部匹配时认可这六个已审查事实。新增文件、其他发现、结果缺失或源码变化都失败。平台运行时扫描及其他 Skill 的策略不变。
