#!/usr/bin/env python3
"""Initialize daily state files for the tech-intel-daily skill."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


KEYWORDS = [
    "科技管理",
    "科技政策",
    "科技创新",
    "科技成果转化",
    "高水平科技自立自强",
    "新质生产力",
    "未来产业",
    "人工智能",
    "数据要素",
    "数据资产",
    "智能体",
    "数智化转型",
    "能源安全",
    "油气勘探",
    "油气开发",
    "增储上产",
    "海洋经济",
    "深远海开发",
    "深海油气",
    "海上风电",
    "海上CCUS",
    "绿色低碳",
    "标准化",
    "知识产权",
    "科技人才",
    "科研管理",
    "产学研融合",
    "数据湖",
    "大模型",
]

BUSINESS_TAGS = [
    {"category": "生产项目", "direction": "勘探", "tags": ["油气勘探", "储量评价", "地震资料"]},
    {"category": "生产项目", "direction": "开发", "tags": ["油气田开发", "提高采收率", "生产动态"]},
    {"category": "生产项目", "direction": "工程", "tags": ["海洋工程", "海上平台", "深水装备"]},
    {"category": "生产项目", "direction": "钻完井", "tags": ["智能钻井", "高压井", "深水钻完井"]},
    {"category": "生产项目", "direction": "新能源", "tags": ["海上风电", "氢能", "储能", "CCUS"]},
    {"category": "生产项目", "direction": "数智化", "tags": ["智能油田", "数字孪生", "数据湖"]},
    {"category": "科技项目", "direction": "勘探", "tags": ["基础地质研究", "地震解释", "储层预测"]},
    {"category": "科技项目", "direction": "开发", "tags": ["油藏工程", "水驱开发", "注碳增油"]},
    {"category": "科技项目", "direction": "数智化", "tags": ["AI大模型", "智能体", "知识图谱", "科学计算"]},
    {"category": "科技成果转化", "direction": "-", "tags": ["成果转化", "知识产权", "专利", "科技奖励"]},
    {"category": "科技资产", "direction": "-", "tags": ["数据资产", "软件资产", "科研设备", "标准规范"]},
    {"category": "科技交流与合作", "direction": "-", "tags": ["国家重点实验室", "学会协会", "产学研合作"]},
]

FOCUS_STANDARDS = [
    "国家领导原话",
    "国家政策",
    "政策解读",
    "国家部署工作或任务",
    "理论学习",
    "政策导向",
    "潜在资源",
    "潜在机遇",
    "潜在风险",
    "优秀做法/经验",
]


def write_json_if_missing(path: Path, payload: object) -> None:
    if path.exists():
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize tech intelligence daily state files.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Daily folder date, YYYY-MM-DD.")
    parser.add_argument("--output-root", default="outputs/tech-intel-daily", help="Root output folder.")
    args = parser.parse_args()

    day_dir = Path(args.output_root) / args.date
    day_dir.mkdir(parents=True, exist_ok=True)

    write_json_if_missing(day_dir / "keyword_library.json", [{"term": term, "source": "seed"} for term in KEYWORDS])
    write_json_if_missing(day_dir / "business_tag_library.json", BUSINESS_TAGS)
    write_json_if_missing(day_dir / "focus_standard_library.json", [{"term": term, "source": "seed"} for term in FOCUS_STANDARDS])

    for name in ["knowledge_base.jsonl", "taxonomy_updates.jsonl"]:
        path = day_dir / name
        if not path.exists():
            path.write_text("", encoding="utf-8")

    report = day_dir / "daily_report.md"
    if not report.exists():
        report.write_text(f"# 科技管理情报跟踪日报（{args.date}）\n\n", encoding="utf-8")

    html_report = day_dir / "daily_report.html"
    if not html_report.exists():
        html_report.write_text(
            "<!doctype html>\n"
            "<html lang=\"zh-CN\">\n"
            "<head><meta charset=\"utf-8\">"
            f"<title>科技管理情报跟踪日报（{args.date}）</title></head>\n"
            f"<body><h1>科技管理情报跟踪日报（{args.date}）</h1>"
            "<p>日报状态已初始化，等待写入情报记录并渲染。</p></body>\n"
            "</html>\n",
            encoding="utf-8",
        )

    print(day_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
