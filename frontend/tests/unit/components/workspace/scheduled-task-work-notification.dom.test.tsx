import { afterEach, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import { ScheduledTaskWorkNotification } from "@/components/workspace/scheduled-task-work-notification";
import { I18nProvider } from "@/core/i18n/context";

afterEach(cleanup);

function renderField(
  props: Partial<ComponentProps<typeof ScheduledTaskWorkNotification>> = {},
) {
  const onChange = rs.fn();
  const refresh = rs.fn();
  render(
    <I18nProvider initialLocale="en-US">
      <ScheduledTaskWorkNotification
        id="work-target"
        value={null}
        targets={{ enabled: true, targets: [] }}
        loading={false}
        failed={false}
        refresh={refresh}
        onChange={onChange}
        {...props}
      />
    </I18nProvider>,
  );
  return { onChange, refresh };
}

test("two available chats require an explicit choice", () => {
  const { onChange } = renderField({
    targets: {
      enabled: true,
      targets: [
        { connection_id: "connection-a", label: "Work chat" },
        { connection_id: "connection-b", label: "Work chat" },
      ],
    },
  });
  const select = screen.getByRole<HTMLSelectElement>("combobox", {
    name: "Haineng Work notification",
  });
  expect(select.value).toBe("");
  expect(screen.getByRole("option", { name: "Work chat (1)" })).toBeDefined();
  expect(screen.getByRole("option", { name: "Work chat (2)" })).toBeDefined();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: "connection-b" } });
  expect(onChange).toHaveBeenCalledWith("connection-b");
});

test("missing chat shows the private-message setup and supports refreshing", () => {
  const { refresh } = renderField();
  expect(screen.getByRole("status").textContent).toContain(
    "send a message in a private bot chat",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Refresh private chats" }),
  );
  expect(refresh).toHaveBeenCalledOnce();
});

test("a saved unavailable chat can be turned off without revealing its ID as a label", () => {
  const { onChange } = renderField({
    value: "old-connection-id",
    targets: { enabled: false, targets: [] },
  });
  expect(screen.queryByText("old-connection-id")).toBeNull();
  const select = screen.getByRole<HTMLSelectElement>("combobox");
  expect(select.value).toBe("old-connection-id");
  fireEvent.change(select, { target: { value: "" } });
  expect(onChange).toHaveBeenCalledWith(null);
});

test("disabled Work notifications leave an unconfigured form unchanged", () => {
  renderField({ targets: { enabled: false, targets: [] } });
  expect(screen.queryByRole("combobox")).toBeNull();
});
