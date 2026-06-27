# Taxonomies

Use these seed libraries for initial matching. Update them daily from selected article evidence.

## Keyword Library

General technology governance:

- 正确政绩观
- 高质量发展考核
- 科学决策

Marine oil and gas R&D and production:

- 增储上产
- 能源安全
- 油气保供
- 高水平科技自立自强
- 新质生产力
- 未来产业
- 科技创新与产业创新深度融合
- 科技成果转化
- 技术经理人
- 数智化转型
- 人工智能+
- 数据要素
- 智能体
- 词元
- 海洋经济高质量发展
- 深远海开发
- 海上风电
- 潮流能
- 深海油气
- 注碳增气
- 海上CCUS
- 氢能
- 储能
- 新型电力系统
- 碳达峰碳中和
- 节能降碳
- 绿色转型
- 区域协同
- 京津冀
- 粤港澳
- 长三角
- 中试平台
- 概念验证
- 产学研融合
- 标准化
- 知识产权
- 专利转化
- 基础研究
- 原始创新
- 非共识项目
- 科技人才
- 青年科技人才
- 揭榜挂帅
- 科技安全
- 深海极地
- 战略资源

## Business Tag Library

| 业务大类 | 专业方向 | 业务标签 |
| --- | --- | --- |
| 生产项目 | 勘探 | 油气勘探、储量评价 |
| 生产项目 | 开发 | 油气田开发、深海油气 |
| 生产项目 | 工程 | 海洋工程、海上风电安装 |
| 生产项目 | 钻（完）井 | 高压井、深水钻井 |
| 生产项目 | 新能源 | 海上风电、氢能、储能、潮流能 |
| 生产项目 | 数智化 | 智能油田、数字孪生 |
| 科技项目 | 勘探 | 基础地质研究 |
| 科技项目 | 开发 | 提高采收率、注碳增气 |
| 科技项目 | 工程 | 深海装备、水下生产系统 |
| 科技项目 | 钻（完）井 | 智能钻井 |
| 科技项目 | 新能源 | 氢能制储、储能技术 |
| 科技项目 | 数智化 | AI大模型、科学计算 |
| 科技项目 | 水合物 | 天然气水合物试采 |
| 科技交流与协作 | - | 国家重点实验室、学协会、学术交流、科协、主任基金 |
| 科技成果转化 | - | 科技成果（转化）、知识产权、科研设备、科技奖励 |
| 科技资产 | - | 企业资质、软件管理、数据资产 |
| 科技创新 | - | 基础研究、未来产业、战新产业、科技人才 |
| 标准化 | - | 标准（国标、团标、企标） |

## Focus-Content Taxonomy

- 国家政策、政策解读
- 国家部署工作、重点任务
- 理论学习、解读
- 政策导向
- 潜在资源
- 潜在机遇
- 潜在风险
- 优秀做法、经验

## HTML Highlighting Rules

Use these styles in the final `daily_report.html` article detail blocks:

| 内容类型 | CSS class | 视觉标注 |
| --- | --- | --- |
| 国家领导原话 | `leader-quote` | 加粗 |
| 国家政策 | `national-policy` | 下划线 |
| 国家部署工作或任务 | `national-task` | 高黄底 |
| 政策导向 | `policy-direction` | 黄色底 |
| 潜在资源 | `potential-resource` | 蓝色 |
| 潜在机遇 | `potential-opportunity` | 绿色 |
| 潜在风险 | `potential-risk` | 红色 |
| 优秀做法、经验 | `good-practice` | 绿色浅底 |

When generating JSONL records for rendering, store the full paragraph context and mark only the target spans:

```json
{
  "type": "潜在机遇",
  "context": "整段上下文原文，包含需要标注的局部内容，便于用户理解前后语义。",
  "text": "需要标注的局部内容",
  "marks": [
    {
      "type": "潜在机遇",
      "text": "需要标注的局部内容"
    }
  ],
  "note": "可选：为什么这样标注"
}
```

## Dynamic Update Rules

- Add a new keyword when it appears in an official source and has clear relevance to technology management, energy security, marine economy, oil and gas R&D, innovation policy, digital transformation, standards, IP, talent, or research governance.
- Add a new business tag only when it maps to a repeated business activity or management object, not a one-off phrase.
- Add a new focus standard only when existing categories cannot faithfully describe the evidence.
- For every added or reinforced term, record: `term`, `category`, `evidence_url`, `evidence_title`, `first_seen`, `reason`, and `status` (`new` or `reinforced`).

## Keyword Constraint Notes

Use broad keywords only with scope qualifiers:

- `绿色转型` / `节能降碳` / `新能源`: require marine or offshore qualifiers such as 海洋新能源、海上风电、深远海风电、氢能、氢电、海陆一体、绿电存储、源网荷储、储能、潮流能、海上CCUS、海上平台节能.
- `人工智能+` / `数据要素` / `智能体`: require relevance to energy, oil and gas, marine, offshore, enterprise R&D, scientific research, standards, IP, data assets, project management, safety, or production operations.
- `新质生产力` / `未来产业` / `科技创新`: require a management signal such as policy deployment, project mechanism, funding/platform, pilot/demo, standards, IP, talent, research organization, technology transfer, or enterprise implementation path.
