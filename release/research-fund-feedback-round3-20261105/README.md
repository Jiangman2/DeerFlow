# 科研经费决算 Skill 第三轮反馈 2026-11-05

本批以前一批已上线的30文件源码树 `6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951` 为基线。只更新共享的 public Skill，不更新 Gateway、Frontend、Provisioner 或 PPT Runner，不访问数据库或模型。

## 修改范围

1. 外协实验／试验费的结余原因由测试化验加工费与试验费两个子项依次汇总。原父级填写文字仅作为来源保留，正式报告不会重复原因标题；子项真实缺失仍阻断。
2. 标准空表的灰色汇总格预置公式。上传表相同语义汇总格为空时在内存补算，其他公式可引用补算结果；保留原始输入，不覆盖显式错误金额或公式。年度整行增删后按标签定位。
3. 集团公司报告不再要求正式验收日期，也不校验该日期格式或与编制日期的先后。编制日期仍须有效，其他报告类型规则保持。
4. 预算执行摘要在执行率后结束，删除用户截图中“间接经费支出……万元单独列示，不纳入预算执行支出及执行率计算”一句。间接经费表格、明细和直接经费执行口径保持。

## 范围与证据

- 采用用户最后更正的 Excel，SHA256 `16cd3454cf64d908dea3dd41402ae4ef5529955ab18c9fdbf1062261e4933178`。未修改或提交含业务金额的原工作簿、报告或截图。
- 用户样表灰色位置为 E15、G15、E16:E18、B22:E22、B25:E25。公开空表保留4条年度预留行，所以对应 E16:E19、B23:E23、B26:E26，共14格。具体公式见 template-formulas.json。
- 新空表由 Artifact 工具导入、编辑与重算。导出运行时在输出完成后返回1；未将该进程标记为成功。独立核验其实际文件：只有14格公式/灰底变更，其余值、语义格式、合并关系、行高列宽保持；11个非零合成计算结果正确，公式错误扫描为0。
- 导出的绝对内部 OOXML 关系规范为指向同一成员的相对关系；非关系部件逐字节不变，不放宽 Office 审查规则。详见 template-package-qualification.json。
- operator-preflight-r3.json 保留操作员输出的原 JSON 行（含 CRLF），提取来源和哈希见独立 provenance 文件；历史清单、回执和更新工具保持不变。

## 本地验收

- 财务相关回归共312项通过；最终候选仅在该轮测试后更正一条说明文字，运行代码与模板未变，具体测试绑定见 local-behavior-regressions.json。
- 最终30文件候选树为 `0d935a5e5c45e8bf916b6b52ee310826d68acb22fffb0cbc70ab5d3304840a4c`，总计793,973字节。独立 SkillReview 复核见 skill-review-qualification-round3-20261105.json，原始发现保留，已知 Office 包与公式词法器误报仅按精确来源限定处置。
- 合成报告15页逐页检查完成，新增字段、父级原因和间接经费保留符合本轮要求；证据见 round3-word-visual-qa.json 与 word-render-receipt.json。本机无 LibreOffice，采用独立隐藏 Word 实例只读渲染，不等同于生产 LibreOffice 或业务验收。原模板已有的第12、14页空白及部分留白/跨页保持，本轮未重排。

## 发布状态

2026-11-05 操作员返回成功回执：`RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED`，操作号 `ad6bc5cd6b3a4f0e9573d631dc14defc`。共享 Skill 已从 `6c393b11…` 更新到本目录封存候选 `0d935a5e…`。五个 Gateway 节点各抽样一个 Pod，全部读到同一新树；现场263个行为测试实例和三种报告的9项 check/build/inspect 检查通过。耗时72.7秒，原版备份已核验，临时维护 Pod 已确认删除。

`operator-update-20261105.json` 保留聊天传回的完整JSON，含镜像地址的Markdown表示；本地7978字节、SHA256 `fdaee894f69469209b85764c76764f0819d2a65f7fa916c16c657d7a1b78e89c`。它不是服务器原始文件的逐字节副本。操作员报告服务器回执为 `/var/lib/portal-research-fund-skill-update-round3-20261105-r1/REPORT.json`，7258字节、SHA256 `2804e8c39c133145f676b2cc502c651d392937443c207cb6307ac6d034a83fab`；该哈希只作为操作员报告值保留，不称已重新下载复核。

本次没有 Deployment patch、Gateway滚动或数据库/模型请求。现场文件系统不支持目录交换，实际采用 `TWO_RENAMES_WITH_SHORT_NAME_GAP`；不声称原子切换、无瞬间目录空档或业务零中断。旧版备份保留于 `/mnt/skills/.research-fund-release-round3-20261105-r1-ad6bc5cd6b3a4f0e9573d631dc14defc/backup`。

服务端更新验收完成；新会话重新导入最新版Excel后的实际报告验收仍待操作员确认，不延伸为真实财务金额正确性或生产渲染验收。更新与核查脚本无需重跑。
