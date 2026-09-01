import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { searchAllWeKnoraKnowledge } from "@/core/weknora/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function documents(offset: number, count: number) {
  return Array.from({ length: count }, (_, index) => ({
    id: `file-${offset + index}`,
    knowledge_base_id: `kb-${(offset + index) % 2}`,
    title: `Document ${offset + index}`,
  }));
}

function requestURL(input: RequestInfo | URL): string {
  if (typeof input === "string") {
    return input;
  }
  return input instanceof URL ? input.href : input.url;
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("searchAllWeKnoraKnowledge", () => {
  test("loads every known result page instead of truncating the scope picker at 100 files", async () => {
    mockedFetch.mockImplementation(async (input) => {
      const url = new URL(requestURL(input), "http://localhost");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const count = offset < 200 ? 100 : 30;
      return jsonResponse({
        items: documents(offset, count),
        total: 230,
        has_more: offset + count < 230,
      });
    });

    const result = await searchAllWeKnoraKnowledge();

    expect(result.items).toHaveLength(230);
    expect(result.has_more).toBe(false);
    expect(
      mockedFetch.mock.calls.map(([input]) =>
        new URL(requestURL(input), "http://localhost").searchParams.get(
          "offset",
        ),
      ),
    ).toEqual(["0", "100", "200"]);
  });

  test("continues sequentially when the upstream omits a reliable total", async () => {
    mockedFetch.mockImplementation(async (input) => {
      const url = new URL(requestURL(input), "http://localhost");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      return offset === 0
        ? jsonResponse({
            items: documents(0, 100),
            has_more: true,
          })
        : jsonResponse({
            items: documents(100, 3),
            has_more: false,
          });
    });

    const result = await searchAllWeKnoraKnowledge("", 500);

    expect(result.items).toHaveLength(103);
    expect(result.has_more).toBe(false);
    expect(mockedFetch).toHaveBeenCalledTimes(2);
  });
});
