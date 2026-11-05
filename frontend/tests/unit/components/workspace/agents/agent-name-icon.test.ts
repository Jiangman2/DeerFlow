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

  it("accepts welcome-page size overrides without changing the compatibility layers", () => {
    const html = renderToStaticMarkup(
      createElement(AgentNameIcon, {
        className: "h-12 w-12",
        iconClassName: "h-6 w-6",
      }),
    );

    expect(html).toContain("h-12 w-12");
    expect(html).toContain("h-6 w-6");
    expect(html).toContain("bg-primary");
    expect(html).toContain("opacity-10");
    expect(html).not.toContain("bg-primary/10");
  });
});
