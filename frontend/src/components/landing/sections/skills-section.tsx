"use client";

import { cn } from "@/lib/utils";

import ProgressiveSkillsAnimation from "../progressive-skills-animation";
import { Section } from "../section";

export function SkillsSection({ className }: { className?: string }) {
  return (
    <Section
      className={cn("min-h-[calc(100vh-64px)] w-full bg-white/2", className)}
      title="业务技能"
      subtitle={
        <div>
          按任务动态加载所需技能，减少无关上下文干扰。
          <br />
          支持沉淀勘探开发资料检索、数据分析、报告编制和办公自动化流程。
        </div>
      }
    >
      <div className="relative overflow-hidden">
        <ProgressiveSkillsAnimation />
      </div>
    </Section>
  );
}
