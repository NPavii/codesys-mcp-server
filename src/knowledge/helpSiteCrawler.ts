import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as cheerio from "cheerio";
import MiniSearch from "minisearch";
import type { SearchResult } from "./types.js";
import { createSnippet } from "./search.js";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SEED_URL = "https://content.helpme-codesys.com/en/index.html";
const ALLOWED_PREFIX = "https://content.helpme-codesys.com/en/";
const DATA_DIR = new URL("../../../data", import.meta.url).pathname;
const INDEX_FILE = path.join(DATA_DIR, "helpsite-index.json");
const CRAWL_DELAY_MS = 150;
const BATCH_SIZE = 5;
const MAX_TEXT_CHARS = 15_000;
const INDEX_VERSION = 1;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CrawledPage {
  id: string;
  url: string;
  title: string;
  text: string;
  crawledAt: number;
}

export interface CrawlIndex {
  version: number;
  crawledAt: number;
  pageCount: number;
  pendingUrls: string[];
  pages: CrawledPage[];
}

export interface CrawlProgress {
  crawled: number;
  total: number;
  pending: number;
  errors: number;
}

export interface CrawlStatus {
  indexed: number;
  pending: number;
  lastCrawledAt: number;
  inProgress: boolean;
}

// ---------------------------------------------------------------------------
// In-memory state
// ---------------------------------------------------------------------------

let cachedIndex: CrawlIndex | null = null;
let miniSearch: MiniSearch<CrawledPage> | null = null;
let crawlInProgress = false;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function urlToId(url: string): string {
  return url
    .replace(/^https?:\/\//, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .toLowerCase()
    .slice(0, 120);
}

function normalizeUrl(base: string, href: string): string | null {
  try {
    const url = new URL(href, base);
    url.hash = "";
    if (!url.href.startsWith(ALLOWED_PREFIX)) return null;
    // Skip non-HTML resources
    const ext = url.pathname.split(".").pop()?.toLowerCase();
    if (ext && !["html", "htm", ""].includes(ext)) return null;
    return url.href;
  } catch {
    return null;
  }
}

async function fetchPage(url: string): Promise<{
  title: string;
  text: string;
  links: string[];
} | null> {
  try {
    const response = await fetch(url, {
      signal: AbortSignal.timeout(8000),
      headers: {
        "user-agent":
          "codesys-mcp-server/0.1 (documentation indexer; +https://modelcontextprotocol.io)"
      }
    });

    if (!response.ok) return null;

    const html = await response.text();
    const $ = cheerio.load(html);

    // Collect links before stripping elements
    const links: string[] = [];
    $("a[href]").each((_, el) => {
      const href = $(el).attr("href");
      if (href) {
        const normalized = normalizeUrl(url, href);
        if (normalized) links.push(normalized);
      }
    });

    $("script, style, nav, footer, header, noscript, svg, img, .breadcrumb, .toc").remove();

    const title = $("h1").first().text().trim() || $("title").first().text().trim() || url;

    const text = ($("main, article, .content, body").first().text() || $("body").text())
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, MAX_TEXT_CHARS);

    return { title, text, links };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

async function loadIndex(): Promise<CrawlIndex> {
  try {
    await fs.mkdir(DATA_DIR, { recursive: true });
    const raw = await fs.readFile(INDEX_FILE, "utf-8");
    const parsed = JSON.parse(raw) as CrawlIndex;
    if (parsed.version !== INDEX_VERSION) {
      throw new Error("version mismatch");
    }
    return parsed;
  } catch {
    return {
      version: INDEX_VERSION,
      crawledAt: 0,
      pageCount: 0,
      pendingUrls: [SEED_URL],
      pages: []
    };
  }
}

async function saveIndex(index: CrawlIndex): Promise<void> {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(INDEX_FILE, JSON.stringify(index), "utf-8");
}

// ---------------------------------------------------------------------------
// MiniSearch index
// ---------------------------------------------------------------------------

function buildSearchIndex(pages: CrawledPage[]): MiniSearch<CrawledPage> {
  const ms = new MiniSearch<CrawledPage>({
    idField: "id",
    fields: ["title", "text"],
    storeFields: ["url", "title", "text"],
    searchOptions: {
      boost: { title: 4 },
      prefix: true,
      fuzzy: 0.15
    }
  });
  ms.addAll(pages);
  return ms;
}

async function ensureSearchIndex(): Promise<void> {
  if (miniSearch) return;
  const index = await loadIndex();
  cachedIndex = index;
  if (index.pages.length > 0) {
    miniSearch = buildSearchIndex(index.pages);
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export async function getCrawlStatus(): Promise<CrawlStatus> {
  const index = cachedIndex ?? (await loadIndex());
  return {
    indexed: index.pages.length,
    pending: index.pendingUrls.length,
    lastCrawledAt: index.crawledAt,
    inProgress: crawlInProgress
  };
}

/**
 * Crawl up to `maxPages` pages from the CODESYS help site, appending to any
 * previously-persisted index.  Safe to call multiple times; resumes from where
 * it left off.
 */
export async function crawlHelpSite(maxPages = 300): Promise<CrawlProgress> {
  if (crawlInProgress) {
    const status = await getCrawlStatus();
    return { crawled: 0, total: status.indexed, pending: status.pending, errors: 0 };
  }

  crawlInProgress = true;
  const index = await loadIndex();

  // Ensure the seed is queued on a fresh index
  if (index.pages.length === 0 && index.pendingUrls.length === 0) {
    index.pendingUrls.push(SEED_URL);
  }

  const visited = new Set(index.pages.map((p) => p.url));
  let crawled = 0;
  let errors = 0;

  try {
    while (index.pendingUrls.length > 0 && crawled < maxPages) {
      const batch = index.pendingUrls.splice(0, BATCH_SIZE);

      const results = await Promise.all(
        batch.map(async (url) => ({ url, data: await fetchPage(url) }))
      );

      for (const { url, data } of results) {
        visited.add(url);

        if (!data) {
          errors++;
          continue;
        }

        // Skip pages with no useful content
        if (data.text.length < 50) continue;

        const id = urlToId(url);
        // Avoid duplicates (id collision on very long URLs)
        if (index.pages.some((p) => p.id === id)) continue;

        index.pages.push({ id, url, title: data.title, text: data.text, crawledAt: Date.now() });
        crawled++;

        for (const link of data.links) {
          if (!visited.has(link) && !index.pendingUrls.includes(link)) {
            index.pendingUrls.push(link);
          }
        }
      }

      index.crawledAt = Date.now();
      index.pageCount = index.pages.length;

      // Persist every 25 pages so progress survives a crash
      if (crawled > 0 && crawled % 25 === 0) {
        await saveIndex(index);
      }

      if (index.pendingUrls.length > 0 && crawled < maxPages) {
        await new Promise((resolve) => setTimeout(resolve, CRAWL_DELAY_MS));
      }
    }

    await saveIndex(index);

    // Rebuild in-memory search index
    cachedIndex = index;
    miniSearch = buildSearchIndex(index.pages);
  } finally {
    crawlInProgress = false;
  }

  return {
    crawled,
    total: index.pages.length,
    pending: index.pendingUrls.length,
    errors
  };
}

/**
 * Search the crawled help-site index.  Returns an empty array if nothing has
 * been crawled yet.
 */
export async function searchCrawledDocs(query: string, limit: number): Promise<SearchResult[]> {
  await ensureSearchIndex();

  if (!miniSearch || !cachedIndex || cachedIndex.pages.length === 0) {
    return [];
  }

  const hits = miniSearch.search(query).slice(0, limit * 2);

  return hits.slice(0, limit).map((hit) => ({
    id: `crawled-${String(hit.id)}`,
    title: String(hit.title ?? ""),
    snippet: createSnippet(String(hit.text ?? ""), query),
    body: String(hit.text ?? ""),
    score: hit.score,
    sourceType: "crawled" as const,
    tags: [],
    citations: [{ title: String(hit.title ?? ""), url: String(hit.url ?? "") }]
  }));
}
