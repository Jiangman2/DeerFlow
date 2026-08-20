import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

describe("thread-scoped subtask state", () => {
  test("scopes stateful providers by the current dynamic thread segment", () => {
    const contextSource = readFileSync(
      resolve(process.cwd(), "src/core/tasks/context.tsx"),
      "utf8",
    );
    const chatProviderSource = readFileSync(
      resolve(
        process.cwd(),
        "src/app/workspace/chats/[thread_id]/providers.tsx",
      ),
      "utf8",
    );
    const agentLayoutSource = readFileSync(
      resolve(
        process.cwd(),
        "src/app/workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx",
      ),
      "utf8",
    );
    const threadHooksSource = readFileSync(
      resolve(process.cwd(), "src/core/threads/hooks.ts"),
      "utf8",
    );
    const chatPageSource = readFileSync(
      resolve(process.cwd(), "src/app/workspace/chats/[thread_id]/page.tsx"),
      "utf8",
    );
    const agentChatPageSource = readFileSync(
      resolve(
        process.cwd(),
        "src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
      ),
      "utf8",
    );

    expect(contextSource).toContain(
      "useLayoutEffect(() => {\n    if (previousScopeKeyRef.current === scopeKey)",
    );
    expect(contextSource).toContain(
      'previousScopeKeyRef.current === "new" &&',
    );
    expect(chatProviderSource).not.toContain("usePathname");
    expect(chatProviderSource).toContain(
      'import { useThreadChat } from "@/components/workspace/chats";',
    );
    expect(chatProviderSource).toContain(
      'const scopeKey = isNewThread ? "new" : (threadId ?? "unknown");',
    );
    expect(chatProviderSource).toContain(
      '<SubtasksProvider scopeKey={scopeKey}>',
    );
    expect(chatProviderSource).toContain(
      '<ArtifactsProvider scopeKey={scopeKey}>',
    );
    expect(chatProviderSource).not.toContain("<ArtifactsProvider key=");
    expect(agentLayoutSource).toContain(
      'import { useThreadChat } from "@/components/workspace/chats";',
    );
    expect(agentLayoutSource).toContain(
      'const scopeKey = isNewThread ? "new" : (threadId ?? "unknown");',
    );
    expect(agentLayoutSource).toContain(
      '<SubtasksProvider scopeKey={scopeKey}>',
    );
    expect(threadHooksSource).toContain(
      "liveMessagesThreadId === currentViewThreadId",
    );
    expect(threadHooksSource).toContain(
      'useThreadHistory(onStreamThreadId ?? "",',
    );
    expect(threadHooksSource).toContain(
      "threadId: onStreamThreadId,",
    );
    expect(threadHooksSource).toContain("(!isNewThread && Boolean(threadId))");
    expect(threadHooksSource).toContain(
      "!isNewThread && threadId ? history : []",
    );
    expect(chatPageSource).toContain(
      "history.replaceState(null, \"\", nextPath);",
    );
    expect(chatPageSource).toContain("setIsNewThread(false);");
    expect(chatPageSource).toContain("notifyThreadChatRouteChange(nextPath);");
    expect(chatPageSource).toContain("createdThreadIdRef.current !== threadId");
    expect(chatPageSource).toContain("!thread.isLoading");
    expect(agentChatPageSource).toContain(
      "history.replaceState(null, \"\", nextPath);",
    );
    expect(agentChatPageSource).toContain(
      "notifyThreadChatRouteChange(nextPath);",
    );
  });
});
