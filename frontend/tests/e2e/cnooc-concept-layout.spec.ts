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
});
