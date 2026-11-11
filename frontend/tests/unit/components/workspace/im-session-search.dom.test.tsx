import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { SidebarProvider } from "@/components/ui/sidebar";
import { ImSessionSearch } from "@/components/workspace/im-session-search";
import { imSessionCopy } from "@/core/i18n/im-session-tree";
import { zhCN } from "@/core/i18n/locales/zh-CN";
import { useImThreadSearch } from "@/core/im/hooks";
import type { ImSearchResponse } from "@/core/im/types";

rs.mock("@/core/im/hooks", () => ({
  useImThreadSearch: rs.fn(),
}));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ t: zhCN, locale: "zh-CN" }),
}));

const copy = imSessionCopy("zh-CN");

const mockedSearch = rs.mocked(useImThreadSearch);

function renderSearch() {
  return render(
    <SidebarProvider>
      <ImSessionSearch />
    </SidebarProvider>,
  );
}

function result(overrides: Partial<ImSearchResponse["results"][number]> = {}) {
  return {
    thread_id: "x1",
    title: "昨天的方案",
    channel_type: "group" as const,
    chat_id: "oc_group_x",
    chat_name: "研发一群",
    last_active_at: "2026-01-02T00:00:00Z",
    is_current: false,
    status: "active" as const,
    ...overrides,
  };
}

function searchData(
  results: ImSearchResponse["results"],
  limit = 20,
): ImSearchResponse {
  return { results, limit, offset: 0 };
}

afterEach(cleanup);

beforeEach(() => {
  mockedSearch.mockReturnValue({
    data: undefined,
    isFetching: false,
  } as unknown as ReturnType<typeof useImThreadSearch>);
});

describe("IM session search", () => {
  it("debounces typing before querying the backend", async () => {
    renderSearch();
    fireEvent.change(screen.getByTestId("im-session-search-input"), {
      target: { value: "  研发  " },
    });
    expect(mockedSearch).toHaveBeenCalledWith("");
    await waitFor(() => expect(mockedSearch).toHaveBeenCalledWith("研发"));
  });

  it("renders matched threads with a group or thread badge", async () => {
    mockedSearch.mockImplementation((query: string) => {
      if (!query) {
        return {
          data: undefined,
          isFetching: false,
        } as unknown as ReturnType<typeof useImThreadSearch>;
      }
      return {
        data: searchData([
          result(),
          result({
            thread_id: "p1",
            title: "研发私聊",
            channel_type: "private",
            chat_name: "飞书私聊",
          }),
        ]),
        isFetching: false,
      } as unknown as ReturnType<typeof useImThreadSearch>;
    });

    renderSearch();
    fireEvent.change(screen.getByTestId("im-session-search-input"), {
      target: { value: "研发" },
    });

    expect(await screen.findByText("昨天的方案")).toBeTruthy();
    expect(screen.getByText("研发私聊")).toBeTruthy();
    expect(screen.getByText(copy.imMatchGroup)).toBeTruthy();
    expect(screen.getByText(copy.imMatchThread)).toBeTruthy();
  });

  it("shows the empty state when nothing matches", async () => {
    mockedSearch.mockImplementation(
      () =>
        ({
          data: searchData([]),
          isFetching: false,
        }) as unknown as ReturnType<typeof useImThreadSearch>,
    );

    renderSearch();
    fireEvent.change(screen.getByTestId("im-session-search-input"), {
      target: { value: "不存在" },
    });

    expect(await screen.findByText(copy.imSearchNoResult)).toBeTruthy();
  });

  it("states that results were truncated at the page limit", async () => {
    mockedSearch.mockImplementation(
      () =>
        ({
          data: searchData([result()], 1),
          isFetching: false,
        }) as unknown as ReturnType<typeof useImThreadSearch>,
    );

    renderSearch();
    fireEvent.change(screen.getByTestId("im-session-search-input"), {
      target: { value: "方案" },
    });

    expect(await screen.findByText(copy.imSearchTruncated)).toBeTruthy();
  });
});
