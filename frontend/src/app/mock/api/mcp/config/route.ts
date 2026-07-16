export function GET() {
  return Response.json({
    mcp_servers: {
      "mcp-open-source-trending": {
        enabled: true,
        type: "stdio",
        command: "uvx",
        args: ["mcp-open-source-trending"],
        env: {},
        url: null,
        headers: {},
        description:
          "A MCP server that provides access to open-source project trend data",
      },
      "context-7": {
        enabled: true,
        description:
          "Get the latest documentation and code into Cursor, Claude, or other LLMs",
      },
      "feishu-importer": {
        enabled: true,
        description: "Import Feishu documents",
      },
    },
  });
}
