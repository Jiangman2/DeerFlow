# Filtering Rules

Use these rules to narrow the collection scope before writing the daily report.

## Date Window

- Daily generation at the end of a day: include articles whose publication date is that same calendar day.
- Morning push on the next day: include articles whose publication date is the previous calendar day.
- Do not include articles outside the target publication date unless the user explicitly asks for retrospective coverage.
- If an article page is updated today but originally published outside the target date, treat it as out of scope and mention it only in collection notes when useful.

## Source Types

Collect three source types:

1. Policy and policy interpretation
   - Sources: ministries and commissions, SASAC, NEA, MNR, MOST, NDRC, MIIT.
   - Include only if directly relevant to technology management, marine energy, offshore oil and gas R&D, enterprise innovation governance, standards, IP, data assets, AI, energy security, or research project management.

2. Information/news and theory interpretation
   - Sources: People's Daily and Qiushi.
   - Include if it provides policy direction, national deployment, excellent practice, experience, technology-management signal, or strategic interpretation relevant to marine oil and gas research management.

3. Leadership speech, instruction, and written comments
   - Sources: CNOOC Group and CNOOC Research Institute public accounts or accessible official pages.
   - Include leadership speeches, instructions, written comments, research deployment, inspection remarks, conference speeches, and internal-system strategic signals related to R&D, innovation, production technology, energy security, green transition, digitalization, talent, or project management.

## Narrowing Constraints

Use modifier constraints before broad keywords. This is the preferred way to reduce false positives.

### Green transition and energy-saving terms

For broad new-energy terms such as `绿色转型`, `节能降碳`, `新能源`, `低碳`, include only when at least one marine-energy qualifier is also present:

- 海洋新能源
- 海上风电
- 深远海风电
- 氢能 / 氢电
- 海陆一体
- 绿电存储
- 源网荷储
- 储能
- 潮流能
- 海上CCUS
- 海洋油气低碳
- 海上平台节能

Do not include generic city, building, transport, agriculture, or consumer low-carbon articles unless they explicitly connect to marine energy or offshore oil and gas operations.

### Regional articles

If an article is clearly about one province, city, district, or local development zone:

- Policy and policy interpretation: do not include.
- Information/news: include only when the article is an excellent practice, transferable experience, pilot demonstration, or benchmark case relevant to technology management, marine energy, enterprise innovation, or oil and gas R&D.
- If it is neither excellent practice nor transferable experience, exclude.

### Broad digital and AI terms

For `人工智能+`, `数据要素`, `智能体`, `数字化`, include only when connected to at least one of:

- energy, oil and gas, marine, offshore, enterprise R&D, scientific research, standards, IP, data assets, project management, safety, or production operations.

### Broad innovation terms

For `新质生产力`, `未来产业`, `科技创新`, include only when the article contains an actionable management signal:

- policy deployment, project mechanism, funding or platform, pilot/demo, standards, IP, talent, research organization, technology transfer, or enterprise implementation path.

## Exclusion Notes

When excluding an otherwise keyword-matched article, keep a short note in collection notes only if it helps explain coverage:

- out of target publication date
- regional policy without transferable practice
- broad low-carbon article without marine-energy qualifier
- broad digital article without technology-management or energy relevance
