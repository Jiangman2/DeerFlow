import { beforeEach, describe, expect, rs, test } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { deleteUploadedFile } from "@/core/uploads/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status >= 400 ? "Error" : "OK",
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("uploads api", () => {
  test("keeps Unicode, percent signs and spaces in one encoded filename", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { success: true }));
    await deleteUploadedFile("thread-1", "报告 100%#1?.txt");
    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/threads/thread-1/uploads/%E6%8A%A5%E5%91%8A%20100%25%231%3F.txt",
      { method: "DELETE" },
    );
  });

  test("encodes uploaded filenames in delete request paths", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        success: true,
        message: "Deleted report#1?.txt",
      }),
    );

    await expect(
      deleteUploadedFile("thread-1", "report#1?.txt"),
    ).resolves.toEqual({
      success: true,
      message: "Deleted report#1?.txt",
    });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/threads/thread-1/uploads/report%231%3F.txt",
      { method: "DELETE" },
    );
  });
});
