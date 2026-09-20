import { createRequire } from "node:module";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import type { AppConfig } from "../config.js";
import type { PdfDocument, SearchResult } from "./types.js";
import { createSnippet } from "./search.js";

const require = createRequire(import.meta.url);

interface PdfTextResult {
  text: string;
}

interface PdfInfoResult {
  total?: number;
}

interface PdfParser {
  getText(): Promise<PdfTextResult>;
  getInfo(): Promise<PdfInfoResult>;
  destroy(): Promise<void>;
}

type PdfParserConstructor = new (options: { data: Buffer }) => PdfParser;

function loadPdfParser(): PdfParserConstructor {
  const packageName = "pdf-parse";
  const pdfParse = require(packageName) as { PDFParse: PdfParserConstructor };
  return pdfParse.PDFParse;
}

interface PdfCache {
  signature: string;
  docs: PdfDocument[];
}

let pdfCache: PdfCache | undefined;

function normalizePdfDir(config: AppConfig): string {
  return path.resolve(process.cwd(), config.pdfDocsDir);
}

function pdfIdFromFileName(fileName: string): string {
  return fileName
    .toLowerCase()
    .replace(/\.pdf$/i, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function titleFromFileName(fileName: string): string {
  return fileName
    .replace(/\.pdf$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

async function readPdfText(
  filePath: string,
  maxTextChars: number
): Promise<{
  text: string;
  pageCount?: number;
}> {
  const buffer = await readFile(filePath);
  const PDFParse = loadPdfParser();
  const parser = new PDFParse({ data: buffer });

  try {
    const info = await parser.getInfo().catch(() => undefined);
    const textResult = await parser.getText();

    return {
      text: textResult.text.replace(/\s+/g, " ").trim().slice(0, maxTextChars),
      pageCount: info?.total
    };
  } finally {
    await parser.destroy();
  }
}

async function buildPdfCache(config: AppConfig): Promise<PdfCache> {
  if (!config.pdfSearchEnabled) {
    return {
      signature: "disabled",
      docs: []
    };
  }

  const pdfDir = normalizePdfDir(config);
  let pdfFiles: string[];

  try {
    const entries = await readdir(pdfDir);
    pdfFiles = entries
      .filter((entry) => entry.toLowerCase().endsWith(".pdf"))
      .sort((a, b) => a.localeCompare(b));
  } catch {
    return {
      signature: `missing:${pdfDir}`,
      docs: []
    };
  }

  const stats = await Promise.all(
    pdfFiles.map(async (fileName) => ({
      fileName,
      filePath: path.join(pdfDir, fileName),
      stats: await stat(path.join(pdfDir, fileName))
    }))
  );

  const signature = stats
    .map(({ fileName, stats: fileStats }) => {
      return `${fileName}:${fileStats.size}:${fileStats.mtimeMs}`;
    })
    .join("|");

  if (pdfCache?.signature === signature) {
    return pdfCache;
  }

  const docs = (
    await Promise.all(
      stats.map(async ({ fileName, filePath, stats: fileStats }) => {
        try {
          const parsed = await readPdfText(filePath, config.pdfMaxTextChars);
          const doc: PdfDocument = {
            id: pdfIdFromFileName(fileName),
            fileName,
            title: titleFromFileName(fileName),
            text: parsed.text,
            sizeBytes: fileStats.size,
            modifiedMs: fileStats.mtimeMs
          };

          if (parsed.pageCount !== undefined) {
            doc.pageCount = parsed.pageCount;
          }

          return doc;
        } catch {
          return undefined;
        }
      })
    )
  ).filter((doc): doc is PdfDocument => Boolean(doc));

  const nextCache: PdfCache = {
    signature,
    docs
  };

  pdfCache = nextCache;
  return nextCache;
}

export function clearPdfCache(): void {
  pdfCache = undefined;
}

export async function listPdfDocuments(config: AppConfig): Promise<PdfDocument[]> {
  const cache = await buildPdfCache(config);
  return cache.docs.map((doc) => ({
    ...doc,
    text: ""
  }));
}

export async function searchPdfDocuments(
  query: string,
  limit: number,
  config: AppConfig
): Promise<SearchResult[]> {
  const cache = await buildPdfCache(config);
  const normalizedLimit = Math.min(Math.max(limit, 1), 10);

  if (!query.trim()) {
    return cache.docs.slice(0, normalizedLimit).map((doc, index) => ({
      id: doc.id,
      title: doc.title,
      snippet: `Local PDF: ${doc.fileName}${doc.pageCount ? ` (${doc.pageCount} pages)` : ""}`,
      body: doc.text.slice(0, 30000),
      score: normalizedLimit - index,
      sourceType: "pdf",
      tags: ["pdf", "local-document"],
      citations: [{ title: doc.title, url: `pdf://${doc.fileName}` }]
    }));
  }

  const tokens = query
    .toLowerCase()
    .replace(/[^a-z0-9_#-]+/g, " ")
    .split(/\s+/)
    .filter((token) => token.length > 2);

  return cache.docs
    .map((doc) => {
      const titleText = `${doc.title} ${doc.fileName}`.toLowerCase();
      const bodyText = doc.text.toLowerCase();
      const score = tokens.reduce((total, token) => {
        const titleScore = titleText.includes(token) ? 3 : 0;
        const bodyScore = bodyText.includes(token) ? 1 : 0;
        return total + titleScore + bodyScore;
      }, 0);

      return {
        doc,
        score
      };
    })
    .filter((result) => result.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, normalizedLimit)
    .map(({ doc, score }) => ({
      id: doc.id,
      title: doc.title,
      snippet: createSnippet(doc.text, query),
      body: doc.text.slice(0, 30000),
      score,
      sourceType: "pdf" as const,
      tags: ["pdf", "local-document"],
      citations: [{ title: doc.title, url: `pdf://${doc.fileName}` }]
    }));
}

export async function getPdfDocument(
  identifier: string,
  maxChars: number,
  config: AppConfig
): Promise<PdfDocument | undefined> {
  const cache = await buildPdfCache(config);
  const normalizedIdentifier = identifier.toLowerCase();
  const doc = cache.docs.find((candidate) => {
    return (
      candidate.id.toLowerCase() === normalizedIdentifier ||
      candidate.fileName.toLowerCase() === normalizedIdentifier ||
      candidate.title.toLowerCase() === normalizedIdentifier
    );
  });

  if (!doc) {
    return undefined;
  }

  return {
    ...doc,
    text: doc.text.slice(0, Math.min(Math.max(maxChars, 1000), config.pdfMaxTextChars))
  };
}
