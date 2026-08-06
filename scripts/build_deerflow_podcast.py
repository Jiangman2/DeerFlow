"""Build spoken episode scripts from the DeerFlow internals Markdown book.

This is deliberately a deterministic first-pass adapter: it turns diagrams,
tables, lists, links, and code blocks into narration-friendly text. Human
editing can then improve pacing without touching the source book.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOK = ROOT / "docs" / "deerflow-internals-guide-zh.md"
DEFAULT_OUTPUT = ROOT / "docs" / "podcast" / "scripts"


def clean_inline(text: str) -> str:
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def diagram_narration(block: list[str]) -> str:
    labels: list[str] = []
    edges: list[str] = []
    for line in block:
        for match in re.finditer(r"(?:\w+)=?\[\"([^\"]+)\"\]", line):
            label = match.group(1).replace("\\n", "，")
            if label not in labels:
                labels.append(label)
        if "-->" in line or "-." in line:
            compact = re.sub(r"\s+", " ", line.strip())
            compact = re.sub(r"[A-Za-z_][A-Za-z0-9_]*", "", compact)
            compact = compact.replace("[", "").replace("]", "")
            compact = compact.replace("\"", "")
            compact = compact.strip(" -.:>")
            if compact and compact not in edges:
                edges.append(compact)
    if labels:
        main = "、".join(labels[:12])
        narration = f"图示口述：这张图展示了{main}之间的关系。"
    else:
        narration = "图示口述：这里是一张用于说明模块关系或执行流程的架构图。"
    if len(labels) > 12:
        narration += "图中还有更多细节，重点是这些节点按照箭头方向逐步传递状态和结果。"
    elif edges:
        narration += "整体方向是从输入开始，经过处理和执行，再回到状态、事件或最终输出。"
    return narration


def code_narration(block: list[str], language: str) -> str:
    if language.lower() == "mermaid":
        return diagram_narration(block)
    if language.lower() in {"json", "yaml", "yml", "python", "typescript", "ts", "bash", "text", ""}:
        return "代码示例口述：这里的代码用于说明数据结构或调用顺序，重点不是逐字朗读代码，而是理解它如何把输入传给下一层，并接收处理结果。"
    return "代码示例口述：这里的代码展示了一个实现细节，重点是它如何连接当前章节中的几个概念。"


def table_narration(rows: list[str]) -> str:
    meaningful = [r for r in rows if not re.match(r"^\s*\|?\s*:?-+:?", r)]
    if not meaningful:
        return "表格口述：这里列出了几个概念之间的对照关系。"
    pieces: list[str] = []
    for row in meaningful[:12]:
        cells = [clean_inline(cell) for cell in row.strip().strip("|").split("|")]
        cells = [cell for cell in cells if cell]
        if cells:
            pieces.append("，".join(cells))
    return "表格口述：" + "；".join(pieces) + "。"


def render_section(title: str, lines: list[str]) -> str:
    spoken: list[str] = [f"本集标题：{clean_inline(title)}。", "欢迎收听 DeerFlow 源码与运行机制导读。"]
    in_code = False
    code_language = ""
    code_lines: list[str] = []
    table_lines: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = clean_inline(" ".join(paragraph))
            if text:
                spoken.append(text)
            paragraph.clear()

    def flush_table() -> None:
        if table_lines:
            flush_paragraph()
            spoken.append(table_narration(table_lines))
            table_lines.clear()

    for line in lines:
        if line.startswith("```"):
            if not in_code:
                flush_paragraph()
                flush_table()
                in_code = True
                code_language = line[3:].strip()
                code_lines = []
            else:
                spoken.append(code_narration(code_lines, code_language))
                in_code = False
                code_language = ""
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("|"):
            flush_paragraph()
            table_lines.append(line)
            continue
        flush_table()
        if re.match(r"^#{2,6} ", line):
            flush_paragraph()
            heading = clean_inline(re.sub(r"^#{2,6} ", "", line))
            if heading:
                spoken.append(f"下面进入小节：{heading}。")
            continue
        if line.strip() == "---":
            flush_paragraph()
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph.append(line)

    flush_paragraph()
    flush_table()
    spoken.append("本集小结：请记住，本章的概念最终都要回到 DeerFlow 的运行链路中，观察它们如何影响状态、工具、事件和最终输出。")
    return "\n\n".join(spoken) + "\n"


def split_long_section(title: str, lines: list[str], max_chars: int = 9000) -> list[tuple[str, list[str]]]:
    if len("\n".join(lines)) <= max_chars:
        return [(title, lines)]
    starts = [i for i, line in enumerate(lines) if line.startswith("### ")]
    if not starts:
        return [(title, lines)]
    blocks: list[tuple[str, list[str]]] = []
    intro = lines[: starts[0]]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append((lines[start][4:].strip(), lines[start:end]))

    chunks: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = list(intro)
    current_size = len("\n".join(current_lines))
    part = 1
    for block_title, block_lines in blocks:
        block_size = len("\n".join(block_lines))
        if current_title and current_size + block_size > max_chars:
            chunks.append((f"{title}：第 {part} 部，{current_title}", current_lines))
            part += 1
            current_title = block_title
            current_lines = list(block_lines)
            current_size = block_size
        else:
            if not current_title:
                current_title = block_title
            current_lines.extend(block_lines)
            current_size += block_size
    if current_lines:
        chunks.append((f"{title}：第 {part} 部，{current_title}", current_lines))
    return chunks


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    sections: list[tuple[str, list[str]]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        title = lines[start][3:].strip()
        if title in {"目录", "一本面向当前企业分支的通俗源码电子书"}:
            continue
        sections.extend(split_long_section(title, lines[start + 1 : end]))
    return sections


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--book", type=Path, default=DEFAULT_BOOK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    sections = split_sections(args.book.read_text(encoding="utf-8"))
    manifest: list[str] = ["# DeerFlow 播客脚本清单", "", "本目录由 `build_deerflow_podcast.py` 从电子书生成，可人工润色后用于音频合成。", ""]
    for number, (title, lines) in enumerate(sections, start=1):
        filename = f"episode-{number:02d}.txt"
        path = args.output / filename
        path.write_text(render_section(title, lines), encoding="utf-8")
        chars = len(path.read_text(encoding="utf-8"))
        manifest.append(f"{number:02d}. [{title}]({filename}) — {chars} 字")
    (args.output / "README.md").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print(f"generated {len(sections)} episode scripts in {args.output}")


if __name__ == "__main__":
    main()
