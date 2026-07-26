import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SlashSkillChip } from "@/components/workspace/slash-skill-chip";

describe("SlashSkillChip", () => {
  it("uses opaque contrasting colors that work without color-mix support", () => {
    const html = renderToStaticMarkup(
      createElement(SlashSkillChip, {
        name: "academic-research",
        onRemove: () => undefined,
      }),
    );

    expect(html).toContain("bg-secondary");
    expect(html).toContain("text-secondary-foreground");
    expect(html).toContain("hover:bg-accent");
    expect(html).toContain("select-none");
    expect(html).not.toContain("bg-primary/");
    expect(html).not.toContain("text-primary");
    expect(html).toContain("/academic-research");
  });
});
