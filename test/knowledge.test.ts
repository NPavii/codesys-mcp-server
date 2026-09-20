import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import type { AppConfig } from "../src/config.js";
import { isAllowedOfficialUrl, searchOfficialDocs } from "../src/knowledge/liveLookup.js";
import {
  clearPdfCache,
  getPdfDocument,
  listPdfDocuments,
  searchPdfDocuments
} from "../src/knowledge/pdfDocs.js";
import { searchCuratedDocs } from "../src/knowledge/search.js";
import { buildWritingGuidance, getCodesysTopic } from "../src/knowledge/topics.js";
import { createSimplePdf } from "./pdf-fixture.js";

const testConfig: AppConfig = {
  host: "127.0.0.1",
  port: 0,
  officialLookupEnabled: false,
  officialLookupTimeoutMs: 100,
  officialLookupCacheTtlMs: 1000,
  pdfSearchEnabled: true,
  pdfDocsDir: "docs/pdfs",
  pdfMaxTextChars: 200000
};

describe("CODESYS knowledge search", () => {
  it("returns curated results for Structured Text timer queries", () => {
    const results = searchCuratedDocs("Structured Text TON timer", 5);

    expect(results.length).toBeGreaterThan(0);
    expect(results.map((result) => result.id)).toContain("timers");
    expect(results[0]?.citations.length).toBeGreaterThan(0);
  });

  it("surfaces the official CODESYS examples source", () => {
    const results = searchCuratedDocs("official CODESYS examples sample projects", 3);

    expect(results[0]?.id).toBe("official-codesys-help-and-examples");
    expect(results[0]?.citations.map((citation) => citation.url)).toContain(
      "https://content.helpme-codesys.com/en/CODESYS%20Examples/_ex_start_page.html"
    );
  });

  it("returns topic details with citations and examples", async () => {
    const topic = await getCodesysTopic("function block", "normal", true, testConfig);

    expect(topic.topic.toLowerCase()).toContain("function block");
    expect(topic.answer).toContain("function block");
    expect(topic.citations.length).toBeGreaterThan(0);
    expect(topic.examples?.length).toBeGreaterThan(0);
  });

  it("builds safety-aware writing guidance", () => {
    const guidance = buildWritingGuidance({
      task: "Write a motor control function block with EtherCAT drive feedback",
      target: "function_block"
    });

    expect(guidance.answer).toContain("function_block");
    expect(guidance.answer).toContain("Safety note");
    expect(guidance.citations.length).toBeGreaterThan(0);
  });

  it("rejects non-allowlisted official lookup URLs", () => {
    expect(
      isAllowedOfficialUrl(
        "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_programming_in_st.html"
      )
    ).toBe(true);
    expect(isAllowedOfficialUrl("https://example.com/not-codesys")).toBe(false);
    expect(isAllowedOfficialUrl("http://content.helpme-codesys.com/insecure")).toBe(false);
  });

  it("returns no official results when live lookup is disabled", async () => {
    await expect(searchOfficialDocs("TON timer", 3, testConfig)).resolves.toEqual([]);
  });

  it("lists, searches, and returns extracted PDF text", async () => {
    const pdfDir = await mkdtemp(path.join(tmpdir(), "codesys-pdfs-"));
    const pdfPath = path.join(pdfDir, "codesys-local-manual.pdf");
    const config = {
      ...testConfig,
      pdfDocsDir: pdfDir
    };

    try {
      await writeFile(
        pdfPath,
        await createSimplePdf("CODESYS local PDF manual covers TON timers and task watchdogs.")
      );
      clearPdfCache();

      const pdfs = await listPdfDocuments(config);
      expect(pdfs).toHaveLength(1);
      expect(pdfs[0]?.fileName).toBe("codesys-local-manual.pdf");

      const results = await searchPdfDocuments("task watchdog", 3, config);
      expect(results[0]?.sourceType).toBe("pdf");
      expect(results[0]?.snippet).toContain("watchdogs");

      const pdf = await getPdfDocument("codesys-local-manual.pdf", 12000, config);
      expect(pdf?.text).toContain("TON timers");
    } finally {
      clearPdfCache();
      await rm(pdfDir, { recursive: true, force: true });
    }
  });
});
