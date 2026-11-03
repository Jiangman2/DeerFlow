import { describe, expect, test } from "@rstest/core";
import postcss from "postcss";

import semanticAlphaFallback, {
  addSemanticAlphaFallback,
} from "../../../scripts/postcss-semantic-alpha-fallback.mjs";

describe("semantic alpha enterprise-browser fallback", () => {
  test("converts semantic color-mix values to legacy rgba values", () => {
    expect(
      addSemanticAlphaFallback(
        "color-mix(in oklab, var(--color-primary) 10%, transparent)",
      ),
    ).toBe(
      "rgba(var(--legacy-primary-rgb), calc(var(--legacy-primary-opacity, 1) * 0.1))",
    );
  });

  test("emits fallback before the modern declaration", async () => {
    const result = await postcss([semanticAlphaFallback()]).process(
      ".state { background-color: color-mix(in oklab, var(--color-background) 85%, transparent); }",
      { from: undefined },
    );

    const fallbackIndex = result.css.indexOf(
      "rgba(var(--legacy-background-rgb)",
    );
    const modernIndex = result.css.indexOf("color-mix(in oklab");
    expect(fallbackIndex).toBeGreaterThan(-1);
    expect(modernIndex).toBeGreaterThan(fallbackIndex);
  });

  test("replaces Tailwind's opaque slash-alpha fallback", async () => {
    const result = await postcss([semanticAlphaFallback()]).process(
      String.raw`.bg-primary\/10 { background-color: var(--primary); }
      @supports (color: color-mix(in lab, red, red)) {
        .bg-primary\/10 { background-color: color-mix(in oklab, var(--primary) 10%, transparent); }
      }`,
      { from: undefined },
    );

    expect(result.css).toContain(
      "background-color: rgba(var(--legacy-primary-rgb), calc(var(--legacy-primary-opacity, 1) * 0.1))",
    );
    expect(result.css).toContain(
      "background-color: color-mix(in oklab, var(--primary) 10%, transparent)",
    );
    expect(result.css).not.toContain("background-color: var(--primary)");
  });

  test("keeps an opaque utility intact when Tailwind groups it with alpha zero", async () => {
    const result = await postcss([semanticAlphaFallback()]).process(
      String.raw`.bg-background, .bg-background\/0 { background-color: var(--background); }`,
      { from: undefined },
    );

    expect(result.css).toContain(
      ".bg-background, .bg-background\\/0 { background-color: var(--background); }",
    );
    expect(result.css).toContain(
      ".bg-background\\/0 { background-color: rgba(var(--legacy-background-rgb), calc(var(--legacy-background-opacity, 1) * 0)); }",
    );
  });

  test("does not rewrite static Tailwind palette colors", () => {
    expect(
      addSemanticAlphaFallback(
        "color-mix(in oklab, var(--color-blue-500) 10%, transparent)",
      ),
    ).toBeNull();
  });
});
