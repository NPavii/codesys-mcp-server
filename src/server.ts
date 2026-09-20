import { randomUUID } from "node:crypto";
import express, { type Request, type Response } from "express";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import type { AppConfig } from "./config.js";
import { createCodesysMcpServer } from "./mcp/tools.js";

type TransportMap = Record<string, StreamableHTTPServerTransport>;

function sendMcpError(res: Response, status: number, message: string): void {
  res.status(status).json({
    jsonrpc: "2.0",
    error: {
      code: -32000,
      message
    },
    id: null
  });
}

export function createApp(config: AppConfig) {
  const app = express();
  const transports: TransportMap = {};

  app.disable("x-powered-by");
  app.use(express.json({ limit: "1mb" }));
  app.use((req, res, next) => {
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization, mcp-session-id");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");

    if (req.method === "OPTIONS") {
      res.status(204).end();
      return;
    }

    next();
  });

  app.get("/healthz", (_req, res) => {
    res.json({
      ok: true,
      name: "codesys-docs",
      officialLookupEnabled: config.officialLookupEnabled
    });
  });

  app.post("/mcp", async (req: Request, res: Response) => {
    const sessionId = req.header("mcp-session-id");
    let transport: StreamableHTTPServerTransport | undefined;

    try {
      if (sessionId && transports[sessionId]) {
        transport = transports[sessionId];
      } else if (!sessionId && isInitializeRequest(req.body)) {
        transport = new StreamableHTTPServerTransport({
          enableJsonResponse: true,
          sessionIdGenerator: () => randomUUID(),
          onsessioninitialized: (newSessionId) => {
            if (transport) {
              transports[newSessionId] = transport;
            }
          }
        });

        transport.onclose = () => {
          const closedSessionId = transport?.sessionId;
          if (closedSessionId) {
            delete transports[closedSessionId];
          }
        };

        const mcpServer = createCodesysMcpServer(config);
        await mcpServer.connect(transport);
      } else {
        sendMcpError(res, 400, "Bad Request: missing initialize request or valid mcp-session-id.");
        return;
      }

      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      if (!res.headersSent) {
        sendMcpError(
          res,
          500,
          error instanceof Error ? error.message : "Internal MCP server error."
        );
      }
    }
  });

  app.get("/mcp", async (req: Request, res: Response) => {
    const sessionId = req.header("mcp-session-id");
    if (!sessionId || !transports[sessionId]) {
      sendMcpError(res, 400, "Bad Request: invalid or missing mcp-session-id.");
      return;
    }

    await transports[sessionId].handleRequest(req, res);
  });

  app.delete("/mcp", async (req: Request, res: Response) => {
    const sessionId = req.header("mcp-session-id");
    if (!sessionId || !transports[sessionId]) {
      sendMcpError(res, 400, "Bad Request: invalid or missing mcp-session-id.");
      return;
    }

    await transports[sessionId].handleRequest(req, res);
  });

  return app;
}
