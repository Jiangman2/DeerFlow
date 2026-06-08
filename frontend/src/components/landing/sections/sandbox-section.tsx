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
      title="Agent Runtime Environment"
      subtitle={
        <p>
          CNOOC Exploration and Development General Agent can execute commands,
          manage files, and run long tasks in a secure Docker-based sandbox.
        </p>
      }
    >
      <div className="mt-8 flex w-full max-w-6xl flex-col items-center gap-12 lg:flex-row lg:gap-16">
        <div className="w-full flex-1">
          <Terminal className="h-[360px] w-full">
            <TypingAnimation>$ cat requirements.txt</TypingAnimation>
            <AnimatedSpan delay={800} className="text-zinc-400">
              pygame==2.5.0
            </AnimatedSpan>

            <TypingAnimation delay={1200}>
              $ pip install -r requirements.txt
            </TypingAnimation>
            <AnimatedSpan delay={2000} className="text-green-500">
              Installed pygame
            </AnimatedSpan>

            <TypingAnimation delay={2400}>
              $ write report.py --lines 156
            </TypingAnimation>
            <AnimatedSpan delay={3200} className="text-blue-500">
              Written 156 lines
            </AnimatedSpan>

            <TypingAnimation delay={3600}>
              $ python report.py --test
            </TypingAnimation>
            <AnimatedSpan delay={4200} className="text-green-500">
              Data loaded
            </AnimatedSpan>
            <AnimatedSpan delay={4500} className="text-green-500">
              Charts rendered
            </AnimatedSpan>
            <AnimatedSpan delay={4800} className="text-green-500">
              Report ready
            </AnimatedSpan>

            <TypingAnimation delay={5400}>
              $ curl -O production-2024.csv
            </TypingAnimation>
            <AnimatedSpan delay={6200} className="text-zinc-400">
              Downloaded 12.4 MB
            </AnimatedSpan>
          </Terminal>
        </div>

        <div className="w-full flex-1 space-y-6">
          <div className="space-y-4">
            <p className="text-sm font-medium tracking-wider text-blue-400 uppercase">
              Runtime
            </p>
            <h2 className="text-4xl font-bold tracking-tight lg:text-5xl">
              AIO Sandbox
            </h2>
          </div>

          <div className="space-y-4 text-lg text-zinc-400">
            <p>
              The runtime combines browser, shell, file, MCP and VSCode Server
              capabilities in a single Docker container.
            </p>
          </div>

          <div className="flex flex-wrap gap-3 pt-4">
            {["Isolated", "Safe", "Persistent", "Mountable FS", "Long-running"].map(
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
