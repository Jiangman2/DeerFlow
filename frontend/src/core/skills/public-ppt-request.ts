// A UI shortcut, deliberately sent as ordinary user text. The public Skill
// owns its workflow; a leading slash would activate Gateway's extra guards.
const PREFIX =
  "请使用已安装且启用的 public ppt-master Skill 完成以下请求。先读取 /mnt/skills/public/ppt-master/SKILL.md，并按该 Skill 的原生流程与确认要求执行。若 Skill 不可用，请说明原因；若需求不完整，请先询问。\n\n用户需求：\n";

function record(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function ordinaryText(text: string): string | undefined {
  const match = /^\/ppt-master(?:\s+|$)/.exec(text);
  return match ? PREFIX + text.slice(match[0].length).trim() : undefined;
}

/** Convert only the latest visible human request, never historical messages. */
export function preparePublicPptRequest<T>(payload: T): T {
  const input = record(record(payload)?.input);
  if (!input || !Array.isArray(input.messages)) return payload;
  const messages = input.messages as unknown[];
  let index = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = record(messages[i]);
    if (
      (message?.type === "human" || message?.role === "user") &&
      !message.name &&
      record(message.additional_kwargs)?.hide_from_ui !== true
    ) {
      index = i;
      break;
    }
  }
  const message = record(messages[index]);
  if (!message) return payload;
  let content: unknown = message.content;
  let converted: string | undefined;
  if (typeof content === "string") {
    converted = ordinaryText(content);
    content = converted ?? content;
  } else if (Array.isArray(content)) {
    const firstText = content.findIndex((part) => {
      const block = record(part);
      return block?.type === "text" && typeof block.text === "string";
    });
    const block = record(content[firstText]);
    if (block && typeof block.text === "string") {
      converted = ordinaryText(block.text);
      if (converted !== undefined) {
        content = content.map((part, i) =>
          i === firstText ? { ...block, text: converted } : part,
        );
      }
    }
  }
  if (converted === undefined) return payload;
  const kwargs = record(message.additional_kwargs);
  const updated = {
    ...message,
    content,
    ...(kwargs
      ? {
          additional_kwargs: {
            ...kwargs,
            // Gateway activation reads this value before content when present.
            ...(typeof kwargs.original_user_content === "string"
              ? { original_user_content: converted }
              : {}),
          },
        }
      : {}),
  };
  return {
    ...payload,
    input: {
      ...input,
      messages: messages.map((value, i) => (i === index ? updated : value)),
    },
  };
}

/** Restore only our exact transport prefix in human-message presentation. */
export function displayPublicPptRequest(text: string): string {
  if (!text.startsWith(PREFIX.trimEnd())) return text;
  if (text === PREFIX.trimEnd()) return "/ppt-master";
  if (!text.startsWith(PREFIX)) return text;
  const task = text.slice(PREFIX.length);
  return task ? `/ppt-master ${task}` : "/ppt-master";
}

export function hasPersistedSkillWorkflow(state: unknown): boolean {
  const workflow = record(record(record(state)?.values)?.skill_workflow);
  return Boolean(workflow && Object.keys(workflow).length);
}
