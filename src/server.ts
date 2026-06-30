import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadConfig, type AppConfig } from "./config.js";
import { SERVER_NAME, SERVER_VERSION } from "./constants.js";
import { registerTools } from "./tools.js";

export function createCapillusMcpServer(config: AppConfig = loadConfig()): McpServer {
  const server = new McpServer({ name: SERVER_NAME, version: SERVER_VERSION });
  registerTools(server, config);
  return server;
}

export async function runStdioServer(config: AppConfig = loadConfig()): Promise<void> {
  const server = createCapillusMcpServer(config);
  await server.connect(new StdioServerTransport());
}
