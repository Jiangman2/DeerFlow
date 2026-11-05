import { expect, rs, test } from "@rstest/core";

import {
  LEGACY_SIDECAR_METADATA_KEY,
  SIDECAR_METADATA_KEY,
} from "@/core/sidecar/thread";
import { findSidecarThreadIdsForParent } from "@/core/threads/hooks";
import type { AgentThread } from "@/core/threads/types";

function makeThread(
  threadId: string,
  metadata: Record<string, unknown> = {},
): AgentThread {
  return {
    thread_id: threadId,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    metadata,
    status: "idle",
    values: { title: threadId, messages: [] },
  } as unknown as AgentThread;
}

test("finds current and legacy sidecars attached to the deleted parent", async () => {
  const currentSidecar = makeThread("sidecar-1", {
    [SIDECAR_METADATA_KEY]: true,
    parent_thread_id: "parent-1",
  });
  const legacySidecar = makeThread("legacy-sidecar-1", {
    [LEGACY_SIDECAR_METADATA_KEY]: true,
    parent_thread_id: "parent-1",
  });
  const search = rs.fn().mockImplementation(({ metadata }) => {
    if (Reflect.get(metadata, SIDECAR_METADATA_KEY) === true) {
      return Promise.resolve([
        currentSidecar,
        makeThread("sidecar-other-parent", {
          [SIDECAR_METADATA_KEY]: true,
          parent_thread_id: "parent-2",
        }),
        makeThread("primary-1"),
      ]);
    }
    return Promise.resolve([legacySidecar, currentSidecar]);
  });

  await expect(
    findSidecarThreadIdsForParent(
      {
        threads: {
          search,
        },
      },
      "parent-1",
    ),
  ).resolves.toEqual(["sidecar-1", "legacy-sidecar-1"]);

  expect(search).toHaveBeenNthCalledWith(1, {
    metadata: {
      [SIDECAR_METADATA_KEY]: true,
      parent_thread_id: "parent-1",
    },
    limit: 100,
    offset: 0,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "metadata"],
  });
  expect(search).toHaveBeenNthCalledWith(2, {
    metadata: {
      [LEGACY_SIDECAR_METADATA_KEY]: true,
      parent_thread_id: "parent-1",
    },
    limit: 100,
    offset: 0,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "metadata"],
  });
});
