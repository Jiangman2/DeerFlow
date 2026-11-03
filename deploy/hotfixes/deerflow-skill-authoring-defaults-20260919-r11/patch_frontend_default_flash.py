#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def write_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor, found {count}")
    write_lf(path, text.replace(old, new, 1))


def patch(root: Path) -> None:
    replace_once(
        root / "src/core/settings/local.ts",
        '    mode: undefined,\n',
        '    mode: "flash",\n',
        "default local mode",
    )
    replace_once(
        root / "src/core/settings/local.ts",
        '''}

export function mergeLocalSettings(
  settings?: Partial<LocalSettings>,
''',
        '''}

type LocalSettingsInput = Omit<Partial<LocalSettings>, "context"> & {
  context?: Partial<LocalSettings["context"]>;
};

export function mergeLocalSettings(
  settings?: LocalSettingsInput,
''',
        "deep-partial persisted context input",
    )
    replace_once(
        root / "src/core/settings/local.ts",
        "      const settings = JSON.parse(json) as Partial<LocalSettings>;\n",
        "      const settings = JSON.parse(json) as LocalSettingsInput;\n",
        "persisted settings input type",
    )
    for relative in (
        "src/components/workspace/mode-selector.tsx",
        "src/components/workspace/sidecar/sidecar-panel.tsx",
    ):
        replace_once(
            root / relative,
            '  return supportsThinking ? "pro" : "flash";\n',
            '  return "flash";\n',
            relative,
        )

    shared_agents = root / "src/core/agents/shared-agents.ts"
    old_tail = '''  {
    name: "前期研究报告编写专家",
    description: "科研报告智能生产专家，支持科研内容组织、撰写与输出优化。",
    model: null,
    tool_groups: ["报告生成", "PPT to Word"],
    skills: null,
    category_id: "science-technology-innovation",
    tags: ["科研报告", "内容生成", "PPT"],
    avatar_id: "knowledge-service",
    source: "team",
    external_url: "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/",
  },
];
'''
    new_tail = old_tail[:-3] + '''  {
    name: "邮件智能整理助手",
    description:
      "邮件智能整理助手，支持会议与待办一键写入海能work日历和待办，并进行智能提醒。",
    model: null,
    tool_groups: ["邮件整理", "日历与待办"],
    skills: null,
    category_id: "comprehensive-management",
    tags: ["邮件", "会议", "待办", "智能提醒"],
    avatar_id: "knowledge-service",
    source: "team",
    external_url: "https://cnoocclaw.cnooc.com.cn/yjzs/",
  },
];
'''
    replace_once(shared_agents, old_tail, new_tail, "sixth external digital employee")

    launch = root / "src/core/agents/external-launch.ts"
    replace_once(
        launch,
        '''const EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
] as const;
''',
        '''const CNOOCCLAW_EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
  "yjzs",
] as const;

const LEGACY_EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
] as const;
''',
        "split CNOOCClaw and legacy allowlists",
    )
    replace_once(
        launch,
        '''    const isApprovedAgent = EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
''',
        '''    const isApprovedCnoocClawAgent = CNOOCCLAW_EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
    const isApprovedLegacyAgent = LEGACY_EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
''',
        "split direct and legacy approval checks",
    )
    replace_once(
        launch,
        "      isApprovedAgent\n    ) {\n      return value;\n",
        "      isApprovedCnoocClawAgent\n    ) {\n      return value;\n",
        "CNOOCClaw direct-link condition",
    )
    replace_once(
        launch,
        "      isApprovedAgent\n    ) {\n      return `/api/cnooc/external-agents/${key}/launch`;\n",
        "      isApprovedLegacyAgent\n    ) {\n      return `/api/cnooc/external-agents/${key}/launch`;\n",
        "legacy launch condition",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-root", type=Path, required=True)
    args = parser.parse_args()
    patch(args.frontend_root.resolve())
    print("frontend_default_flash_patch=PASS")


if __name__ == "__main__":
    main()
