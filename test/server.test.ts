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
    expect(response.body.name).toBe("codesys-mcp");
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

    // v0.3.0: tools ported from Codesys-MCP-SP21-plus (bridge ops)
    expect(toolNames).toEqual(
      expect.arrayContaining([
        "codesys_list_applications",
        "codesys_set_active_application",
        "codesys_list_tasks",
        "codesys_configure_task",
        "codesys_device_info",
        "codesys_list_device_parameters",
        "codesys_set_device_parameter",
        "codesys_set_device_state",
        "codesys_device_reachable",
        "codesys_device_rebind",
        "codesys_device_user_add",
        "codesys_add_device",
        "codesys_scan_network",
        "codesys_io_mappings_csv",
        "codesys_rename_object",
        "codesys_move_object",
        "codesys_export_plcopen_xml",
        "codesys_import_plcopen_xml",
        "codesys_dump_pou_code",
        "codesys_clean_all",
        "codesys_save_project_archive",
        "codesys_save_project_as",
        "codesys_application_build_action",
        "codesys_get_compiler_version",
        "codesys_set_compiler_version_newest",
        "codesys_set_exclude_from_build",
        "codesys_signature_crc",
        "codesys_online_change_check",
        "codesys_boot_application_create",
        "codesys_download_application",
        "codesys_application_start_stop",
        "codesys_application_state",
        "codesys_application_reset",
        "codesys_plc_file_list",
        "codesys_plc_file_delete",
        "codesys_plc_file_transfer",
        "codesys_source_download",
        "codesys_source_upload",
        "codesys_symbol_config_create",
        "codesys_symbol_config_list",
        "codesys_symbol_config_set_access",
        "codesys_symbol_config_settings_get",
        "codesys_symbol_config_settings_set",
        "codesys_symbol_config_export_xsd",
        "codesys_nvl_sender_set",
        "codesys_nvl_receiver_create",
        "codesys_grant_object_access",
        "codesys_create_project",
        "codesys_list_open_projects"
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
