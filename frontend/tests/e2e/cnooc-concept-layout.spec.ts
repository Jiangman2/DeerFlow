import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

async function useChineseLocale(page: Page) {
  await page.context().addCookies([
    {
      name: "locale",
      value: "zh-CN",
      url: test.info().project.use.baseURL ?? "http://localhost:3000",
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
  await page.route("**/api/weknora/knowledge/search?*", async (route) => {
    const keyword = new URL(route.request().url()).searchParams
      .get("keyword")
      ?.toLocaleLowerCase();
    const items = [
      {
        id: "file-1",
        knowledge_base_id: "kb-1",
        title: "井控操作手册.pdf",
        file_type: "pdf",
        parse_status: "completed",
        tags: [
          {
            id: "tag-1",
            name: "井控",
            color: "#805ad5",
            knowledge_base_id: "kb-1",
          },
        ],
      },
      {
        id: "file-2",
        knowledge_base_id: "kb-1",
        title: "生产运行制度.docx",
        file_type: "docx",
        parse_status: "completed",
      },
      {
        id: "file-3",
        knowledge_base_id: "kb-2",
        title: "月报模板与海上油气安全生产综合分析工作簿.xlsx",
        file_type: "xlsx",
        parse_status: "completed",
        tags: [
          {
            id: "tag-2",
            name: "月报",
            color: "#3182ce",
            knowledge_base_id: "kb-2",
          },
        ],
      },
      ...Array.from({ length: 24 }, (_, index) => ({
        id: `file-${index + 4}`,
        knowledge_base_id: index % 2 === 0 ? "kb-1" : "kb-2",
        title:
          index % 3 === 0
            ? `短文档-${index + 4}.pdf`
            : `知识范围滚动验证文档-${String(index + 1).padStart(2, "0")}.pdf`,
        file_type: "pdf",
        parse_status: "completed",
      })),
    ].filter((item) =>
      keyword ? item.title.toLocaleLowerCase().includes(keyword) : true,
    );
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items,
        total: items.length,
        has_more: false,
      }),
    });
  });
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
  await page.route(
    "**/api/weknora/knowledge-bases/kb-2/tags?*",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "tag-2",
              name: "月报",
              color: "#3182ce",
              knowledge_base_id: "kb-2",
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
    await expect(
      rail.locator('[data-slot="suggestion-item"] > :is(button, a)'),
    ).toHaveCount(7);
    await expectLoadedImages(page, ".cnooc-welcome-actions img", 7);
    await page.waitForTimeout(800);

    const actionBoxes = await rail
      .locator('[data-slot="suggestion-item"] > :is(button, a)')
      .evaluateAll((buttons) =>
        buttons.map((button) => {
          const box = button.getBoundingClientRect();
          return { width: box.width, height: box.height };
        }),
      );
    expect(actionBoxes).toHaveLength(7);
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

    await page.getByTestId("portal-tools-trigger").click();
    await expect(page.getByTestId("portal-tool-liveTranscribe")).toBeVisible();
    await expect(page.getByTestId("portal-tool-convert")).toBeVisible();
    await expect(
      page.getByTestId("portal-tool-recordTranscribe"),
    ).toBeVisible();
    const moreTriggerStyle = await page
      .getByTestId("portal-tools-trigger")
      .evaluate((button) => {
        const image = button.querySelector(".cnooc-action-artwork");
        return {
          fontSize: getComputedStyle(button).fontSize,
          imageWidth: image?.getBoundingClientRect().width,
        };
      });
    const moreOptionStyles = await page
      .locator('.cnooc-portal-menu [role="menuitem"]')
      .evaluateAll((items) =>
        items.map((item) => ({
          fontSize: getComputedStyle(item).fontSize,
          imageWidth: item
            .querySelector(".cnooc-action-artwork")
            ?.getBoundingClientRect().width,
        })),
      );
    for (const style of moreOptionStyles) {
      expect(Number.parseFloat(style.fontSize)).toBeGreaterThanOrEqual(12);
      expect(
        Math.abs((style.imageWidth ?? 0) - (moreTriggerStyle.imageWidth ?? 0)),
      ).toBeLessThanOrEqual(1);
    }
    await page.keyboard.press("Escape");

    const composerBox = await composer.boundingBox();
    const railBox = await rail.boundingBox();
    expect(composerBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(railBox!.width).toBeLessThanOrEqual(composerBox!.width);
    expect(railBox!.width / composerBox!.width).toBeGreaterThanOrEqual(0.8);
    expect(
      Math.abs(
        composerBox!.x +
          composerBox!.width / 2 -
          (railBox!.x + railBox!.width / 2),
      ),
    ).toBeLessThanOrEqual(3);

    await page.screenshot({
      path: test.info().outputPath("cnooc-concept-desktop-expanded.png"),
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
      expect(Number.parseFloat(style.fontSize)).toBeGreaterThanOrEqual(14);
      expect(Number.parseFloat(style.fontSize)).toBeLessThanOrEqual(
        Number.parseFloat(modelControlStyle.fontSize),
      );
    }
    await expect(page.locator(".cnooc-tool-datalake")).toHaveAttribute(
      "data-active",
      "false",
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
      // At this 1440x900 viewport the existing compact desktop media rule
      // uses 66px entries with at least an 8px gap; separate menu groups
      // add their own padding. Wide-screen 80px entries do not apply here.
      expect(step).toBeGreaterThanOrEqual(66 + 8);
    }
    for (let index = 0; index < count; index += 1) {
      const button = menuButtons.nth(index);
      const buttonBox = await button.boundingBox();
      expect(buttonBox).not.toBeNull();
      expect(buttonBox!.height).toBe(66);
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
      path: test.info().outputPath("cnooc-concept-desktop-collapsed.png"),
      fullPage: true,
    });
  });

  test("skill creation welcome stays centered below the branded header", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?mode=skill&visualQa=cnooc-concept");

    const panel = page.getByTestId("welcome-panel");
    const heading = panel.getByRole("heading", {
      level: 1,
      name: "创建您的专属技能",
    });
    const textbox = page.getByRole("textbox").first();

    await expect(heading).toBeVisible({ timeout: 15_000 });
    await expect(panel).toContainText(
      "描述您希望智能体完成的工作，我们会协助您搭建可复用的技能。",
    );
    await expect(textbox).toBeEditable();

    const desktopLayout = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector(selector);
        if (!element) {
          throw new Error(`Missing ${selector}`);
        }
        const bounds = element.getBoundingClientRect();
        return {
          top: bounds.top,
          right: bounds.right,
          bottom: bounds.bottom,
          left: bounds.left,
        };
      };
      return {
        brand: box(".cnooc-desktop-brandline"),
        panel: box(".cnooc-welcome-skill"),
        composer: box('[data-testid="welcome-composer"]'),
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
      };
    });

    expect(desktopLayout.panel.top).toBeGreaterThan(
      desktopLayout.brand.bottom + 60,
    );
    expect(desktopLayout.panel.bottom).toBeLessThan(
      desktopLayout.composer.top - 24,
    );
    expect(desktopLayout.scrollWidth).toBeLessThanOrEqual(
      desktopLayout.viewportWidth,
    );
    await page.screenshot({
      path: test.info().outputPath("cnooc-skill-mode-desktop.png"),
      fullPage: true,
    });

    await textbox.fill("创建一个测试技能");
    await expect(textbox).toHaveValue("创建一个测试技能");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(heading).toBeVisible();
    const mobileLayout = await page.evaluate(() => {
      const box = (selector: string) => {
        const element = document.querySelector(selector);
        if (!element) {
          throw new Error(`Missing ${selector}`);
        }
        const bounds = element.getBoundingClientRect();
        return {
          top: bounds.top,
          right: bounds.right,
          bottom: bounds.bottom,
          left: bounds.left,
        };
      };
      return {
        header: box(".cnooc-chat-header"),
        panel: box(".cnooc-welcome-skill"),
        composer: box('[data-testid="welcome-composer"]'),
        scrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
      };
    });

    expect(mobileLayout.panel.top).toBeGreaterThanOrEqual(
      mobileLayout.header.bottom,
    );
    expect(mobileLayout.panel.left).toBeGreaterThanOrEqual(0);
    expect(mobileLayout.panel.right).toBeLessThanOrEqual(390);
    expect(mobileLayout.panel.bottom).toBeLessThan(
      mobileLayout.composer.top - 12,
    );
    expect(mobileLayout.scrollWidth).toBeLessThanOrEqual(
      mobileLayout.viewportWidth,
    );
    await page.screenshot({
      path: test.info().outputPath("cnooc-skill-mode-mobile.png"),
      fullPage: true,
    });
  });

  test("more action stays last and removed actions stay absent in the responsive welcome rail", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    mockLangGraphAPI(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept");
    const rail = page.getByTestId("welcome-suggestion-rail");
    await expect(rail).toBeVisible({ timeout: 15_000 });
    await expect(
      rail.locator('[data-slot="suggestion-item"] > :is(button, a)'),
    ).toHaveCount(7);
    await expect(rail.getByTestId("surprise-suggestion")).toHaveCount(0);
    await expect(rail.getByTestId("create-menu-trigger")).toHaveCount(0);
    await expect(
      rail.locator('[data-slot="suggestion-item"] > :is(button, a)').last(),
    ).toHaveAttribute("data-testid", "portal-tools-trigger");

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      rail.locator('[data-slot="suggestion-item"] > :is(button, a)'),
    ).toHaveCount(7);
    await expect(
      rail.locator('[data-slot="suggestion-item"] > :is(button, a)').last(),
    ).toHaveAttribute("data-testid", "portal-tools-trigger");
  });

  test("knowledge scope mirrors WeKnora mention groups and selected chips", async ({
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
    await page.getByLabel("Upload files").setInputFiles({
      name: "layout-proof.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("layout proof"),
    });
    const attachmentChip = page
      .locator(".cnooc-composer-attachment-chip")
      .first();
    await expect(attachmentChip).toBeVisible();
    await trigger.click();

    const menu = page.getByTestId("knowledge-scope-menu");
    await expect(menu).toBeVisible();
    await expect(page.getByTestId("knowledge-scope-group-kb")).toBeVisible();
    await expect(page.getByTestId("knowledge-scope-group-tag")).toBeVisible();
    await expect(page.getByTestId("knowledge-scope-group-file")).toBeVisible();

    await page.getByTestId("knowledge-scope-group-file").click();
    const listViewport = page.getByTestId("knowledge-scope-list");
    await expect
      .poll(() =>
        listViewport.evaluate(
          (element) => element.scrollHeight > element.clientHeight,
        ),
      )
      .toBe(true);
    await listViewport.hover();
    await page.mouse.wheel(0, 1800);
    await expect
      .poll(() => listViewport.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await listViewport.evaluate((element) => {
      element.scrollTop = 0;
    });
    const file = page.getByTestId("knowledge-document-kb-2-file-3");
    await expect(file).toContainText("月报模板");
    await expect(file).toContainText("月报资料库");
    await expect(file).toContainText("月报");
    await file.click();
    await expect(menu).toBeVisible();
    await expect(page.getByTestId("knowledge-scope-clear")).toBeVisible();
    await expect
      .poll(() =>
        file.evaluate((element) => element.scrollWidth <= element.clientWidth),
      )
      .toBe(true);
    await expect(trigger).toHaveAttribute("data-selected-count", "1");
    const fileScopeChip = page.getByTestId("knowledge-scope-chip-file-file-3");
    await expect(fileScopeChip).toBeVisible();
    const [modelBox, attachmentBox, scopeBox, composerBox] = await Promise.all([
      modelSelector.boundingBox(),
      attachmentChip.boundingBox(),
      fileScopeChip.boundingBox(),
      page.getByTestId("welcome-composer").boundingBox(),
    ]);
    expect(modelBox).not.toBeNull();
    expect(attachmentBox).not.toBeNull();
    expect(scopeBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(scopeBox!.y).toBeGreaterThanOrEqual(
      modelBox!.y + modelBox!.height + 4,
    );
    const materialGap = scopeBox!.x - (attachmentBox!.x + attachmentBox!.width);
    expect(materialGap).toBeGreaterThanOrEqual(0);
    expect(materialGap).toBeLessThanOrEqual(12);
    expect(attachmentBox!.width).toBeLessThanOrEqual(240);
    expect(scopeBox!.width).toBeLessThanOrEqual(240);
    await expect
      .poll(() =>
        fileScopeChip.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBe(true);
    await expect
      .poll(() =>
        attachmentChip.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      )
      .toBe(true);
    expect(scopeBox!.x + scopeBox!.width).toBeLessThanOrEqual(
      composerBox!.x + composerBox!.width,
    );
    await listViewport.hover();
    await page.mouse.wheel(0, 1800);
    await expect
      .poll(() => listViewport.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await listViewport.evaluate((element) => {
      element.scrollTop = 0;
    });
    await file.click();
    await expect(trigger).toHaveAttribute("data-selected-count", "0");
    await expect(page.getByTestId("knowledge-scope-clear")).toBeHidden();
    await file.click();
    await expect(trigger).toHaveAttribute("data-selected-count", "1");

    await page.getByTestId("knowledge-scope-back").click();
    await page.getByTestId("knowledge-scope-group-tag").click();
    const tag = page.getByTestId("knowledge-tag-kb-1-tag-1");
    await expect(tag).toContainText("井控");
    await expect(tag).toContainText("运行制度库");
    await tag.click();
    await expect(trigger).toHaveAttribute("data-selected-count", "2");
    await expect(
      page.getByTestId("knowledge-scope-chip-tag-tag-1"),
    ).toBeVisible();
    await expect(trigger).toHaveAttribute("data-active", "true");

    await page.getByTestId("knowledge-scope-back").click();
    const search = page.getByRole("textbox", {
      name: "搜索知识库、标签或文件",
    });
    await search.fill("月报");
    await expect(page.getByTestId("knowledge-base-item-kb-2")).toBeVisible();
    await expect(page.getByTestId("knowledge-tag-kb-2-tag-2")).toBeVisible();
    await expect(
      page.getByTestId("knowledge-document-kb-2-file-3"),
    ).toBeVisible();

    await page.getByTestId("knowledge-base-item-kb-2").click();
    await expect(
      page.getByTestId("knowledge-scope-chip-file-file-3"),
    ).toBeHidden();
    await expect(
      page.getByTestId("knowledge-scope-chip-kb-kb-2"),
    ).toBeVisible();
    await expect(
      page.getByTestId("knowledge-scope-chip-tag-tag-1"),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await page.screenshot({
      path: test.info().outputPath("cnooc-concept-desktop-materials.png"),
      fullPage: true,
    });

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
    await expect(page.getByTestId("knowledge-scope-count")).toBeHidden();
    const mobileToolMetrics = await page
      .getByTestId("composer-primary-tools")
      .evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }));
    expect(mobileToolMetrics.scrollWidth).toBeLessThanOrEqual(
      mobileToolMetrics.clientWidth + 1,
    );
    await trigger.click();
    await expect(menu).toBeVisible();
    await expect
      .poll(async () => {
        const menuBox = await menu.boundingBox();
        return menuBox ? menuBox.x + menuBox.width : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(390);
  });

  test("many attachments and knowledge scopes stay contained on desktop and reachable on mobile", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await useChineseLocale(page);
    mockLangGraphAPI(page);
    await mockWeKnoraKnowledgeScope(page);

    await page.goto("/workspace/chats/new?visualQa=cnooc-concept");
    await expect(page.getByRole("textbox").first()).toBeEditable({
      timeout: 15_000,
    });
    await page.getByLabel("Upload files").setInputFiles(
      Array.from({ length: 8 }, (_, index) => ({
        name: `附件验证-${String(index + 1).padStart(2, "0")}-较长文件名.txt`,
        mimeType: "text/plain",
        buffer: Buffer.from(`layout proof ${index + 1}`),
      })),
    );

    const trigger = page.getByTestId("knowledge-scope-trigger");
    await trigger.click();
    await page.getByTestId("knowledge-scope-group-file").click();
    const documentParents = [
      "kb-1",
      "kb-1",
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
    ];
    for (const [index, parentId] of documentParents.entries()) {
      await page
        .getByTestId(`knowledge-document-${parentId}-file-${index + 1}`)
        .click();
    }
    await page.getByTestId("knowledge-scope-back").click();
    await page.getByTestId("knowledge-scope-group-tag").click();
    await page.getByTestId("knowledge-tag-kb-1-tag-1").click();
    await page.getByTestId("knowledge-tag-kb-2-tag-2").click();
    await page.getByTestId("knowledge-scope-back").click();
    await page.getByTestId("knowledge-scope-group-file").click();
    const additionalDocumentParents = [
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
      "kb-2",
      "kb-1",
    ];
    for (const [index, parentId] of additionalDocumentParents.entries()) {
      const documentIndex = index + 9;
      await page
        .getByTestId(`knowledge-document-${parentId}-file-${documentIndex}`)
        .click();
    }
    await page.getByTestId("knowledge-document-kb-2-file-19").click();
    await expect(
      page.getByText("知识范围最多选择 20 项", { exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    const header = page.locator(".cnooc-composer-header");
    const materials = page.locator(
      ".cnooc-composer-attachment-chip, .cnooc-knowledge-scope-chip",
    );
    await expect(page.getByTestId("knowledge-scope-count")).toHaveText("20");
    await expect(materials).toHaveCount(28);
    const desktopMetrics = await header.evaluate((element) => {
      const headerRect = element.getBoundingClientRect();
      const inputRect = element.parentElement
        ?.querySelector(".cnooc-composer-input")
        ?.getBoundingClientRect();
      const boxes = Array.from(
        element.querySelectorAll(
          ".cnooc-composer-attachment-chip, .cnooc-knowledge-scope-chip",
        ),
      ).map((item) => {
        const rect = item.getBoundingClientRect();
        return {
          bottom: rect.bottom,
          left: rect.left,
          right: rect.right,
          top: rect.top,
          width: rect.width,
        };
      });
      return {
        boxes,
        clientWidth: element.clientWidth,
        headerBottom: headerRect.bottom,
        headerLeft: headerRect.left,
        headerRight: headerRect.right,
        inputTop: inputRect?.top ?? 0,
        rows: new Set(boxes.map((box) => Math.round(box.top))).size,
        rowCounts: Array.from(
          boxes.reduce((counts, box) => {
            const key = Math.round(box.top);
            counts.set(key, (counts.get(key) ?? 0) + 1);
            return counts;
          }, new Map<number, number>()),
          ([, count]) => count,
        ),
        reportedRows: Number(
          element
            .closest<HTMLElement>("[data-testid='welcome-composer']")
            ?.style.getPropertyValue("--cnooc-desktop-material-rows") ?? "0",
        ),
        scrollWidth: element.scrollWidth,
      };
    });
    expect(desktopMetrics.scrollWidth).toBeLessThanOrEqual(
      desktopMetrics.clientWidth + 1,
    );
    expect(desktopMetrics.rows).toBeGreaterThanOrEqual(4);
    expect(Math.max(...desktopMetrics.rowCounts)).toBeGreaterThan(4);
    expect(desktopMetrics.reportedRows).toBe(desktopMetrics.rows);
    expect(desktopMetrics.headerBottom).toBeLessThanOrEqual(
      desktopMetrics.inputTop,
    );
    for (const box of desktopMetrics.boxes) {
      expect(box.left).toBeGreaterThanOrEqual(desktopMetrics.headerLeft - 1);
      expect(box.right).toBeLessThanOrEqual(desktopMetrics.headerRight + 1);
      expect(box.width).toBeLessThanOrEqual(240);
    }
    await page.screenshot({
      path: test.info().outputPath("cnooc-concept-desktop-materials-many.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    const mobileMetrics = await header.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(mobileMetrics.scrollWidth).toBeGreaterThan(
      mobileMetrics.clientWidth,
    );
    await expect
      .poll(async () => {
        await header.evaluate((element) => {
          element.scrollLeft = element.scrollWidth;
        });
        const box = await materials.last().boundingBox();
        return box ? box.x + box.width : Number.POSITIVE_INFINITY;
      })
      .toBeLessThanOrEqual(390);
    await page.screenshot({
      path: test.info().outputPath("cnooc-concept-mobile-materials-many.png"),
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
    await expectLoadedImages(page, ".cnooc-welcome-actions img", 7);
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
      .locator('[data-slot="suggestion-item"] > :is(button, a)');
    await expect(mobileActions).toHaveCount(7);
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
      expect(
        Math.abs(box.width - mobileActionBoxes[0]!.width),
      ).toBeLessThanOrEqual(1);
      expect(
        Math.abs(box.height - mobileActionBoxes[0]!.height),
      ).toBeLessThanOrEqual(1);
    }
    const mobileActionViewport = page
      .getByTestId("welcome-suggestion-rail")
      .locator('[data-slot="scroll-area-viewport"]');
    const mobileActionViewportMetrics = await mobileActionViewport.evaluate(
      (element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }),
    );
    expect(mobileActionViewportMetrics.scrollWidth).toBeGreaterThan(
      mobileActionViewportMetrics.clientWidth,
    );
    await mobileActionViewport.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
    });
    const lastMobileActionBox = await mobileActions.last().boundingBox();
    expect(lastMobileActionBox).not.toBeNull();
    expect(
      lastMobileActionBox!.x + lastMobileActionBox!.width,
    ).toBeLessThanOrEqual(390);

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

    await page.getByTestId("portal-tools-trigger").click();
    const mobileMoreTriggerStyle = await page
      .getByTestId("portal-tools-trigger")
      .evaluate((button) => ({
        fontSize: getComputedStyle(button).fontSize,
        imageWidth: button
          .querySelector(".cnooc-action-artwork")
          ?.getBoundingClientRect().width,
      }));
    const mobileMoreOptionStyles = await page
      .locator('.cnooc-portal-menu [role="menuitem"]')
      .evaluateAll((items) =>
        items.map((item) => ({
          fontSize: getComputedStyle(item).fontSize,
          imageWidth: item
            .querySelector(".cnooc-action-artwork")
            ?.getBoundingClientRect().width,
        })),
      );
    for (const style of mobileMoreOptionStyles) {
      expect(Number.parseFloat(style.fontSize)).toBeGreaterThanOrEqual(12);
      expect(
        Math.abs(
          (style.imageWidth ?? 0) - (mobileMoreTriggerStyle.imageWidth ?? 0),
        ),
      ).toBeLessThanOrEqual(4);
    }
    await page.keyboard.press("Escape");

    await expect(page.locator(".cnooc-tool-datalake")).toHaveAttribute(
      "data-active",
      "false",
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
    await page
      .getByRole("button", { name: /Settings and more|设置和更多/ })
      .click();
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
      path: test.info().outputPath("cnooc-concept-mobile.png"),
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
            '[data-testid="welcome-suggestion-rail"] [data-slot="suggestion-item"] > :is(button, a)',
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
      expect(geometry.actionBoxes).toHaveLength(7);
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
