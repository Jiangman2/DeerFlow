"use client";

import {
  AnimatedSpan,
  Terminal,
  TypingAnimation,
} from "@/components/ui/terminal";

import { Section } from "../section";

export function SandboxSection({ className }: { className?: string }) {
  return (
    <Section
      className={className}
      title="安全运行环境"
      subtitle={
        <p>
          中海油勘探开发通用智能体可在受控环境中执行命令、管理文件并完成长任务。
        </p>
      }
    >
      <div className="mt-8 flex w-full max-w-6xl flex-col items-center gap-12 px-4 lg:flex-row lg:gap-16">
        <div className="w-full flex-1">
          <Terminal className="h-[360px] w-full">
            <TypingAnimation>$ cat production-analysis.md</TypingAnimation>
            <AnimatedSpan delay={800} className="text-zinc-400">
              输入资料已读取
            </AnimatedSpan>

            <TypingAnimation delay={1200}>
              $ python analyze_well_data.py
            </TypingAnimation>
            <AnimatedSpan delay={2000} className="text-green-500">
              生产动态分析完成
            </AnimatedSpan>

            <TypingAnimation delay={2400}>
              $ write report.md --sections 6
            </TypingAnimation>
            <AnimatedSpan delay={3200} className="text-blue-500">
              报告正文已生成
            </AnimatedSpan>

            <TypingAnimation delay={3600}>$ validate report.md</TypingAnimation>
            <AnimatedSpan delay={4200} className="text-green-500">
              数据来源已校验
            </AnimatedSpan>
            <AnimatedSpan delay={4500} className="text-green-500">
              图表已生成
            </AnimatedSpan>
            <AnimatedSpan delay={4800} className="text-green-500">
              汇报材料已就绪
            </AnimatedSpan>

            <TypingAnimation delay={5400}>
              $ export ./outputs/final-report.docx
            </TypingAnimation>
            <AnimatedSpan delay={6200} className="text-zinc-400">
              已导出工作产物
            </AnimatedSpan>
          </Terminal>
        </div>

        <div className="w-full flex-1 space-y-6">
          <div className="space-y-4">
            <p className="text-sm font-medium tracking-wider text-blue-400 uppercase">
              Runtime
            </p>
            <h2 className="text-4xl font-bold tracking-tight lg:text-5xl">
              受控沙箱
            </h2>
          </div>

          <div className="space-y-4 text-lg text-zinc-400">
            <p>
              运行环境整合浏览器、Shell、文件系统和工具调用能力，在隔离工作区内处理资料、表格、脚本和产物。
            </p>
          </div>

          <div className="flex flex-wrap gap-3 pt-4">
            {["隔离执行", "权限可控", "过程留痕", "文件挂载", "长任务"].map(
              (label) => (
                <span
                  key={label}
                  className="rounded-full border border-zinc-800 bg-zinc-900 px-4 py-2 text-sm text-zinc-300"
                >
                  {label}
                </span>
              ),
            )}
          </div>
        </div>
      </div>
    </Section>
  );
}
