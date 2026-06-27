# Source Catalog

Prioritize official sources and original URLs. When a source has multiple sections, check the most specific section first.

## News and Journals: Information / Theory Source Type

| Source | URL | Notes |
| --- | --- | --- |
| 人民日报 | http://paper.people.com.cn/rmrb/pc/layout/202604/27/node_01.html | Use the date-specific layout page when available. For a single article URL, inspect neighboring layout links to discover same-day articles. |
| 求是杂志 | https://www.qstheory.cn/20251231/2d916da295774130ac2fb223fd208895/c.html | Prefer current issue and policy-theory articles related to science, innovation, energy, and high-quality development. |

For these sources, classify selected items as `source_type=资讯类信息` unless the article is clearly a policy-theory interpretation. Include only if it has policy direction, national deployment, excellent practice, experience, or strategic interpretation relevant to technology management.

## Ministries and Commissions: Policy / Policy Interpretation Source Type

### 国家发展改革委

- Home: https://www.ndrc.gov.cn/
- 通知公告: https://www.ndrc.gov.cn/xwdt/tzgg/
- 动态-委领导: https://www.ndrc.gov.cn/xwdt/dt/wlddt/
- 政策: https://www.ndrc.gov.cn/xxgk/zcfb/tz/
- 政策解读: https://www.ndrc.gov.cn/xxgk/jd/jd/

### 科学技术部

- Home: https://www.most.gov.cn/index.html
- 通知公告: https://www.most.gov.cn/tztg/
- 部门规章: https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/bmgz/
- 规范性文件: https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/gfxwj/
- 政策解读: https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/fgzc/zcjd/
- 科技统计: https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/kjtjbg/
- 国家科技奖励: https://www.most.gov.cn/xxgk/xinxifenlei/fdzdgknr/gjkjjl/
- 科技政策-综合: https://www.most.gov.cn/satp/kjzc/zh/
- 科技计划管理: https://www.most.gov.cn/satp/kjzc/kjjhgl/
- 企业科技创新: https://www.most.gov.cn/satp/kjzc/qykjcx/
- 科技奖励: https://www.most.gov.cn/satp/kjzc/kjjl/
- 国际科技合作: https://www.most.gov.cn/satp/kjzc/gjkjhz/

### 工业和信息化部

- Home: https://www.miit.gov.cn/
- 工信动态: https://www.miit.gov.cn/xwfb/gxdt/index.html
- 部领导活动: https://www.miit.gov.cn/xwfb/bldhd/index.html
- 政策文件: https://www.miit.gov.cn/search/zcwjk.html?websiteid=110000000000000&pg=&p=&tpl=14&category=183&q=
- 部门规章: https://www.miit.gov.cn/search/bmgzk.html?websiteid=110000000000000&pg=&p=&tpl=14&category=181&q=
- 行政规范性文件: https://www.miit.gov.cn/zc/wjxzfl/xzgfxwj/index.html
- 政策解读: https://www.miit.gov.cn/zwgk/zcjd/index.html

### 国务院国资委

- Home: http://www.sasac.gov.cn/n2588035/index.html
- 政策发布: http://www.sasac.gov.cn/n2588035/n2588320/n2588335/index.html
- 政策解读: http://www.sasac.gov.cn/n2588035/n2588320/n2588340/index.html
- 政策图解: http://www.sasac.gov.cn/n2588035/n16743047/index.html
- 国资数据-央企: http://www.sasac.gov.cn/n16582853/n16582883/index.html
- 监管动态-科技创新: http://www.sasac.gov.cn/n2588030/n2588934/index.html
- 监管动态-国企改革: http://www.sasac.gov.cn/n2588030/n2588924/index.html
- 监管动态-国际合作: http://www.sasac.gov.cn/n2588030/n2588949/index.html

### 国家能源局

- Home: http://www.nea.gov.cn/
- 最新文件: https://www.nea.gov.cn/policy/zxwj.htm
- 通知: https://www.nea.gov.cn/policy/tz.htm
- 公告: https://www.nea.gov.cn/policy/gg.htm
- 解读: https://www.nea.gov.cn/policy/gg.htm
- 局工作动态: https://www.nea.gov.cn/news/jwzdt.htm

### 自然资源部

- Home: https://www.mnr.gov.cn/
- 政策法规库: https://f.mnr.gov.cn/
- 动态-海洋: https://www.mnr.gov.cn/dt/hy
- 政策解读: https://www.mnr.gov.cn/gk/zcjd/
- 数据服务-矿产: https://www.mnr.gov.cn/sj/sjfw/kc_19263/
- 数据服务-海洋: https://www.mnr.gov.cn/sj/sjfw/hy/

## WeChat Public Accounts

WeChat public account pages may block normal browser collection. Use accessible article URLs, homepage metadata, or the official WeChat API when valid credentials are provided at runtime. Classify selected CNOOC items as `source_type=领导讲话/批示指示类信息` when they are speeches, instructions, written comments, meetings, inspections, or research deployment by CNOOC Group or Research Institute leaders.

Security handling:

- Read AppID from `WECHAT_MP_APP_ID`.
- Read AppSecret from `WECHAT_MP_APP_SECRET`.
- Never store AppSecret in repository files, skill files, reports, prompts, or logs.
- Do not print access tokens unless the user explicitly asks for debugging and understands the risk.
- Cache short-lived tokens only under the daily output directory, preferably `.secrets/access_token.json`, and keep that path out of final reports.

| Account | URL | Notes |
| --- | --- | --- |
| 中国海油 | https://mp.weixin.qq.com/mp/homepage?__biz=MjM5OTk4NDQxMg%3D%3D&hid=17&sn=5e3253f4466c42a05889933cf025b44e&scene=126#wechat_redirect | Articles and leadership speeches. |
| 中国海油-增储上产专题 | https://mp.weixin.qq.com/mp/homepage?__biz=MjM5OTk4NDQxMg%3D%3D&hid=16&sn=e64d0c8fc7c33626efb4e6efc4a14041&scene=126#wechat_redirect | Use for production and energy-security intelligence. |
| 中国海油-绿色低碳专题 | https://mp.weixin.qq.com/mp/homepage?__biz=MjM5OTk4NDQxMg%3D%3D&hid=17&sn=5e3253f4466c42a05889933cf025b44e&scene=126#wechat_redirect | Use for low-carbon and green-transition intelligence. |
| 中海油研究总院 | Use WeChat API or individual article URLs | Runtime credentials may authorize API access for this account. |
