import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PptConfirmationOptIn } from "@/components/workspace/ppt-confirmation-opt-in";

afterEach(cleanup);

test("manual starts unpressed and only an explicit interaction requests enable", () => {
  const change = rs.fn();
  render(
    <PptConfirmationOptIn
      locale="zh-CN"
      checked={false}
      disabled={false}
      hasQuotes={false}
      onCheckedChange={change}
    />,
  );
  const toggle = screen.getByRole("button", {
    name: "本次 PPT 任务：60 秒后自动确认",
  });
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  expect(toggle.getAttribute("type")).toBe("button");
  expect(screen.getByText(/默认人工确认/)).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(toggle);
  expect(change).toHaveBeenCalledWith(true);
});

test("busy composer cannot change consent", () => {
  const change = rs.fn();
  render(
    <PptConfirmationOptIn
      locale="en-US"
      checked={false}
      disabled={true}
      hasQuotes={false}
      onCheckedChange={change}
    />,
  );
  fireEvent.click(screen.getByRole("button"));
  expect(change).not.toHaveBeenCalled();
});

test("quoted context blocks enabling but permits disabling existing intent", () => {
  const change = rs.fn();
  const view = render(
    <PptConfirmationOptIn
      locale="zh-CN"
      checked={false}
      disabled={false}
      hasQuotes={true}
      onCheckedChange={change}
    />,
  );
  expect(screen.getByRole<HTMLButtonElement>("button").disabled).toBe(true);
  view.rerender(
    <PptConfirmationOptIn
      locale="zh-CN"
      checked={true}
      disabled={false}
      hasQuotes={true}
      onCheckedChange={change}
    />,
  );
  fireEvent.click(screen.getByRole("button"));
  expect(change).toHaveBeenCalledWith(false);
});
