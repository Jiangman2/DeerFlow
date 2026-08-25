"use client";

import { useSearchParams } from "next/navigation";

import { cn } from "@/lib/utils";

import { CnoocAssistantAvatar } from "./cnooc-assistant-avatar";

export function Welcome({
  className,
}: {
  className?: string;
  mode?: "ultra" | "pro" | "thinking" | "flash";
}) {
  const searchParams = useSearchParams();
  const isSkillMode = searchParams.get("mode") === "skill";

  if (isSkillMode) {
    return (
      <section
        className={cn("cnooc-welcome-skill", className)}
        data-testid="welcome-panel"
      >
        <h1>创建您的专属技能</h1>
        <p>描述您希望智能体完成的工作，我们会协助您搭建可复用的技能。</p>
      </section>
    );
  }

  return (
    <section
      className={cn("cnooc-welcome-panel", className)}
      data-testid="welcome-panel"
    >
      <div className="cnooc-desktop-welcome-copy">
        <h1>中海油勘探开发智能体</h1>
        <p>您好！我是您的智能助手，有什么可以帮助您？</p>
      </div>

      <div className="cnooc-mobile-welcome-card">
        <CnoocAssistantAvatar className="cnooc-welcome-mascot" />
        <div className="cnooc-mobile-welcome-copy">
          <p className="cnooc-mobile-welcome-eyebrow">hi,您好呀~</p>
          <h1>
            你好，我是您的
            <strong>海油智能助手</strong>
          </h1>
          <p className="cnooc-mobile-welcome-description">
            很高兴遇到你，我将时刻为您答疑解惑，助力高效工作。
          </p>
        </div>
        <span aria-hidden="true" className="cnooc-mobile-welcome-wave" />
      </div>
    </section>
  );
}
