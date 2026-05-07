---
name: web-opener
description: 通过站点名称匹配并返回可点击超链接，由前端点击打开网页。用户说“打开某网站/仅输入站点名”或需要访问企业内网站点时调用。
---

# Web Opener

用于把用户的自然语言站点名称映射到 URL，并执行“返回超链接，由前端点击打开”。

## 强制规则（必须遵守）

1. 用户输入包含“打开 + 站点名”或仅输入站点名时，第一步必须走 `site_router.py --query` 路由。
2. 对已配置站点，禁止先走 `web_search`，禁止要求用户再次提供 URL。
3. 对命中结果，直接返回 markdown 超链接，不做额外检索。
4. 仅当 `site_router.py` 明确返回 `matched=false` 时，才允许提供候选站点或请求补充信息。

## 适用场景

- 用户输入短站点名，如“百度”
- 用户说“打开 xxx 网站”“进入 xxx 系统”
- 用户需要访问企业内网站点（如“勘探开发图文智能检索工具”）

## 执行步骤

1. 使用路由脚本解析用户输入并匹配站点。
2. 若匹配成功：
   - 回复用户一个 markdown 超链接，例如 `[百度](https://www.baidu.com)`。
   - 不再尝试由后端自动拉起浏览器，统一由前端点击打开。
3. 若未匹配成功：
   - 返回最接近的候选站点。
   - 引导用户补充站点名，或新增映射。

## 命令

优先使用容器路径（Docker 部署）：

```bash
python /app/skills/public/web-opener/scripts/site_router.py --query "<用户输入>"
```

如在沙箱路径下：

```bash
python /mnt/skills/public/web-opener/scripts/site_router.py --query "<用户输入>"
```

## 已配置站点（示例）

- `百度` -> `https://www.baidu.com`
- `勘探开发图文智能检索工具` -> `http://10.57.128.79:8100/`
- `科研报告自动生产智能体` -> `http://10.57.128.79:7860/`
- `油气专业文档智能审查工具` -> `http://10.57.128.79:5001/`
- `源头采集自动化工具` -> `http://10.57.128.79:5000/`

## 维护站点映射

映射文件：`skills/public/web-opener/sites.json`

新增站点（示例）：

```bash
python /app/skills/public/web-opener/scripts/site_router.py \
  --add-name "新系统名称" \
  --add-url "http://10.0.0.1:8080/" \
  --add-aliases "别名1,别名2"
```

列出全部站点：

```bash
python /app/skills/public/web-opener/scripts/site_router.py --list
```

## 返回格式要求

- 成功时统一返回：
  - `已匹配站点：[站点名](URL)，请点击链接访问。`
