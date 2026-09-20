import request from "supertest";
import { describe, expect, it } from "vitest";
import type { AppConfig } from "../src/config.js";
import { createApp } from "../src/server.js";

const config: AppConfig = {
  host: "127.0.0.1",
  port: 0,
  officialLookupEnabled: false,
  officialLookupTimeoutMs: 100,
  officialLookupCacheTtlMs: 1000,
  pdfSearchEnabled: true,
  pdfDocsDir: "docs/pdfs",
  pdfMaxTextChars: 200000
};

describe("HTTP server", () => {
  it("serves health without auth", async () => {
    const app = createApp(config);
    const response = await request(app).get("/healthz").expect(200);

    expect(response.body.ok).toBe(true);
    expect(response.body.name).toBe("codesys-docs");
  });

  it("allows MCP initialize and tools/list", async () => {
    const app = createApp(config);

    const initializeResponse = await request(app)
      .post("/mcp")
      .set("Accept", "application/json, text/event-stream")
      .send({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: {
          protocolVersion: "2025-06-18",
          capabilities: {},
          clientInfo: {
            name: "test-client",
            version: "0.0.0"
          }
        }
      })
      .expect(200);

    const sessionId = initializeResponse.header["mcp-session-id"];
    expect(sessionId).toBeTruthy();

    await request(app)
      .post("/mcp")
      .set("Accept", "application/json, text/event-stream")
      .set("mcp-session-id", sessionId)
      .send({
        jsonrpc: "2.0",
        method: "notifications/initialized",
        params: {}
      })
      .expect((response) => {
        expect([200, 202]).toContain(response.status);
      });

    const toolsResponse = await request(app)
      .post("/mcp")
      .set("Accept", "application/json, text/event-stream")
      .set("mcp-session-id", sessionId)
      .send({
        jsonrpc: "2.0",
        id: 2,
        method: "tools/list",
        params: {}
      })
      .expect(200);

    const toolNames = toolsResponse.body.result.tools.map((tool: { name: string }) => tool.name);
    expect(toolNames).toEqual(
      expect.arrayContaining([
        "search_codesys_docs",
        "get_codesys_topic",
        "codesys_writing_guidance",
        "list_codesys_pdfs",
        "search_codesys_pdfs",
        "get_codesys_pdf",
        "get_codesys_page",
        "crawl_codesys_help"
      ])
    );

    const toolCallResponse = await request(app)
      .post("/mcp")
      .set("Accept", "application/json, text/event-stream")
      .set("mcp-session-id", sessionId)
      .send({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: {
          name: "search_codesys_docs",
          arguments: {
            query: "TON timer Structured Text",
            limit: 2,
            sourceMode: "curated"
          }
        }
      })
      .expect(200);

    expect(toolCallResponse.body.result.content[0].text).toContain("TON timer");
  });
});
