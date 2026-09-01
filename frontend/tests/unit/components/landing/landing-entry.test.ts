import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const landingPageSource = readFileSync(
  resolve(process.cwd(), "src/app/page.tsx"),
  "utf8",
);
const headerSource = readFileSync(
  resolve(process.cwd(), "src/components/landing/header.tsx"),
  "utf8",
);
const heroSource = readFileSync(
  resolve(process.cwd(), "src/components/landing/hero.tsx"),
  "utf8",
);
const nginxSource = readFileSync(
  resolve(process.cwd(), "../k8s/local-pvc/nginx.conf"),
  "utf8",
);

describe("enterprise landing entry", () => {
  test("sends the exact root directly to the new-chat entry", () => {
    expect(landingPageSource).toContain('redirect("/workspace/chats/new")');
    expect(landingPageSource).not.toContain("getServerSideUser");
    expect(nginxSource).not.toContain('if ($cookie_access_token = "")');
    expect(nginxSource).not.toContain("location = / {");
  });

  test("sends every landing CTA through the existing server auth route", () => {
    expect(headerSource.match(/href=\{brand\.links\.login\}/g)).toHaveLength(2);
    expect(headerSource).not.toContain("href={brand.links.workspace}");
    expect(heroSource).toContain('href="/api/v1/auth/iam/login?next=/workspace/chats/new"');
    expect(heroSource).not.toContain("href={brand.links.workspace}");
  });
});
