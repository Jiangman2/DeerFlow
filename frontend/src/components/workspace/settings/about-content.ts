/**
 * About DeerFlow markdown content. Inlined to avoid raw-loader dependency
 * (Turbopack cannot resolve raw-loader for .md imports).
 */
import { APP_VERSION } from "@/version";

export const aboutMarkdown = `# 关于中海油勘探开发通用智能体 ${APP_VERSION}

> **中海油智能体应用平台**

本平台面向油气业务的智能研究与协同作业场景，提供统一工作台、对话线程管理、知识接入、工具编排与产物沉淀能力，支撑业务分析、方案编制与知识复用等任务。

---

## 核心能力

* **多模型接入**：按任务选择合适的推理与生成能力。
* **知识与资料接入**：支持文档、知识库、表格与业务资料的统一使用。
* **专业技能扩展**：通过内置和自定义 Skills 持续扩展业务能力。
* **安全执行与产物沉淀**：在受控沙箱中执行任务并保留可追溯结果。

---

## 开源基础

![Star History Chart](https://api.star-history.com/svg?repos=bytedance/deer-flow&type=Date)

本平台构建于开源 DeerFlow 能力之上：[github.com/bytedance/deer-flow](https://github.com/bytedance/deer-flow)

## 许可证

DeerFlow 以 MIT License 开源发布；本企业版本在遵循该许可证的基础上进行集成与定制。
`;
