import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig } from "./config.js";
import { createCodesysMcpServer } from "./mcp/tools.js";

export async function startStdioServer(): Promise<void> {
  const config = loadConfig();
  const server = createCodesysMcpServer(config);
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
