"use client";

import MagicBento, { type BentoCardProps } from "@/components/ui/magic-bento";
import { cn } from "@/lib/utils";

import { Section } from "../section";

const COLOR = "#0a0a0a";
const features: BentoCardProps[] = [
  {
    color: COLOR,
    label: "上下文工程",
    title: "长期与短期记忆",
    description: "围绕业务人员、项目背景和任务过程持续沉淀上下文",
  },
  {
    color: COLOR,
    label: "长任务执行",
    title: "规划与子任务协同",
    description: "面向复杂研究、资料分析和报告编制任务进行分解、执行与汇总",
  },
  {
    color: COLOR,
    label: "可扩展",
    title: "业务技能与工具",
    description: "接入 MinIO 文档、检索、脚本工具和可复用业务流程",
  },
  {
    color: COLOR,
    label: "可沉淀",
    title: "安全工作区",
    description: "在受控工作区内读写文件、生成产物并保留任务过程",
  },
  {
    color: COLOR,
    label: "模型接入",
    title: "百炼 Qwen 模型",
    description: "统一使用百炼 Qwen 能力支撑对话、分析与生成任务",
  },
  {
    color: COLOR,
    label: "私有部署",
    title: "企业级工作台",
    description: "保留官方 gateway auth，适配内部部署与受控使用场景",
  },
];

export function WhatsNewSection({ className }: { className?: string }) {
  return (
    <Section
      className={cn("", className)}
      title="平台能力升级"
      subtitle="基于官方 2.0 主分支，面向油气业务沉淀工作流、技能与智能体"
    >
      <div className="flex w-full items-center justify-center">
        <MagicBento data={features} />
      </div>
    </Section>
  );
}
