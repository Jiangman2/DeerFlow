import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AgentNameIcon } from "@/components/workspace/agents/agent-name-icon";

describe("AgentNameIcon", () => {
  it("uses a separate opacity layer instead of a color-mix dependent background", () => {
    const html = renderToStaticMarkup(createElement(AgentNameIcon));

    expect(html).toContain("bg-primary");
    expect(html).toContain("opacity-10");
    expect(html).toContain("<svg");
    expect(html).not.toContain("bg-primary/10");
  });
});
