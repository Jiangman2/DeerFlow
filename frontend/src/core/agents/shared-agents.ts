import type { Agent } from "./types";

export const SHARED_EXTERNAL_AGENT_NAME_ALIASES = [
  "知识嵌入提升地震分辨率工具",
  "科研报告自动生产智能体",
  "科研报告自动生产智能体（PPT to Word）",
  "勘探开发图文智能检索工具",
  "源头采集自动化工具",
  "油气专业文档智能审查工具",
];

export const SHARED_EXTERNAL_AGENTS: Agent[] = [
  {
    name: "专业文档解析专家",
    description:
      "图文智能检索专家，面向勘探开发场景提供知识检索与图件检索能力。",
    model: null,
    tool_groups: ["图文检索", "以图查图"],
    skills: null,
    source: "team",
    external_url: "https://datalake.cnooc/zywdjxzj",
  },
  {
    name: "地震数据提频专家",
    description: "地震数据提频专家，利用 U-net 网络模型给 SEGY 数据提频。",
    model: null,
    tool_groups: ["地震资料", "SEGY"],
    skills: null,
    source: "team",
    external_url: "https://datalake.cnooc/dzsjtpzj",
  },
  {
    name: "专业报告校审专家",
    description: "文档智能审查专家，面向油气业务文档进行内容审核与一致性检查。",
    model: null,
    tool_groups: ["文档审查", "油气专业"],
    skills: null,
    source: "team",
    external_url: "https://datalake.cnooc/zjbgjszj/",
  },
  {
    name: "自动数采专家",
    description: "源头数据采集专家，提供源头信息采集、整理与结构化处理能力。",
    model: null,
    tool_groups: ["表格抽取", "数据采集"],
    skills: null,
    source: "team",
    external_url: "https://datalake.cnooc/zdsczj",
  },
  {
    name: "前期研究报告编写专家",
    description: "科研报告智能生产专家，支持科研内容组织、撰写与输出优化。",
    model: null,
    tool_groups: ["报告生成", "PPT to Word"],
    skills: null,
    source: "team",
    external_url: "https://datalake.cnooc/qqyjbgbxzj",
  },
];
