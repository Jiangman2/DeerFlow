import { describe, expect, it } from "@rstest/core";

import {
  displayPublicPptRequest,
  hasPersistedSkillWorkflow,
  preparePublicPptRequest,
} from "@/core/skills/public-ppt-request";

describe("public PPT shortcut as ordinary Agent input", () => {
  for (const task of [
    "",
    "制作汇报",
    "快速生成",
    "美化上传的 PPT\n保持内容",
    "/other is quoted task text",
  ]) {
    it(`keeps native Skill instructions and task: ${task}`, () => {
      const text = `/ppt-master${task ? ` ${task}` : ""}`;
      const payload = {
        input: { messages: [{ role: "user", content: text }] },
        context: { model_name: "glm-5.3-flash" },
      };
      const result = preparePublicPptRequest(payload);
      const content = result.input.messages[0]!.content;
      expect(content).not.toMatch(/^\//);
      expect(content).toContain("/mnt/skills/public/ppt-master/SKILL.md");
      expect(content).toContain("原生流程与确认要求");
      expect(content.endsWith(task)).toBe(true);
      expect(displayPublicPptRequest(content.trim())).toBe(text);
      expect(result.context).toBe(payload.context);
      expect(payload.input.messages[0]!.content).toBe(text);
      expect(preparePublicPptRequest(result)).toBe(result);
    });
  }

  it("preserves files, image blocks, prior messages and replaces the activation override", () => {
    const old = { type: "human", content: "/ppt-master old" };
    const image = {
      type: "image_url",
      image_url: { url: "data:image/png;base64,AA" },
    };
    const files = [
      {
        filename: "template.pptx",
        path: "/mnt/user-data/uploads/template.pptx",
      },
    ];
    const payload = {
      input: {
        messages: [
          old,
          {
            type: "human",
            content: [{ type: "text", text: "/ppt-master 使用模板" }, image],
            additional_kwargs: {
              files,
              original_user_content: "/ppt-master 使用模板",
            },
          },
        ],
      },
    };
    const result = preparePublicPptRequest(payload);
    expect(result.input.messages[0]).toBe(old);
    const message = result.input.messages[1]!;
    if (!("additional_kwargs" in message)) throw new Error("Missing file metadata");
    expect(message.additional_kwargs.files).toBe(files);
    expect(message.additional_kwargs.original_user_content).not.toMatch(/^\//);
    expect(message.content[1]).toBe(image);
  });

  for (const text of [
    "继续",
    "/ppt-master-other task",
    "请解释 /ppt-master",
    "/other task",
  ]) {
    it(`does not convert unrelated input: ${text}`, () => {
      const payload = {
        input: { messages: [{ type: "human", content: text }] },
      };
      expect(preparePublicPptRequest(payload)).toBe(payload);
      expect(displayPublicPptRequest(text)).toBe(text);
    });
  }

  it("does not rewrite an earlier shortcut when latest human input is a continuation", () => {
    const payload = {
      input: {
        messages: [
          { type: "human", content: "/ppt-master earlier" },
          { type: "human", content: "继续" },
        ],
      },
    };
    expect(preparePublicPptRequest(payload)).toBe(payload);
  });

  it("does not convert hidden control messages or invent a state reset", () => {
    const payload = {
      input: {
        messages: [
          {
            type: "human",
            content: "/ppt-master hidden",
            additional_kwargs: { hide_from_ui: true },
          },
        ],
      },
    };
    expect(preparePublicPptRequest(payload)).toBe(payload);
    expect(hasPersistedSkillWorkflow({ values: {} })).toBe(false);
    expect(
      hasPersistedSkillWorkflow({ values: { skill_workflow: null } }),
    ).toBe(false);
    expect(
      hasPersistedSkillWorkflow({
        values: {
          skill_workflow: { status: "blocked", skill_name: "ppt-master" },
        },
      }),
    ).toBe(true);
    expect(
      hasPersistedSkillWorkflow({
        values: {
          skill_workflow: { status: "completed", skill_name: "other" },
        },
      }),
    ).toBe(true);
  });
});
