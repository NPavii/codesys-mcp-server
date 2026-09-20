import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { AppConfig } from "../config.js";
import type { SourceMode } from "../knowledge/types.js";
import { getPdfDocument, listPdfDocuments, searchPdfDocuments } from "../knowledge/pdfDocs.js";
import { buildWritingGuidance, getCodesysTopic, searchCodesysDocs } from "../knowledge/topics.js";
import { crawlHelpSite, getCrawlStatus } from "../knowledge/helpSiteCrawler.js";
import { fetchPageByUrl, isAllowedOfficialUrl } from "../knowledge/liveLookup.js";
import { registerProjectTools } from "./projectTools.js";
import { registerBridgeTools } from "./bridgeTools.js";

const sourceModeSchema = z.enum(["curated", "official", "pdf", "crawled", "both", "all"]);

function asTextResult(value: Record<string, unknown>) {
  return {
    content: [
      {
        type: "text" as const,
        text: JSON.stringify(value, null, 2)
      }
    ],
    structuredContent: value
  };
}

export function createCodesysMcpServer(config: AppConfig): McpServer {
  const server = new McpServer({
    name: "codesys-mcp",
    version: "0.2.0"
  });

  server.registerTool(
    "search_codesys_docs",
    {
      title: "Search CODESYS Docs",
      description:
        "Search curated CODESYS guidance, allowlisted official CODESYS pages, and optional PDFs in the docs/pdfs folder. Use this first for concepts, syntax, library topics, workflows, and best practices.",
      inputSchema: {
        query: z
          .string()
          .min(1)
          .describe(
            "The CODESYS topic, syntax, library block, workflow, or design question to search for."
          ),
        limit: z
          .number()
          .int()
          .min(1)
          .max(10)
          .optional()
          .describe("Maximum number of results to return. Defaults to 5."),
        sourceMode: sourceModeSchema
          .optional()
          .describe(
            "Use curated, official, pdf, crawled (full helpme-codesys.com index), both (curated + official), or all sources. Defaults to all."
          )
      }
    },
    async ({ query, limit, sourceMode }) => {
      const results = await searchCodesysDocs(
        query,
        limit ?? 5,
        (sourceMode ?? "all") as SourceMode,
        config
      );

      return asTextResult({
        query,
        sourceMode: sourceMode ?? "all",
        results,
        note: "Official CODESYS pages are summarized and cited. PDF results come from local files in the configured PDF folder. Crawled results come from the indexed help site. Verify generated CODESYS code in the target project and runtime."
      });
    }
  );

  server.registerTool(
    "list_codesys_pdfs",
    {
      title: "List CODESYS PDFs",
      description:
        "List PDF files available to this MCP server from the configured docs/pdfs folder.",
      inputSchema: {}
    },
    async () => {
      const pdfs = await listPdfDocuments(config);
      return asTextResult({
        pdfFolder: config.pdfDocsDir,
        count: pdfs.length,
        pdfs,
        note: "Drop PDF files into the configured folder and restart or call again; the server refreshes when file metadata changes."
      });
    }
  );

  server.registerTool(
    "search_codesys_pdfs",
    {
      title: "Search CODESYS PDFs",
      description: "Search text extracted from PDF files in the configured docs/pdfs folder.",
      inputSchema: {
        query: z.string().min(1).describe("Search text for the local CODESYS PDFs."),
        limit: z
          .number()
          .int()
          .min(1)
          .max(10)
          .optional()
          .describe("Maximum number of PDF matches to return. Defaults to 5.")
      }
    },
    async ({ query, limit }) => {
      const results = await searchPdfDocuments(query, limit ?? 5, config);
      return asTextResult({
        query,
        pdfFolder: config.pdfDocsDir,
        results
      });
    }
  );

  server.registerTool(
    "get_codesys_pdf",
    {
      title: "Get CODESYS PDF Text",
      description:
        "Return extracted text from one PDF in the configured docs/pdfs folder. Use list_codesys_pdfs first if you do not know the file name.",
      inputSchema: {
        identifier: z
          .string()
          .min(1)
          .describe("PDF id, file name, or title returned by list_codesys_pdfs."),
        maxChars: z
          .number()
          .int()
          .min(1000)
          .max(50000)
          .optional()
          .describe("Maximum extracted characters to return. Defaults to 12000.")
      }
    },
    async ({ identifier, maxChars }) => {
      const pdf = await getPdfDocument(identifier, maxChars ?? 12000, config);
      if (!pdf) {
        return asTextResult({
          identifier,
          found: false,
          pdfFolder: config.pdfDocsDir,
          note: "No matching PDF was found. Use list_codesys_pdfs to see available files."
        });
      }

      return asTextResult({
        found: true,
        pdf,
        note: "Extracted PDF text may omit diagrams, tables, screenshots, and formatting. Verify important details against the original PDF."
      });
    }
  );

  server.registerTool(
    "get_codesys_topic",
    {
      title: "Get CODESYS Topic",
      description:
        "Return a focused explanation for a known CODESYS topic such as POUs, function blocks, timers, tasks, variables, libraries, debugging, visualization, or Structured Text.",
      inputSchema: {
        topic: z.string().min(1).describe("The CODESYS topic to explain."),
        detail: z
          .enum(["brief", "normal", "deep"])
          .optional()
          .describe("Level of detail to return. Defaults to normal."),
        includeExamples: z
          .boolean()
          .optional()
          .describe("Whether to include extracted Structured Text examples when available.")
      }
    },
    async ({ topic, detail, includeExamples }) => {
      const result = await getCodesysTopic(
        topic,
        detail ?? "normal",
        includeExamples ?? true,
        config
      );

      return asTextResult({
        ...result,
        note: "Use citations for factual guidance, and verify library/device-specific behavior in CODESYS."
      });
    }
  );

  server.registerTool(
    "codesys_writing_guidance",
    {
      title: "CODESYS Writing Guidance",
      description:
        "Provide practical Structured Text and CODESYS design guidance for writing or improving code, choosing a POU structure, or following CODESYS style.",
      inputSchema: {
        task: z
          .string()
          .min(1)
          .describe("The CODESYS coding or design task the user wants help with."),
        target: z
          .enum(["program", "function_block", "function", "method", "library"])
          .optional()
          .describe("The intended CODESYS object type. Defaults to function_block."),
        language: z
          .string()
          .optional()
          .describe("The implementation language. Defaults to Structured Text."),
        constraints: z
          .string()
          .optional()
          .describe(
            "Device, library, naming, safety, or project constraints that must be preserved."
          )
      }
    },
    async ({ task, target, language, constraints }) => {
      const result = buildWritingGuidance({
        task,
        target,
        language,
        constraints
      });

      return asTextResult({
        ...result,
        note: "Generated Structured Text should be treated as a starting point and compiled/tested in the target CODESYS project."
      });
    }
  );

  server.registerTool(
    "get_codesys_page",
    {
      title: "Get CODESYS Page",
      description:
        "Fetch the full text content of any CODESYS documentation URL (content.helpme-codesys.com, codesys.com). " +
        "Use this to read the complete content of a page returned as a citation in search results or get_codesys_topic. " +
        "Returns up to 60,000 characters of extracted text.",
      inputSchema: {
        url: z
          .string()
          .url()
          .describe(
            "A CODESYS documentation URL to fetch, e.g. from a citation returned by search_codesys_docs or get_codesys_topic."
          )
      }
    },
    async ({ url }) => {
      if (!isAllowedOfficialUrl(url)) {
        return asTextResult({
          url,
          found: false,
          note: "URL is not on an allowed CODESYS domain (content.helpme-codesys.com, codesys.com). Only official CODESYS pages can be fetched."
        });
      }

      const page = await fetchPageByUrl(url, config);
      if (!page) {
        return asTextResult({
          url,
          found: false,
          note: "Could not fetch page. The URL may be unavailable or the request timed out."
        });
      }

      return asTextResult({
        url,
        found: true,
        title: page.title,
        text: page.text,
        characterCount: page.text.length,
        note: "Full extracted text from the page. Tables, diagrams, and code syntax highlighting are stripped — check the original URL for visual formatting."
      });
    }
  );

  server.registerTool(
    "crawl_codesys_help",
    {
      title: "Crawl CODESYS Help Site",
      description:
        "Crawl https://content.helpme-codesys.com/en/ and index all pages so they become searchable via search_codesys_docs (sourceMode: crawled or all). " +
        "Call this once to build the index, then call again to extend it. " +
        "Check status first with statusOnly: true before crawling. " +
        "Crawling is rate-limited and can be called multiple times to progressively index the full site.",
      inputSchema: {
        maxPages: z
          .number()
          .int()
          .min(1)
          .max(2000)
          .optional()
          .describe(
            "Maximum pages to crawl in this call. Defaults to 300. Call again to continue."
          ),
        statusOnly: z
          .boolean()
          .optional()
          .describe("If true, return crawl status without crawling. Defaults to false.")
      }
    },
    async ({ maxPages, statusOnly }) => {
      if (statusOnly) {
        const status = await getCrawlStatus();
        return asTextResult({
          ...status,
          note:
            status.indexed === 0
              ? "No pages indexed yet. Call crawl_codesys_help without statusOnly to start crawling."
              : `${status.indexed} pages indexed, ${status.pending} pending. Call again to continue.`
        });
      }

      const progress = await crawlHelpSite(maxPages ?? 300);
      return asTextResult({
        ...progress,
        note:
          progress.pending > 0
            ? `Crawl batch complete. ${progress.pending} pages still pending — call crawl_codesys_help again to continue indexing. Use search_codesys_docs with sourceMode "crawled" or "all" to search indexed content.`
            : `Crawl complete. ${progress.total} pages indexed. Use search_codesys_docs with sourceMode "crawled" or "all" to search.`
      });
    }
  );

  registerProjectTools(server);
  registerBridgeTools(server);

  return server;
}
