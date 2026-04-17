"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Hero({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "relative flex h-screen w-full flex-col items-center justify-center overflow-hidden",
        className,
      )}
    >
      <div className="absolute inset-0 z-0 bg-[url('/images/home-bg-light.png')] bg-cover bg-center bg-no-repeat" />
      <div className="absolute inset-0 z-10 bg-white/22 backdrop-blur-xs" />
      <div className="absolute top-6 left-6 z-20 md:top-8 md:left-8">
        <BrandLogo variant="full" priority className="h-10 w-auto md:h-12" />
      </div>
      <div className="container-md relative z-10 mx-auto flex h-full flex-col items-center justify-center px-6">
        <h1 className="text-center text-5xl font-bold tracking-[0.06em] text-slate-900 md:text-7xl">
          {brand.productName}
        </h1>
        <p className="mt-6 max-w-2xl text-center text-lg leading-7 text-slate-700 md:text-xl">
          面向油气业务的多智能体工作平台，集深度研究、报告生成、数据分析与智能办公于一体，让专业知识触手可及。
        </p>
        <Link href={`${brand.links.login}?next=/workspace`} prefetch={false}>
          <Button
            className="size-lg mt-8 scale-108 shadow-[0_18px_50px_rgba(15,42,85,0.22)]"
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
