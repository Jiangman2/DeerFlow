# 文件与数据协议

所有JSON UTF-8，路径相对app根目录，日期ISO `YYYY-MM-DD`，时间包含时区。模板使用JSON字段，不读自然语言说明猜数值。样例均已提供于app/data，比简化示意更权威。

## energy_T.json

必需data_date=T、start/end、oil[3]、gas[3]、freight[8]、history、observations、retrieved_at。

报价字段：name、code、unit、date、value、previous_date、previous_value、change、source、contract、basis、decimals、change_method、status。数值为JSON number或null；change单位是百分数，不是小数比例。1.25表示1.25%。previous_date必须早于date。unit不同不计算change。code固定见API表，contract为接口item原文。

history包含start/end/single_day和oil/gas/freight各序列。每序列name、code、unit、axis、dates、values、break_before。dates与values等长、日期严格递增；break_before是折线分段起点的数组下标，表示缺失处不要连接。Henry Hub axis=right，其余left。单位切换形成新序列段，不能把元/吨数据绘在万美元轴上。

observations保存每个请求日/每品种的date、code、item、unit、value、status、raw_close。不删除休市记录以伪装满数据；图只取有数值点。原始响应在sources/jkx_new_日期.json。

## financial_T.json

data_date=T，stocks按海油A/海油H/中石油/中石化/沪A顺序5条，fx按美元指数/USD-CNY顺序2条。

每条name、code、unit、date、value、change、source、url、basis、retrieved_at、evidence。evidence为文件路径或路径列表，必须真实存在。正常计算change_method=calculated，previous_value>0、previous_date<T，严格计算后再展示舍入。引用报道百分比为reported，保留原文百分比，前值可null，不造假。

休市或缺失：status=non_trading或missing，value/change=null，missing_reason写明；date仍为目标T，不能改成P。已知价格却没有可核实前值时value有值、change=null，change_method=missing_previous。企业版校验和渲染支持上述状态；基准样例全部有值，不影响样式对照。

来源名称随实际变化，来源列表从快照取值。USD/CNY decimals=4，其他金融默认2。basis含16:30时模板自动显示16:30标签，否则写日线收盘；必须按证据填写。

## news_D.json

publication_date=D、news_as_of（该期新闻截稿）、retrieved_at（实际检索）、market_news[3..5]、company_news[至少5]。回溯生成可增加edition_basis，不能把实际检索时间改写成历史时间。快照本身不执行搜索。

每条title（不重复加末尾句号）、text、source、published、url、retrieved_at、evidence；建议original_title、published_at、event_date。有published_at时必须不晚于news_as_of，只有日期的来源要阅读原文确认截稿，不能假装精确到分钟。示例部分历史文章只有published日期，以实际归档原文为准。

title为短标题，text含完整主体身份、关键事实和必要条件，不把未来计划写成已实现。URL必须可追溯，不能用搜索结果页或首页替代原文。程序检查重复URL；语义重复、同稿不同URL、无新增进展复用旧闻仍需agent判断，不能误称程序能完全自动判断。

## 输出

app/output/油气与金融市场日报_D.html、.png、.json。JSON是渲染用全量快照，包含sources文件名及SHA256。HTML内联样式/SVG，可离线查看；新闻与行情链接联网打开。PNG宽780像素，可直接手机阅读。修订覆盖指定D，不误生成今日新期次；关键数据修正先保留旧源版本。
