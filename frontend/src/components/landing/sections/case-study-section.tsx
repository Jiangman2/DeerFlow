import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import { Section } from "../section";

export function CaseStudySection({ className }: { className?: string }) {
  const caseStudies = [
    {
      title: "邻井与相似井对比研究",
      description:
        "整合井史、生产动态和地质资料，形成可追溯的对比分析与研究报告。",
    },
    {
      title: "勘探开发方案辅助编制",
      description:
        "基于资料检索、表格处理和结构化写作能力，辅助完成方案草稿与汇报材料。",
    },
    {
      title: "科研报告与审查材料生成",
      description:
        "围绕指定主题梳理证据、组织章节、生成报告正文和图表说明。",
    },
    {
      title: "业务数据分析与可视化",
      description:
        "读取表格和文档数据，完成质量检查、趋势分析、图表生成与结论提炼。",
    },
    {
      title: "知识复用与技能沉淀",
      description:
        "把常用流程沉淀为 Agent Skill，减少重复操作，提高团队协同效率。",
    },
    {
      title: "长任务执行与产物管理",
      description:
        "支持多步骤任务规划、工具调用、文件生成和工作区产物留存。",
    },
  ];

  return (
    <Section
      className={className}
      title="业务场景"
      subtitle="围绕勘探开发一线工作，支撑研究、分析、写作和产物沉淀"
    >
      <div className="container-md mt-8 grid grid-cols-1 gap-4 px-4 md:grid-cols-2 md:px-10 lg:grid-cols-3">
        {caseStudies.map((caseStudy, index) => (
          <Card
            key={caseStudy.title}
            className={cn(
              "min-h-48 overflow-hidden border-slate-200 bg-white/90 p-6 shadow-sm",
              "transition-colors hover:border-[#0067b1]/40 hover:bg-white",
            )}
          >
            <div className="mb-5 flex h-10 w-10 items-center justify-center rounded-md bg-[#0067b1] text-sm font-semibold text-white">
              {String(index + 1).padStart(2, "0")}
            </div>
            <h3 className="text-lg font-semibold text-slate-950">
              {caseStudy.title}
            </h3>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              {caseStudy.description}
            </p>
          </Card>
        ))}
      </div>
    </Section>
  );
}
