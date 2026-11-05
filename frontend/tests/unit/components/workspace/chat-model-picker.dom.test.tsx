import { afterEach, describe, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { ChatModelPicker } from "@/components/workspace/chat-model-picker";
import type { Model } from "@/core/models";

rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      inputBox: {
        modelPickerTitle: "Choose a model",
        searchModels: "Search models",
        currentModel: "Current model",
        noMatchingModels: "No matching models",
      },
    },
  }),
}));

const models: Model[] = [
  {
    id: "one",
    name: "deployment-one",
    model: "provider/one",
    display_name: "DeepSeek",
    description: "Internal description",
  },
  {
    id: "two",
    name: "deployment-two",
    model: "provider/two",
    display_name: "GLM",
    description: "Another long description",
  },
];

function setup() {
  const onSelect = rs.fn();
  const onOpenChange = rs.fn();
  render(
    <ChatModelPicker
      models={models}
      selectedName="deployment-one"
      open
      onSelect={onSelect}
      onOpenChange={onOpenChange}
    >
      <button>Models</button>
    </ChatModelPicker>,
  );
  return { onSelect, onOpenChange };
}

afterEach(cleanup);

describe("Compact model menu", () => {
  it("filters by configured identifier and selects the configured name, not the display label", async () => {
    const { onSelect, onOpenChange } = setup();
    const input = screen.getByRole("textbox", { name: "Search models" });
    fireEvent.change(input, { target: { value: "provider/two" } });
    const item = await screen.findByRole("menuitemradio", { name: "GLM" });
    expect(
      screen.queryByRole("menuitemradio", { name: "DeepSeek" }),
    ).toBeNull();
    expect(screen.queryByText("Another long description")).toBeNull();
    fireEvent.click(item);
    expect(onSelect).toHaveBeenCalledWith("deployment-two");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("moves keyboard focus from search to the filtered option and selects with Enter", async () => {
    const { onSelect } = setup();
    const input = screen.getByRole("textbox", { name: "Search models" });
    fireEvent.change(input, { target: { value: "glm" } });
    input.focus();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const item = screen.getByRole("menuitemradio", { name: "GLM" });
    await waitFor(() => expect(document.activeElement).toBe(item));
    fireEvent.keyDown(item, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("deployment-two");
  });

  it("keeps an empty search safe and dismisses with Escape without choosing a model", async () => {
    const { onSelect, onOpenChange } = setup();
    const input = screen.getByRole("textbox", { name: "Search models" });
    fireEvent.change(input, { target: { value: "missing-model" } });
    expect(screen.getByRole("status").textContent).toBe("No matching models");
    expect(screen.queryAllByRole("menuitemradio")).toHaveLength(0);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
