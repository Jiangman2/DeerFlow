# 仅在新闻原文不足时补充

保存宿主真实抓到的原页为项目 `sources/` 下 HTML、TXT 或 Markdown，不使用搜索摘要冒充原文。写一个 JSON 数组到项目内，最多十项：

```json
[{"url":"https://媒体或公司域名/具体原文路径",
  "source":"实际媒体或公司名",
  "published_at":"实际发布时间，带时区ISO格式",
  "original_title":"原文标题",
  "evidence":"sources/实际原页.html",
  "section":"market_news"}]
```

执行 `python <Skill绝对路径>/scripts/daily.py import-news --workspace <项目绝对路径> --sources <上述JSON绝对路径>`。

程序检查日期窗口、证据路径、原文内容及页面日期冲突，回填证据哈希，更新研究清单。不从模型传入摘要覆盖原文。页面缺少机器可读时间时，模型仍须根据实际读过的原文核实时间；程序不会声称能独立证明这个事实。

`select` 可以把多个候选重新组织为市场／公司两个栏目，但不能重复同一 ID。语义去重和中文事实准确性由本次模型批量审阅，程序负责日期、来源绑定、数量、金额计算和文件一致性。
