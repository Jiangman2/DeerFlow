"use client";

import { LightbulbIcon } from "lucide-react";
import Link from "next/link";

import { AuroraText } from "@/components/ui/aurora-text";
import { Button } from "@/components/ui/button";

import { Section } from "../section";

export function CommunitySection() {
  return (
    <Section
      title={
        <AuroraText colors={["#60A5FA", "#A5FA60", "#A560FA"]}>
          Join the Community
        </AuroraText>
      }
      subtitle="Share ideas, workflows, and reusable practices with your team."
    >
      <div className="flex justify-center">
        <Button className="text-xl" size="lg" asChild>
          <Link
            href="#"
            target="_blank"
            rel="noopener noreferrer"
          >
            <LightbulbIcon />
            Contribute Now
          </Link>
        </Button>
      </div>
    </Section>
  );
}
