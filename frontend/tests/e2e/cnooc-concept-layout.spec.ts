import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

async function useChineseLocale(page: Page) {
  await page.context().addCookies([
    {
      name: "locale",
      value: "zh-CN",
      domain: "127.0.0.1",
      path: "/",
    },
  ]);
}

async function expectLoadedImages(
  page: Page,
  selector: string,
  minimum: number,
) {
  const images = page.locator(selector);
  await expect.poll(() => images.count()).toBeGreaterThanOrEqual(minimum);
  await expect
    .poll(() =>
      images.evaluateAll(
        (nodes) =>
          nodes.filter((node) => {
            const image = node as HTMLImageElement;
            const styles = getComputedStyle(image);
            return (
              image.complete &&
              image.naturalWidth > 0 &&
              styles.display !== "none" &&
              styles.visibility !== "hidden"
            );
          }).length,
      ),
    )
    .toBeGreaterThanOrEqual(minimum);
}

async function mockWeKnoraKnowledgeScope(page: Page) {
  await page.route("**/api/weknora/knowledge-bases", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            id: "kb-1",
            name: "运行制度库",
            type: "document",
            access_tenant_id: "11",
            knowledge_count: 2,
          },
          {
            id: "kb-2",
            name: "月报资料库",
            type: "document",
            access_tenant_id: "12",
            knowledge_count: 1,
          },
        ],
        total: 2,
      }),
    });
  });
  await page.route(
    "**/api/weknora/knowledge-bases/kb-1/knowledge?*",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "file-1",
              knowledge_base_id: "kb-1",
              title: "井控操作手册.pdf",
              file_type: "pdf",
              parse_status: "completed",
            },
            {
              id: "file-2",
              knowledge_base_id: "kb-1",
              title: "生产运行制度.docx",
              file_type: "docx",
              parse_status: "completed",
            },
          ],
          total: 2,
        }),
      });
    },
  );
  await page.route(
    "**/api/weknora/knowledge-bases/kb-1/tags?*",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "tag-1",
              name: "井控",
              knowledge_base_id: "kb-1",
              knowledge_count: 1,
            },
          ],
          total: 1,
        }),
      });
    },
  );
}

test.describe("CNOOC concept layout", () => {
  test("desktop composition and collapsed rail stay aligned", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept");
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    const composer = page.getByTestId("welcome-composer");
    const rail = page.getByTestId("welcome-suggestion-rail");
    await expect(composer).toBeVisible();
    await expect(rail).toBeVisible();
    await expect(rail.getByRole("button")).toHaveCount(6);
    await expectLoadedImages(page, ".cnooc-action-artwork", 6);
    await page.waitForTimeout(800);

    const actionBoxes = await rail
      .locator('[data-slot="suggestion-item"] > button')
      .evaluateAll((buttons) =>
        buttons.map((button) => {
          const box = button.getBoundingClientRect();
          return { width: box.width, height: box.height };
        }),
      );
    expect(actionBoxes).toHaveLength(6);
    const actionWidths = actionBoxes.map((box) => box.width);
    expect(Math.min(...actionWidths)).toBeGreaterThan(100);
    expect(
      Math.max(...actionWidths) - Math.min(...actionWidths),
    ).toBeLessThanOrEqual(32);
    for (const box of actionBoxes) {
      expect(Math.abs(box.height - actionBoxes[0]!.height)).toBeLessThanOrEqual(
        1,
      );
    }

    await page.getByTestId("create-menu-trigger").click();
    await expect(page.getByTestId("create-option-0")).toBeVisible();
    await expect(page.getByTestId("create-option-1")).toBeVisible();
    await expect(page.getByTestId("create-option-2")).toBeVisible();
    const createTriggerStyle = await page
      .getByTestId("create-menu-trigger")
      .evaluate((button) => {
        const image = button.querySelector(".cnooc-action-artwork");
        return {
          fontSize: getComputedStyle(button).fontSize,
          imageWidth: image?.getBoundingClientRect().width,
        };
      });
    const createOptionStyles = await page
      .locator('.cnooc-create-menu [role="menuitem"]')
      .evaluateAll((items) =>
        items.map((item) => ({
          fontSize: getComputedStyle(item).fontSize,
          imageWidth: item
            .querySelector(".cnooc-action-artwork")
            ?.getBoundingClientRect().width,
        })),
      );
    for (const style of createOptionStyles) {
      expect(style.fontSize).toBe(createTriggerStyle.fontSize);
      expect(style.imageWidth).toBe(createTriggerStyle.imageWidth);
    }
    await page.keyboard.press("Escape");

    const composerBox = await composer.boundingBox();
    const railBox = await rail.boundingBox();
    expect(composerBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(Math.abs(composerBox!.width - railBox!.width)).toBeLessThanOrEqual(
      3,
    );

    await page.screenshot({
      path: "test-results/cnooc-concept-desktop-expanded.png",
      fullPage: true,
    });

    await expect(page.locator(".cnooc-scheduled-task-link")).toBeHidden();

    const modelControlStyle = await page
      .locator(".cnooc-model-selector")
      .evaluate((button) => {
        const name = button.querySelector(".cnooc-model-name");
        const chevron = button.querySelector(".cnooc-model-chevron");
        const buttonBox = button.getBoundingClientRect();
        const nameBox = name?.getBoundingClientRect();
        const chevronBox = chevron?.getBoundingClientRect();
        return {
          color: getComputedStyle(button).color,
          fontSize: name ? getComputedStyle(name).fontSize : "",
          unusedWidth:
            buttonBox.width - (nameBox?.width ?? 0) - (chevronBox?.width ?? 0),
        };
      });
    expect(modelControlStyle.unusedWidth).toBeLessThanOrEqual(52);
    for (const selector of [
      ".cnooc-tool-attachments",
      ".cnooc-tool-datalake",
      ".cnooc-tool-knowledge",
      ".cnooc-tool-mode",
    ]) {
      const style = await page.locator(selector).evaluate((button) => ({
        color: getComputedStyle(button).color,
        fontSize: getComputedStyle(button).fontSize,
      }));
      expect(style.color).toBe(modelControlStyle.color);
      expect(style.fontSize).toBe(modelControlStyle.fontSize);
    }
    await expect(page.locator(".cnooc-tool-datalake")).toHaveAttribute(
      "data-active",
      "true",
    );
    await expect(page.locator(".cnooc-tool-knowledge")).toHaveAttribute(
      "data-active",
      "false",
    );

    await page.locator("[data-sidebar='trigger']:visible").first().click();
    const collapsedSidebar = page.locator(
      ".group[data-state='collapsed'] [data-sidebar='sidebar']",
    );
    await expect(collapsedSidebar).toBeVisible();
    await page.waitForTimeout(400);

    const menuButtons = collapsedSidebar.locator(
      "[data-sidebar='menu-button']",
    );
    const collapsedBrand = collapsedSidebar.locator(
      ".cnooc-collapsed-brand-slot",
    );
    const collapsedBrandBox = await collapsedBrand.boundingBox();
    const collapsedSidebarBox = await collapsedSidebar.boundingBox();
    expect(collapsedBrandBox).not.toBeNull();
    expect(collapsedSidebarBox).not.toBeNull();
    expect(
      Math.abs(
        collapsedBrandBox!.x +
          collapsedBrandBox!.width / 2 -
          (collapsedSidebarBox!.x + collapsedSidebarBox!.width / 2),
      ),
    ).toBeLessThanOrEqual(2);
    await collapsedBrand.hover();
    const collapsedBrandTrigger = collapsedBrand.locator(
      ".cnooc-collapsed-brand-trigger",
    );
    await expect(collapsedBrandTrigger).toBeVisible();
    const collapsedBrandTriggerBox = await collapsedBrandTrigger.boundingBox();
    expect(collapsedBrandTriggerBox).not.toBeNull();
    expect(
      Math.abs(
        collapsedBrandTriggerBox!.x +
          collapsedBrandTriggerBox!.width / 2 -
          (collapsedSidebarBox!.x + collapsedSidebarBox!.width / 2),
      ),
    ).toBeLessThanOrEqual(2);
    const count = await menuButtons.count();
    expect(count).toBeGreaterThanOrEqual(5);
    const menuVerticalSteps = await menuButtons.evaluateAll((buttons) =>
      buttons.slice(1).map((button, index) => {
        const previous = buttons[index]!.getBoundingClientRect();
        const current = button.getBoundingClientRect();
        return current.y - previous.y;
      }),
    );
    for (const step of menuVerticalSteps.slice(0, 4)) {
      expect(step).toBeGreaterThanOrEqual(80);
    }
    for (let index = 0; index < count; index += 1) {
      const button = menuButtons.nth(index);
      const buttonBox = await button.boundingBox();
      expect(buttonBox).not.toBeNull();
      const center = buttonBox!.x + buttonBox!.width / 2;
      const sidebarBox = await collapsedSidebar.boundingBox();
      expect(sidebarBox).not.toBeNull();
      const sidebarCenter = sidebarBox!.x + sidebarBox!.width / 2;
      expect(
        Math.abs(center - sidebarCenter),
        `collapsed item ${index} (${await button.getAttribute("aria-label")}) must stay centered`,
      ).toBeLessThanOrEqual(8);

      const icon = button.locator(":scope > .cnooc-sidebar-icon-tile").first();
      const label = button
        .locator(":scope > span:not(.cnooc-sidebar-icon-tile)")
        .first();
      if ((await icon.count()) && (await label.count())) {
        const iconBox = await icon.boundingBox();
        const labelBox = await label.boundingBox();
        expect(iconBox).not.toBeNull();
        expect(labelBox).not.toBeNull();
        expect(
          Math.abs(iconBox!.x + iconBox!.width / 2 - sidebarCenter),
        ).toBeLessThanOrEqual(2);
        expect(
          Math.abs(labelBox!.x + labelBox!.width / 2 - sidebarCenter),
        ).toBeLessThanOrEqual(2);
        expect(labelBox!.y).toBeGreaterThanOrEqual(
          iconBox!.y + iconBox!.height,
        );
      }
    }

    await page.screenshot({
      path: "test-results/cnooc-concept-desktop-collapsed.png",
      fullPage: true,
    });
  });

  test("surprise action stays last in the responsive welcome rail", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept");
    const rail = page.getByTestId("welcome-suggestion-rail");
    await expect(rail).toBeVisible({ timeout: 15_000 });
    await expect(rail.getByRole("button")).toHaveCount(6);
    await expect(rail.getByTestId("surprise-suggestion")).toBeVisible();
    await expect(
      rail.locator('[data-slot="suggestion-item"] > button').last(),
    ).toHaveAttribute("data-testid", "surprise-suggestion");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(rail.getByRole("button")).toHaveCount(6);
    await expect(
      rail.locator('[data-slot="suggestion-item"] > button').last(),
    ).toHaveAttribute("data-testid", "surprise-suggestion");
  });

  test("knowledge scope selects exact files and tags on desktop and mobile", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);
    await mockWeKnoraKnowledgeScope(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept");
    const trigger = page.getByTestId("knowledge-scope-trigger");
    const modelName = page.locator(".cnooc-model-name").first();
    const modelSelector = page.locator(".cnooc-model-selector").first();
    await expect(trigger).toBeVisible({ timeout: 15_000 });
    await expect(trigger).toContainText("知识库");
    await expect(modelName).toHaveText("DeepSeek-V4-Flash");
    await expect
      .poll(() =>
        modelName.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBe(true);
    await expect
      .poll(async () => {
        const selectorBox = await modelSelector.boundingBox();
        const nameBox = await modelName.boundingBox();
        return selectorBox && nameBox
          ? selectorBox.width - nameBox.width
          : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(84);
    await trigger.click();

    const dialog = page.getByTestId("knowledge-scope-dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "运行制度库 2 个文件" }),
    ).toBeVisible();
    await dialog.getByRole("button", { name: /井控操作手册/ }).click();
    await dialog.getByRole("button", { name: "标签" }).click();
    await dialog.getByRole("button", { name: /井控/ }).last().click();
    await dialog.getByRole("button", { name: "应用范围" }).click();
    await expect(trigger).toContainText("知识库");
    await expect(trigger).toHaveAttribute("data-selected-count", "2");
    await expect(trigger).toHaveAttribute("data-active", "true");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(trigger).toContainText("知识库");
    await expect(modelName).toHaveText("DeepSeek-V4-Flash");
    await expect
      .poll(() =>
        modelName.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBe(true);
    await expect
      .poll(async () => {
        const selectorBox = await modelSelector.boundingBox();
        const nameBox = await modelName.boundingBox();
        return selectorBox && nameBox
          ? selectorBox.width - nameBox.width
          : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(64);
    await trigger.click();
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "文件", exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "标签", exact: true }),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "应用范围" }),
    ).toBeVisible();
  });

  test("mobile composition is compact and safe-area aware", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept-mobile");
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });

    await expect(page.locator(".cnooc-mobile-welcome-card")).toBeVisible();
    await expect(page.locator(".cnooc-welcome-mascot")).toBeVisible();
    await expectLoadedImages(page, ".cnooc-assistant-avatar-image", 1);
    await expectLoadedImages(page, ".cnooc-action-artwork", 6);
    await expect(page.getByTestId("welcome-suggestion-rail")).toBeVisible();
    await expect(page.getByTestId("welcome-composer")).toBeVisible();
    await expect(page.locator(".cnooc-mobile-new-chat")).toBeHidden();
    await expect(
      page
        .getByTestId("welcome-suggestion-rail")
        .locator(".cnooc-action-line-icon"),
    ).toHaveCount(0);
    await page.waitForTimeout(800);

    const mobileActions = page
      .getByTestId("welcome-suggestion-rail")
      .locator('[data-slot="suggestion-item"] > button');
    await expect(mobileActions).toHaveCount(6);
    const mobileActionBoxes = await mobileActions.evaluateAll((buttons) =>
      buttons.map((button) => {
        const box = button.getBoundingClientRect();
        return {
          left: box.left,
          right: box.right,
          width: box.width,
          height: box.height,
        };
      }),
    );
    for (const box of mobileActionBoxes) {
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.right).toBeLessThanOrEqual(390);
      expect(
        Math.abs(box.width - mobileActionBoxes[0]!.width),
      ).toBeLessThanOrEqual(1);
      expect(
        Math.abs(box.height - mobileActionBoxes[0]!.height),
      ).toBeLessThanOrEqual(1);
    }

    const mobileComposerBox = await page
      .locator(".cnooc-welcome-composer")
      .boundingBox();
    expect(mobileComposerBox).not.toBeNull();
    expect(mobileComposerBox!.height).toBeLessThanOrEqual(180);

    const modelSelectorBox = await page
      .locator(".cnooc-model-selector")
      .boundingBox();
    expect(modelSelectorBox).not.toBeNull();
    expect(modelSelectorBox!.width).toBeLessThanOrEqual(180);

    await page.getByTestId("create-menu-trigger").click();
    const mobileCreateTriggerStyle = await page
      .getByTestId("create-menu-trigger")
      .evaluate((button) => ({
        fontSize: getComputedStyle(button).fontSize,
        imageWidth: button
          .querySelector(".cnooc-action-artwork")
          ?.getBoundingClientRect().width,
      }));
    const mobileCreateOptionStyles = await page
      .locator('.cnooc-create-menu [role="menuitem"]')
      .evaluateAll((items) =>
        items.map((item) => ({
          fontSize: getComputedStyle(item).fontSize,
          imageWidth: item
            .querySelector(".cnooc-action-artwork")
            ?.getBoundingClientRect().width,
        })),
      );
    for (const style of mobileCreateOptionStyles) {
      expect(
        Math.abs(
          Number.parseFloat(style.fontSize) -
            Number.parseFloat(mobileCreateTriggerStyle.fontSize),
        ),
      ).toBeLessThanOrEqual(0.5);
      expect(
        Math.abs(
          (style.imageWidth ?? 0) - (mobileCreateTriggerStyle.imageWidth ?? 0),
        ),
      ).toBeLessThanOrEqual(4);
    }
    await page.keyboard.press("Escape");

    await expect(page.locator(".cnooc-tool-datalake")).toHaveAttribute(
      "data-active",
      "true",
    );
    await expect(page.locator(".cnooc-tool-knowledge")).toHaveAttribute(
      "data-active",
      "false",
    );

    await page.getByTestId("voice-input-button").hover();
    await expect(page.getByRole("tooltip")).toHaveText("语音输入");

    const metrics = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      bodyWidth: document.body.scrollWidth,
    }));
    expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth);
    expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewportWidth);

    await page.getByRole("button", { name: "打开导航菜单" }).click();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    const settingsMenu = page.locator(".cnooc-settings-menu");
    await expect(settingsMenu).toBeVisible();
    const settingsMenuBox = await settingsMenu.boundingBox();
    expect(settingsMenuBox).not.toBeNull();
    expect(settingsMenuBox!.x).toBeGreaterThanOrEqual(0);
    expect(settingsMenuBox!.y).toBeGreaterThanOrEqual(0);
    expect(settingsMenuBox!.x + settingsMenuBox!.width).toBeLessThanOrEqual(
      390,
    );
    expect(settingsMenuBox!.y + settingsMenuBox!.height).toBeLessThanOrEqual(
      844,
    );

    await page.screenshot({
      path: "test-results/cnooc-concept-mobile.png",
      fullPage: true,
    });
  });

  test("root opens new chat and desktop zoom-equivalent viewports stay composed", async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);

    await page.goto("/");
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });
    await expect(page.getByTestId("style-asset-health-probe")).toHaveCSS(
      "display",
      "none",
    );
    await page.waitForTimeout(800);

    const viewports = [
      { width: 1920, height: 1080, label: "100" },
      { width: 1536, height: 864, label: "125" },
      { width: 1280, height: 720, label: "150" },
      { width: 960, height: 540, label: "200" },
    ];

    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await expect(page.locator(".cnooc-desktop-welcome-copy")).toBeVisible();
      await expect(page.getByTestId("welcome-composer")).toBeVisible();
      await expect(page.getByTestId("welcome-suggestion-rail")).toBeVisible();

      const geometry = await page.evaluate(() => {
        const title = document
          .querySelector(".cnooc-desktop-welcome-copy")
          ?.getBoundingClientRect();
        const composer = document
          .querySelector(".cnooc-welcome-composer")
          ?.getBoundingClientRect();
        const rail = document
          .querySelector('[data-testid="welcome-suggestion-rail"]')
          ?.getBoundingClientRect();
        const actionBoxes = Array.from(
          document.querySelectorAll(
            '[data-testid="welcome-suggestion-rail"] [data-slot="suggestion-item"] > button',
          ),
        ).map((button) => {
          const box = button.getBoundingClientRect();
          return {
            left: box.left,
            right: box.right,
            top: box.top,
            bottom: box.bottom,
          };
        });
        return {
          titleBottom: title?.bottom ?? Number.POSITIVE_INFINITY,
          composerTop: composer?.top ?? 0,
          composerBottom: composer?.bottom ?? Number.POSITIVE_INFINITY,
          composerRight: composer?.right ?? Number.POSITIVE_INFINITY,
          railTop: rail?.top ?? 0,
          railBottom: rail?.bottom ?? Number.POSITIVE_INFINITY,
          actionBoxes,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          documentWidth: Math.max(
            document.body.scrollWidth,
            document.documentElement.scrollWidth,
          ),
          documentHeight: Math.max(
            document.body.scrollHeight,
            document.documentElement.scrollHeight,
          ),
        };
      });

      expect(
        geometry.titleBottom,
        `${viewport.label}% title must remain above the composer`,
      ).toBeLessThanOrEqual(geometry.composerTop + 2);
      expect(geometry.composerRight).toBeLessThanOrEqual(
        geometry.viewportWidth + 1,
      );
      expect(geometry.railTop).toBeGreaterThanOrEqual(
        geometry.composerBottom - 1,
      );
      expect(geometry.railBottom).toBeLessThanOrEqual(
        geometry.viewportHeight + 1,
      );
      expect(geometry.actionBoxes).toHaveLength(6);
      for (const action of geometry.actionBoxes) {
        expect(action.left).toBeGreaterThanOrEqual(0);
        expect(action.right).toBeLessThanOrEqual(geometry.viewportWidth + 1);
        expect(action.top).toBeGreaterThanOrEqual(geometry.composerBottom - 1);
        expect(action.bottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);
      }
      expect(geometry.documentWidth).toBeLessThanOrEqual(
        geometry.viewportWidth + 1,
      );
      expect(geometry.documentHeight).toBeLessThanOrEqual(
        geometry.viewportHeight + 1,
      );

      if (viewport.label === "200") {
        await page.screenshot({
          path: testInfo.outputPath("new-chat-desktop-zoom-200.png"),
          fullPage: true,
        });
      }
    }
  });
});
