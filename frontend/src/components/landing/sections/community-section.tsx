"use client";

import Link from "next/link";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";

import { Section } from "../section";

export function CommunitySection() {
  return (
    <Section
      title={
        <AuroraText colors={["#60A5FA", "#A5FA60", "#A560FA"]}>
          平台共建
        </AuroraText>
      }
      subtitle="沉淀业务技能、完善智能体流程，让平台更贴近勘探开发一线工作。"
    >
      <div className="flex justify-center">
        <Button className="text-xl" size="lg" asChild>
          <Link href="/workspace">进入工作台</Link>
        </Button>
      </div>
    </Section>
  );
}
