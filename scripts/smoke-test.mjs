import { createApp } from "../dist/src/server.js";
import { loadConfig } from "../dist/src/config.js";

const config = loadConfig();
const app = createApp(config);

function listen(appInstance) {
  return new Promise((resolve, reject) => {
    const server = appInstance.listen(0, "127.0.0.1", () => resolve(server));
    server.on("error", reject);
  });
}

async function postMcp(baseUrl, sessionId, body) {
  const headers = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json"
  };
  if (sessionId) {
    headers["mcp-session-id"] = sessionId;
  }

  const response = await fetch(`${baseUrl}/mcp?token=${encodeURIComponent(config.authToken ?? "")}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body)
  });

  const text = await response.text();
  let json;
  try {
    json = text ? JSON.parse(text) : undefined;
  } catch {
    json = { raw: text };
  }

  return {
    status: response.status,
    sessionId: response.headers.get("mcp-session-id") ?? sessionId,
    json
  };
}

async function callTool(baseUrl, sessionId, id, name, args = {}) {
  return postMcp(baseUrl, sessionId, {
    jsonrpc: "2.0",
    id,
    method: "tools/call",
    params: {
      name,
      arguments: args
    }
  });
}

function parseToolContent(response) {
  const text = response.json?.result?.content?.[0]?.text;
  if (!text) {
    return undefined;
  }

  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

const server = await listen(app);

try {
  const address = server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;

  const healthResponse = await fetch(`${baseUrl}/healthz`);
  const health = await healthResponse.json();

  const init = await postMcp(baseUrl, undefined, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: {
        name: "codesys-smoke-test",
        version: "0.1.0"
      }
    }
  });

  const sessionId = init.sessionId;

  await postMcp(baseUrl, sessionId, {
    jsonrpc: "2.0",
    method: "notifications/initialized",
    params: {}
  });

  const tools = await postMcp(baseUrl, sessionId, {
    jsonrpc: "2.0",
    id: 2,
    method: "tools/list",
    params: {}
  });

  const listPdfs = await callTool(baseUrl, sessionId, 3, "list_codesys_pdfs");
  const pdfList = parseToolContent(listPdfs);

  const searchPdfs = await callTool(baseUrl, sessionId, 4, "search_codesys_pdfs", {
    query: "IEC 61131 Structured Text function block",
    limit: 3
  });
  const pdfSearch = parseToolContent(searchPdfs);

  const enhance = await callTool(baseUrl, sessionId, 5, "enhance_codesys_search_query", {
    query: "How do I write a CODESYS function block with a timer?"
  });
  const enhancement = parseToolContent(enhance);

  const searchDocs = await callTool(baseUrl, sessionId, 6, "search_codesys_docs", {
    query: "IEC 61131 Structured Text function block",
    limit: 5,
    sourceMode: "pdf"
  });
  const docSearch = parseToolContent(searchDocs);

  const summary = {
    health,
    initializeStatus: init.status,
    toolsListStatus: tools.status,
    toolNames: tools.json?.result?.tools?.map((tool) => tool.name) ?? [],
    pdfCount: pdfList?.count ?? 0,
    pdfFiles: pdfList?.pdfs?.map((pdf) => pdf.fileName) ?? [],
    pdfSearchStatus: searchPdfs.status,
    pdfSearchResultCount: pdfSearch?.results?.length ?? 0,
    firstPdfSearchTitle: pdfSearch?.results?.[0]?.title,
    openRouterConfigured: Boolean(config.openRouterApiKey),
    openRouterEnabled: config.openRouterEnabled,
    openRouterModel: config.openRouterModel,
    enhancementStatus: enhance.status,
    enhancementProvider: enhancement?.provider,
    enhancementEnabled: enhancement?.enabled,
    enhancementError: enhancement?.error,
    docsSearchStatus: searchDocs.status,
    docsSearchResultCount: docSearch?.results?.length ?? 0,
    docsSearchSourceTypes: [
      ...new Set((docSearch?.results ?? []).map((result) => result.sourceType))
    ],
    firstDocsSearchTitle: docSearch?.results?.[0]?.title
  };

  console.log(JSON.stringify(summary, null, 2));
} finally {
  server.close();
}
