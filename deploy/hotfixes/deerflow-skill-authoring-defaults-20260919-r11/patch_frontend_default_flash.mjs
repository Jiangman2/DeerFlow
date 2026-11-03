#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

function replaceOnce(filePath, oldText, newText, label) {
  const text = fs.readFileSync(filePath, "utf8").replace(/\r\n/g, "\n");
  const count = text.split(oldText).length - 1;
  if (count !== 1) {
    throw new Error(`${label}: expected one source anchor, found ${count}`);
  }
  fs.writeFileSync(filePath, text.replace(oldText, newText), "utf8");
}

function patch(root) {
  replaceOnce(
    path.join(root, "src/core/settings/local.ts"),
    '    mode: undefined,\n',
    '    mode: "flash",\n',
    "default local mode",
  );
  replaceOnce(
    path.join(root, "src/core/settings/local.ts"),
    `}

export function mergeLocalSettings(
  settings?: Partial<LocalSettings>,
`,
    `}

type LocalSettingsInput = Omit<Partial<LocalSettings>, "context"> & {
  context?: Partial<LocalSettings["context"]>;
};

export function mergeLocalSettings(
  settings?: LocalSettingsInput,
`,
    "deep-partial persisted context input",
  );
  replaceOnce(
    path.join(root, "src/core/settings/local.ts"),
    "      const settings = JSON.parse(json) as Partial<LocalSettings>;\n",
    "      const settings = JSON.parse(json) as LocalSettingsInput;\n",
    "persisted settings input type",
  );

  for (const relative of [
    "src/components/workspace/mode-selector.tsx",
    "src/components/workspace/sidecar/sidecar-panel.tsx",
  ]) {
    replaceOnce(
      path.join(root, relative),
      '  return supportsThinking ? "pro" : "flash";\n',
      '  return "flash";\n',
      relative,
    );
  }

  const sharedAgents = path.join(root, "src/core/agents/shared-agents.ts");
  const oldTail = `  {
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
`;
  const newTail = `  {
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
  {
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
`;
  replaceOnce(sharedAgents, oldTail, newTail, "sixth external digital employee");

  const launch = path.join(root, "src/core/agents/external-launch.ts");
  replaceOnce(
    launch,
    `const EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
] as const;
`,
    `const CNOOCCLAW_EXTERNAL_AGENT_KEYS = [
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
`,
    "split CNOOCClaw and legacy allowlists",
  );
  replaceOnce(
    launch,
    `    const isApprovedAgent = EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
`,
    `    const isApprovedCnoocClawAgent = CNOOCCLAW_EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
    const isApprovedLegacyAgent = LEGACY_EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
`,
    "split direct and legacy approval checks",
  );
  replaceOnce(
    launch,
    `      isApprovedAgent
    ) {
      return value;
`,
    `      isApprovedCnoocClawAgent
    ) {
      return value;
`,
    "CNOOCClaw direct-link condition",
  );
  replaceOnce(
    launch,
    `      isApprovedAgent
    ) {
      return \`/api/cnooc/external-agents/\${key}/launch\`;
`,
    `      isApprovedLegacyAgent
    ) {
      return \`/api/cnooc/external-agents/\${key}/launch\`;
`,
    "legacy launch condition",
  );
}

const option = process.argv.indexOf("--frontend-root");
if (option < 0 || !process.argv[option + 1]) {
  throw new Error("usage: patch_frontend_default_flash.mjs --frontend-root PATH");
}
patch(path.resolve(process.argv[option + 1]));
console.log("frontend_default_flash_patch=PASS");
