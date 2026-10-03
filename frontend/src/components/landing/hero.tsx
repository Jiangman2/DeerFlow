"use client";

import { ChevronRightIcon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { brand } from "@/brand";
import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const HERO_WORDS = ["深度研究", "专业报告", "数据分析", "智能办公"];

export function Hero({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative flex min-h-svh w-full flex-col items-center justify-center overflow-hidden bg-slate-50",
        className,
      )}
    >
      <div className="absolute inset-0 bg-[url('/images/home-bg-light.png')] bg-cover bg-center bg-no-repeat" />
      <div className="bg-linear-to-b from-white/72 via-white/34 to-white/62 absolute inset-0" />
      <div className="container-md relative z-10 mx-auto flex min-h-svh flex-col items-center justify-center px-5 pb-16 pt-24 sm:px-8">
        <h1 className="max-w-6xl break-words text-center text-4xl font-bold leading-[1.15] tracking-tight text-slate-950 sm:text-5xl md:text-6xl lg:text-7xl">
          {brand.productName}
        </h1>
        <div className="mt-5 flex w-full min-w-0 max-w-5xl flex-wrap items-center justify-center gap-x-3 gap-y-1 text-center text-xl font-semibold sm:text-2xl md:text-4xl">
          <HeroWordRotate words={HERO_WORDS} />
          <span className="whitespace-nowrap text-slate-900">
            多智能体业务工作平台
          </span>
        </div>
        <p className="mt-8 max-w-4xl text-center text-base leading-7 text-slate-700 sm:text-lg md:text-xl">
          面向油气勘探开发业务，集深度研究、专业报告、数据分析与智能办公于一体。
        </p>
        <Link href="/api/v1/auth/iam/login?next=/workspace/chats/new">
          <Button
            className="mt-9 h-12 rounded-xl border border-[#07569f] bg-[#0868bd] px-7 text-white shadow-[0_8px_20px_rgba(8,104,189,0.24)] hover:-translate-y-0.5 hover:bg-[#075aa5] hover:shadow-[0_12px_26px_rgba(8,104,189,0.3)] focus-visible:ring-[#0868bd]/35 active:translate-y-0 active:bg-[#064c8c]"
            size="lg"
          >
            <span className="text-md">开始使用</span>
            <ChevronRightIcon className="size-4" />
          </Button>
        </Link>
      </div>
    </div>
  );
}

function HeroWordRotate({
  words,
  duration = 2200,
}: {
  words: string[];
  duration?: number;
}) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setIndex((prevIndex) => (prevIndex + 1) % words.length);
    }, duration);

    return () => clearInterval(interval);
  }, [words, duration]);

  return (
    <div className="relative min-w-0 max-w-full overflow-hidden py-2">
      <AnimatePresence mode="popLayout">
        <motion.div
          key={index}
          className="max-w-full"
          initial={{ opacity: 0, y: -50, filter: "blur(16px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: 50, filter: "blur(16px)" }}
          transition={{ duration: 0.3, ease: "easeOut" }}
        >
          <AuroraText
            className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
            speed={3}
            colors={["#07569f", "#1687c9", "#35a7c9"]}
          >
            {words[index]}
          </AuroraText>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
