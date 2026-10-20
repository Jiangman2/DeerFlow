import { test, expect, type WebSocketRoute } from "@playwright/test";
import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

// The synthetic WebSocket peer verifies client controls, not production SSO
// or multi-Pod routing. Real cookie isolation is tested in the backend suite.
for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`manual login controls ${viewport.width}`, async ({
    page,
    context,
    browser,
    baseURL,
  }) => {
    await page.setViewportSize(viewport);
    await context.addCookies([
      { name: "locale", value: "zh-CN", url: baseURL! },
    ]);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const warnings: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning")
        warnings.push(m.text());
    });
    mockLangGraphAPI(page, {
      threads: [{ thread_id: MOCK_THREAD_ID, title: "内网人工登录验证" }],
    });
    const frameContext = await browser.newContext({
      viewport: { width: 1000, height: 650 },
    });
    const framePage = await frameContext.newPage();
    await framePage.setContent(
      '<html lang="zh"><body style="font:22px sans-serif;background:#f3f6fb;display:grid;place-items:center;height:90vh"><main style="padding:45px;background:white;border:1px solid #dbe3f0;border-radius:14px"><h2>企业统一认证 · 测试画面</h2><p>请在这里完成登录</p><label>账号 <input placeholder="企业账号"></label><br><br><label>密码 <input type="password"></label><p style="color:#667;font-size:15px">仅用于界面测试，不会提交登录</p></main></body></html>',
    );
    const frame = await framePage.screenshot({ type: "jpeg" });
    await frameContext.close();
    const controls: string[] = [];
    let manual = false;
    let rejectResume = true;
    let socketCount = 0;
    let currentSocket: WebSocketRoute | undefined;
    let rejectReconnect = false;
    const sessionId = "a".repeat(32);
    const socketURLs: string[] = [];
    await page.routeWebSocket(/\/api\/threads\/.*\/browser\/stream/, (ws) => {
      socketCount++;
      currentSocket = ws;
      socketURLs.push(ws.url());
      if (rejectReconnect) {
        expect(new URL(ws.url()).searchParams.get("session_id")).toBe(
          sessionId,
        );
        ws.close({ code: 4410, reason: "session changed" });
        return;
      }
      ws.send(
        JSON.stringify({
          type: "login_state",
          manual_login: manual,
          session_id: sessionId,
        }),
      );
      ws.send(frame);
      ws.send(JSON.stringify({ type: "url", url: "https://au.cnooc.com.cn/" }));
      ws.onMessage((message) => {
        const event = JSON.parse(String(message));
        if (event.type !== "login_control") return;
        controls.push(event.action);
        if (event.action === "resume" && rejectResume) {
          ws.send(JSON.stringify({ type: "login_error" }));
          return;
        }
        manual = event.action === "begin";
        ws.send(
          JSON.stringify({
            type: "login_state",
            manual_login: manual,
            session_id: sessionId,
          }),
        );
        ws.send(JSON.stringify({ type: "login_ack" }));
        if (event.action === "clear") {
          ws.send(JSON.stringify({ type: "url", url: "about:blank" }));
        }
      });
    });
    await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
    await expect(page.getByTestId("browser-trigger")).toBeVisible({
      timeout: 60000,
    });
    await page.getByTestId("browser-trigger").click();
    const section = page.getByRole("region", { name: "人工登录" });
    await expect(section).toBeVisible();
    await expect(
      section.getByRole("button", { name: "人工登录", exact: true }),
    ).toBeEnabled();
    const establishedSocketCount = socketCount;
    await section
      .getByRole("button", { name: "人工登录", exact: true })
      .click();
    await expect(
      section.getByText(
        "人工登录中：助手暂停读取和操作浏览器。请直接在下方网页输入账号、密码或验证码。",
      ),
    ).toBeVisible();
    await expect(
      section.getByRole("button", { name: "完成登录，交还助手" }),
    ).toBeEnabled();
    await section.getByRole("button", { name: "完成登录，交还助手" }).click();
    await expect(section.getByRole("alert")).toBeVisible();
    await expect(
      section.getByRole("button", { name: "完成登录，交还助手" }),
    ).toBeEnabled();
    rejectResume = false;
    await section.getByRole("button", { name: "完成登录，交还助手" }).click();
    await expect(
      section.getByRole("button", { name: "人工登录", exact: true }),
    ).toBeEnabled();
    await expect(section.getByRole("alert")).toHaveCount(0);
    await section.getByRole("button", { name: "清除登录状态" }).click();
    await expect(
      section.getByRole("button", { name: "人工登录", exact: true }),
    ).toBeEnabled();
    expect(controls).toEqual(["begin", "resume", "resume", "clear"]);
    expect(socketCount).toBe(establishedSocketCount);
    rejectReconnect = true;
    currentSocket!.close({ code: 1012, reason: "restart fixture" });
    await expect(
      section.getByText(
        "未连接到原来的浏览器会话，已停止自动重连。重新连接后可能需要再次登录。",
      ),
    ).toBeVisible();
    const stoppedCount = socketCount;
    await page.waitForTimeout(1200);
    expect(socketCount).toBe(stoppedCount);
    rejectReconnect = false;
    await section.getByRole("button", { name: "重新连接浏览器" }).click();
    await expect(
      section.getByRole("button", { name: "人工登录", exact: true }),
    ).toBeEnabled();
    expect(new URL(socketURLs.at(-1)!).searchParams.has("session_id")).toBe(
      false,
    );
    await expect(
      section.getByRole("button", { name: "重新连接浏览器" }),
    ).toHaveCount(0);
    await expect(page).toHaveURL(new RegExp(MOCK_THREAD_ID));
    expect(await page.title()).not.toEqual("");
    expect(errors).toEqual([]);
    expect(warnings).toEqual([]);
    expect(await page.locator("nextjs-portal").count()).toBeLessThanOrEqual(1);
  });
}
