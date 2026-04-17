/**
 * About markdown content. Inlined to avoid raw-loader dependency
 * (Turbopack cannot resolve raw-loader for .md imports).
 */
export const aboutMarkdown = `# 关于中海油智能体应用平台

> **中海油智能体应用平台**

本平台面向油气业务的智能研究与协同作业场景，提供统一工作台、对话线程管理、文档与知识接入、工具编排与产物沉淀能力，支撑业务分析、方案编制与知识复用等任务。

---

## 🚀 核心能力

* **多模型接入**：支持公网与内网模型，按任务选择最合适的推理与生成能力。
* **工具与技能扩展**：支持 MinIO 文档、检索、脚本工具与业务技能的持续迭代。
* **对话与产物沉淀**：线程化管理任务与产物，便于追溯、复用与共享。
* **沙箱与安全执行**：支持受控执行与文件操作，降低误操作风险。
* **长对话能力**：通过摘要与上下文策略，提升长任务稳定性。

---

## 🌟 开源底座

本平台基于开源底座进行本地化适配与二次开发，在企业环境中落地应用。

开源底座仓库： [github.com/bytedance/deer-flow](https://github.com/bytedance/deer-flow)

---

## 📜 License

开源底座遵循 **MIT License**。

---

## 🙌 Acknowledgments

We extend our heartfelt gratitude to the open source projects and contributors who have made this platform possible.

### Core Frameworks
- **[LangChain](https://github.com/langchain-ai/langchain)**: A phenomenal framework that powers our LLM interactions and chains.
- **[LangGraph](https://github.com/langchain-ai/langgraph)**: Enabling sophisticated multi-agent orchestration.
- **[Next.js](https://nextjs.org/)**: A cutting-edge framework for building web applications.

### UI Libraries
- **[Shadcn](https://ui.shadcn.com/)**: Minimalistic components that power our UI.
- **[SToneX](https://github.com/stonexer)**: For his invaluable contribution to token-by-token visual effects.

These outstanding projects form the backbone of this platform and exemplify the transformative power of open source collaboration.

### Special Thanks
- **[Daniel Walnut](https://github.com/hetaoBackend/)**
- **[Henry Li](https://github.com/magiccube/)**
`;
