# Haiyou Pure SVG - Design Specification

> Clean CNOOC-style template for formal project reporting and executive communication.

## I. Template Overview
- Template ID: `haiyou_pure_svg`
- Style: parsed and reconstructed from `母版.pptx`, preserving CNOOC visual language with blue/red accents.
- Scenarios: project initiation, management review, technical proposal, architecture briefing.

## II. Canvas Specification
- Format: 16:9
- SVG viewBox: `0 0 1280 720`
- Safe area: left/right 72px, top 72px, bottom 72px

## III. Color Scheme
- Primary blue: `#0060A6`
- Accent red: `#DE002B`
- Text dark: `#1F2D3D`
- Text secondary: `#6A7785`
- Divider gray: `#D9DEE5`
- Background: `#FFFFFF`

## IV. Typography System
- Title: `Microsoft YaHei` 46/54 semi-bold
- Subtitle: `Microsoft YaHei` 24/32 normal
- Body: `Microsoft YaHei` 20/30 normal
- Footer: `Microsoft YaHei` 13/18 normal

## V. Page Structure
- Header strip for identity accents
- Middle block for title/content
- Footer strip for organization and page number
- Cover uses `muban_cover_bg.png` as fixed background image and `cnooc_logo_white.png` as top-left white logo.

## VI. Page Types
- Cover page
- TOC page
- Chapter page
- Content page
- Ending page
- TOC/Chapter/Content pages use `muban_inner_bg.png` as shared inner-page background.
- Ending page uses `muban_ending_bg.jpeg` as fixed background with centered closing text.

## VII. Layout Modes (Recommended)
- Cover: blue hero block + white title hierarchy (aligned with pure PPTX visual language)
- TOC: left vertical `CONTENTS` + right 01/02/03 primary items
- Chapter: centered chapter number, tricolor divider, centered chapter title
- Content: top title hierarchy + subtitle area + bottom organization/footer line
- Ending: full-bleed image + centered thank-you and contact line

## VIII. Spacing Specification
- Grid unit: 24px
- Title to subtitle gap: 20px
- Title to content box gap: 28px
- Footer margin: 16px

## IX. SVG Technical Constraints
- Inline style only
- Avoid script/animate/foreignObject
- Keep shape geometry deterministic

## X. Placeholder Specification
- Cover: `{{TITLE}}`, `{{SUBTITLE}}`, `{{DATE}}`, `{{AUTHOR}}`
- TOC: `{{TOC_ITEM_1_TITLE}}` ~ `{{TOC_ITEM_6_TITLE}}`
- Chapter: `{{CHAPTER_NUM}}`, `{{CHAPTER_TITLE}}`
- Content: `{{PAGE_TITLE}}`, `{{CONTENT_AREA}}`, `{{PAGE_NUM}}`
- Ending: `{{THANK_YOU}}`, `{{CONTACT_INFO}}`

## XI. Usage Guide (Recommended)
- Keep business content concise and structured.
- Use red only for key highlights.
- Place charts and tables within content safe box.
