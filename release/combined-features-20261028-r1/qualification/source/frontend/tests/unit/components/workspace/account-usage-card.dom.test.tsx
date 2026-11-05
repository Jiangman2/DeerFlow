import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { AccountUsageCard } from "@/components/workspace/settings/account-usage-card";
import { useAccountUsage } from "@/core/console/account-usage";
import { zhCN } from "@/core/i18n/locales/zh-CN";

rs.mock("@/core/console/account-usage", () => ({ useAccountUsage: rs.fn() }));
rs.mock("@/core/i18n/hooks", () => ({ useI18n: () => ({ t: zhCN, locale: "zh-CN" }) }));
const mockedUsage = rs.mocked(useAccountUsage);
afterEach(cleanup);

describe("account usage display", () => {
  it("shows a real zero and retains an explicit legacy unclassified count", () => {
    mockedUsage.mockReturnValue({ data: { total_tokens: 1500, input_tokens: 1000, output_tokens: 0, unclassified_tokens: 500 }, isError: false } as ReturnType<typeof useAccountUsage>);
    render(<AccountUsageCard />);
    expect(screen.getByText("1,500")).toBeTruthy();
    expect(screen.getByText("0")).toBeTruthy();
    expect(screen.getByText("历史未分类 Token")).toBeTruthy();
    expect(screen.getByText("500")).toBeTruthy();
  });
  it("never displays an unavailable response as zero or stale successful data", () => {
    const refetch = rs.fn();
    mockedUsage.mockReturnValue({ data: { total_tokens: 123 }, isError: true, refetch } as unknown as ReturnType<typeof useAccountUsage>);
    render(<AccountUsageCard compact />);
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("123")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新获取用量" }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
