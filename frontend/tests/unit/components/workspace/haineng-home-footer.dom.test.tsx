import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { HainengHomeFooter } from "@/components/workspace/haineng-home-footer";
import { zhCN } from "@/core/i18n/locales/zh-CN";

rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ t: zhCN }) }));

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
});

test("shows four independent counters and the updated contact/group details", async () => {
  const fetcher = rs.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.includes("model-call-count")
            ? { todayModelCallCount: 12, totalModelCallCount: 1234 }
            : { todayUsers: 2, todayTasks: 4 },
        ),
      ),
  );
  rs.stubGlobal("fetch", fetcher);
  const { container } = render(<HainengHomeFooter />);
  await waitFor(() =>
    expect(container.querySelectorAll(".cnooc-home-usage strong")).toHaveLength(
      4,
    ),
  );
  await waitFor(() =>
    expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
      "今日任务数：4",
    ),
  );
  expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
    "今日用户：2",
  );
  expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
    "总使用量：1,234",
  );
  expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
    "今日使用量：12",
  );
  expect(screen.getByText("sh_tyznt@cnooc.com.cn").getAttribute("href")).toBe(
    "mailto:sh_tyznt@cnooc.com.cn",
  );
  expect(screen.getByText("sh_ai@cnooc.com.cn").getAttribute("href")).toBe(
    "mailto:sh_ai@cnooc.com.cn",
  );
  expect(screen.getByText("通用智能体：")).toBeTruthy();
  expect(screen.getByText("人工智能平台：")).toBeTruthy();
  expect(screen.getByText("010-84521314").getAttribute("href")).toBe(
    "tel:01084521314",
  );
  expect(screen.getByText("13520340738").getAttribute("href")).toBe(
    "tel:13520340738",
  );
  expect(screen.getByText("意见反馈").getAttribute("href")).toContain(
    "mailto:sh_tyznt@cnooc.com.cn?subject=",
  );
  for (const label of [
    "客服邮箱",
    "业务负责人",
    "运维负责人",
    "shiyl19@cnooc.com.cn",
  ]) {
    expect(container.textContent).not.toContain(label);
  }
  expect(screen.getByText("海能通用智能体-问题反馈交流群")).toBeTruthy();
  expect(
    screen.getByAltText("海能Work服务支持群二维码").getAttribute("src"),
  ).toBe("/images/haineng-work-support-qr-20261012.jpg");
});

test("a statistics error does not hide successful Haineng usage, and zero is not unavailable", async () => {
  rs.stubGlobal(
    "fetch",
    rs.fn(async (url: string) =>
      url.includes("model-call-count")
        ? new Response(
            JSON.stringify({ todayModelCallCount: 0, totalModelCallCount: 0 }),
          )
        : new Response("Unavailable", { status: 503 }),
    ),
  );
  const { container } = render(<HainengHomeFooter />);
  await waitFor(() =>
    expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
      "总使用量：0",
    ),
  );
  expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
    "今日用户：—",
  );
  expect(container.querySelector(".cnooc-home-usage")?.textContent).toContain(
    "今日任务数：—",
  );
});
