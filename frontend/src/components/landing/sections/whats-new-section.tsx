"use client";

import MagicBento, { type BentoCardProps } from "@/components/ui/magic-bento";
import { cn } from "@/lib/utils";

import { Section } from "../section";

const COLOR = "#0a0a0a";
const features: BentoCardProps[] = [
  {
    color: COLOR,
    label: "Context Engineering",
    title: "Long/Short-term Memory",
    description: "Better continuity for professional workflows",
  },
  {
    color: COLOR,
    label: "Long Task Running",
    title: "Planning and Sub-tasking",
    description:
      "Plans ahead, reasons through complexity, then executes sequentially or in parallel",
  },
  {
    color: COLOR,
    label: "Extensible",
    title: "Skills and Tools",
    description: "Plug in business skills and reusable agent workflows.",
  },
  {
    color: COLOR,
    label: "Persistent",
    title: "Sandbox with File System",
    description: "Read, write, and run work in an isolated workspace",
  },
  {
    color: COLOR,
    label: "Flexible",
    title: "Multi-Model Support",
    description: "Doubao, DeepSeek, OpenAI, Gemini, etc.",
  },
  {
    color: COLOR,
    label: "Deployable",
    title: "Self-hosted Platform",
    description: "MIT License, self-hosted, full control",
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
