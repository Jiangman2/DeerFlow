"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Hero({ className }: { className?: string }) {
  return (
    <section
      className={cn(
        "relative flex min-h-screen w-full flex-col items-center justify-center overflow-hidden",
        className,
      )}
    >
      <div
        className="absolute inset-0 z-0 bg-[url('/images/home-bg-light.png')] bg-cover bg-center bg-no-repeat"
        style={{
          filter: "blur(1px)",
          WebkitFilter: "blur(1px)",
          transform: "scale(1.04)",
          transformOrigin: "center",
        }}
      />
      <div className="absolute inset-0 z-10 bg-white/25" />
      <div className="absolute top-6 left-6 z-20 md:top-8 md:left-8">
        <BrandLogo variant="full" priority className="h-10 w-auto md:h-12" />
      </div>
      <div className="container-md relative z-20 mx-auto flex h-full flex-col items-center justify-center px-5 sm:px-6">
        <h1 className="max-w-5xl text-center text-4xl font-bold tracking-normal text-slate-950 md:text-6xl">
          {brand.productName}
        </h1>
        <p className="mt-6 max-w-3xl text-center text-base leading-7 text-slate-700 md:text-xl">
          面向油气勘探开发业务的多智能体工作平台，集深度研究、专业报告、数据分析与智能办公于一体。
        </p>
        <Link href={brand.links.workspace} prefetch={false}>
          <Button
            className="mt-8 h-11 px-6 shadow-[0_18px_50px_rgba(15,42,85,0.22)]"
            size="lg"
          >
            <span>开始使用</span>
            <ChevronRightIcon className="size-4" />
          </Button>
        </Link>
      </div>
    </section>
  );
}
