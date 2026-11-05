---
deck_id: cnooc
kind: deck
category: brand
summary: 中国海油项目汇报、管理评审、技术方案与专业交流，用于形成清晰判断、统一行动并支持正式交付；采用海洋蓝、品牌红与海上能源场景视觉。
keywords: [中国海油, CNOOC, 海上能源, 项目汇报, 技术方案]
primary_color: "#0060A6"
canvas_format: ppt169
canvas_width: 1280
canvas_height: 720
canvas_viewbox: "0 0 1280 720"
source_canvas_width: 1280
source_canvas_height: 720
source_viewbox: "0 0 1280 720"
replication_mode: fidelity
native_structure_mode: structured
page_count: 5
---

# 中国海油 — Design Specification

## I. Template Overview

| Application context | Definition |
| --- | --- |
| Recurring presentation family | 中国海油项目立项、管理评审、技术方案、专业交流和对外汇报 |
| Intended audiences and outcomes | 面向管理决策者、项目干系人、专业技术人员与合作伙伴；帮助受众快速理解主题、依据与建议，形成判断并明确下一步 |
| Delivery and reading assumptions | 以正式会议讲解为主，同时支持会后流转、审阅和归档；页面应在脱离口头讲解后仍能辨认结论、证据与责任信息 |
| Representative narrative/page roles | 当前原型覆盖封面、目录、章节、开放内容和结束语；具体选页、重复、顺序与内容由当前项目的 Strategist 根据材料决定 |

- 视觉体系来自所提供的中国海油模板素材，保留海洋蓝、品牌红、海上平台与能源工业图形。
- 结构采用一个 `CNOOC Corporate` Master 和五个用途明确的 Layout；品牌背景属于各 Layout，避免把不同场景图片错误固化为多个重复 Master。
- 正文页保留大面积开放内容区，可承载原生图表、表格、流程图、图片或组合信息模块。

## II. Color Scheme

| Role | Color | Application |
| --- | --- | --- |
| CNOOC blue | #0060A6 | 主标题、章节编号、结构线与关键强调 |
| Deep ocean blue | #004C8B | 深色文字、封面与结束页信息承载区 |
| CNOOC red | #DE002B | 关键节点、强调线与品牌识别 |
| Text dark | #1F2D3D | 正文标题与主要文字 |
| Text secondary | #6A7785 | 辅助说明、来源与页脚 |
| Divider gray | #D9DEE5 | 分隔线、边框与弱层级结构 |
| White | #FFFFFF | 页面背景与深色场景中的反白文字 |

## III. Typography

| Role | Font stack | Application |
| --- | --- | --- |
| Chinese title and body | `"Microsoft YaHei", "PingFang SC", Arial, sans-serif` | 标题、正文、目录项与中文说明 |
| Latin label and folio | `Arial, "Microsoft YaHei", sans-serif` | CNOOC 英文标识、编号、日期与页码 |

字体栈仅使用常见系统字体；Windows 优先微软雅黑，macOS 可回退苹方，不依赖额外字体安装。

## IV. Signature Design Elements

- 封面使用深海蓝左侧标题区与右侧海上平台场景，左上保留白色中国海油标识。
- 目录、章节和内容页使用白色或浅蓝底，配合海洋工程剪影、蓝红双品牌线和右上角标识。
- 章节页以大号章节编号、蓝红分隔线和居中标题构成清晰过渡。
- 内容页使用开放式 `object` slot，允许项目根据实际材料组织原生图表、表格、流程与图片，不把示例文字固化进模板。
- 结束页使用海上平台全景与半透明深海蓝信息卡，保证结束语在复杂照片上仍具备可读性。

## V. Page Roster

| File | Master | Layout key | PowerPoint picker name | Visual character | Reusable slots |
| --- | --- | --- | --- | --- | --- |
| `01_cover.svg` | CNOOC Corporate | cover | Cover | 深海蓝标题区、海上平台场景与白色品牌标识 | 标题、副标题、汇报单位、日期 |
| `02_toc.svg` | CNOOC Corporate | agenda | Agenda | 海洋工程剪影、纵向目录标识与四行编号目录 | 页面标题、四个目录项、页码 |
| `03_chapter.svg` | CNOOC Corporate | section | Section Header | 淡色产业剪影、大号章节编号与蓝红分隔线 | 章节号、章节标题、章节副标题 |
| `04_content.svg` | CNOOC Corporate | content | Title and Content | 品牌页眉页脚与大面积开放内容区 | 页面标题、内容对象、来源、页码 |
| `05_ending.svg` | CNOOC Corporate | closing | Closing | 海上平台全景与半透明深海蓝结束信息卡 | 结束标题、副标题、联系信息、页码 |

## VI. Assets

| File | Intended usage |
| --- | --- |
| cover_bg.png | 封面全幅海上平台背景 |
| toc_bg.png | 目录页浅色海洋工程剪影背景 |
| chapter_bg.png | 章节页淡色产业剪影背景 |
| content_bg.png | 正文页品牌页眉、页脚和留白背景 |
| ending_bg.png | 结束页海上平台全景背景 |
| logo_white.png | 深色背景上的中国海油标识 |
| logo_color.png | 浅色背景或项目内新增页面可复用的标准彩色标识 |
