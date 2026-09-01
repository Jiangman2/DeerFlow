import { afterEach, describe, expect, rs, test } from "@rstest/core";

import { fetchNewShopCartFiles } from "@/core/shop-cart/new-api";

describe("fetchNewShopCartFiles", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  test("preserves the Data Lake login-required error from the list route", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(() =>
        Promise.resolve(
          Response.json(
            {
              code: "data_lake_login_required",
              detail: "请先登录数据湖",
            },
            { status: 401 },
          ),
        ),
      ),
    );

    await expect(fetchNewShopCartFiles()).resolves.toEqual({
      ok: false,
      status: 401,
      code: "data_lake_login_required",
      message: "请先登录数据湖",
    });
  });
});
