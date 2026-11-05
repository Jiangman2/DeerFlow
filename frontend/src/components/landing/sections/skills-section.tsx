"use client";

import { cn } from "@/lib/utils";

import ProgressiveSkillsAnimation from "../progressive-skills-animation";
import { Section } from "../section";

export function SkillsSection({ className }: { className?: string }) {
  return (
    <Section
      className={cn("h-[calc(100vh-64px)] w-full bg-white/2", className)}
      title="Agent Skills"
      subtitle={
        <div>
          Agent Skills are loaded progressively — only what&apos;s needed, when
          it&apos;s needed.
          <br />
          使用自定义技能文件扩展本平台，也可以直接使用内置技能库。
        </div>
      }
    >
      <div className="relative overflow-hidden">
        <ProgressiveSkillsAnimation />
      </div>
    </Section>
  );
}
