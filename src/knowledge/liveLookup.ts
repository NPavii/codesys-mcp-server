import * as cheerio from "cheerio";
import { officialSources, type OfficialSource } from "./sources.js";
import type { AppConfig } from "../config.js";
import type { SearchResult } from "./types.js";
import { createSnippet } from "./search.js";

const allowedOfficialHosts = new Set([
  "content.helpme-codesys.com",
  "codesys.com",
  "www.codesys.com",
  "us.codesys.com",
  "store.codesys.com",
  "us.store.codesys.com"
]);

interface CachedPage {
  expiresAt: number;
  title: string;
  text: string;
}

const pageCache = new Map<string, CachedPage>();

export function isAllowedOfficialUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    return url.protocol === "https:" && allowedOfficialHosts.has(url.hostname);
  } catch {
    return false;
  }
}

function queryTokens(query: string): string[] {
  return query
    .toLowerCase()
    .replace(/[^a-z0-9_#-]+/g, " ")
    .split(/\s+/)
    .filter((token) => token.length > 2);
}

function candidateScore(source: OfficialSource, tokens: string[]): number {
  const haystack = `${source.title} ${source.tags.join(" ")} ${source.url}`.toLowerCase();
  return tokens.reduce((score, token) => {
    return haystack.includes(token) ? score + 2 : score;
  }, 0);
}

function scoreText(text: string, tokens: string[]): number {
  const lowerText = text.toLowerCase();
  return tokens.reduce((score, token) => {
    const matches = lowerText.match(new RegExp(`\\b${escapeRegExp(token)}`, "g"));
    return score + (matches?.length ?? 0);
  }, 0);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function pickSources(query: string, limit: number): OfficialSource[] {
  const tokens = queryTokens(query);
  const scored = officialSources
    .map((source) => ({
      source,
      score: candidateScore(source, tokens)
    }))
    .sort((a, b) => b.score - a.score);

  const matching = scored.filter((item) => item.score > 0).map((item) => item.source);
  return (matching.length > 0 ? matching : scored.map((item) => item.source)).slice(
    0,
    Math.max(limit * 2, 4)
  );
}

async function fetchOfficialPage(
  source: OfficialSource,
  config: AppConfig
): Promise<CachedPage | undefined> {
  if (!isAllowedOfficialUrl(source.url)) {
    return undefined;
  }

  const cached = pageCache.get(source.url);
  if (cached && cached.expiresAt > Date.now()) {
    return cached;
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.officialLookupTimeoutMs);

  try {
    const response = await fetch(source.url, {
      signal: controller.signal,
      headers: {
        "user-agent": "codesys-mcp-server/0.1 (+https://modelcontextprotocol.io)"
      }
    });

    if (!response.ok) {
      return cached;
    }

    const html = await response.text();
    const $ = cheerio.load(html);
    $("script, style, nav, footer, header, noscript, svg, img").remove();

    const title = $("h1").first().text().trim() || $("title").first().text().trim() || source.title;
    const text = $("main, article, body").text().replace(/\s+/g, " ").trim().slice(0, 40000);

    const page = {
      title,
      text,
      expiresAt: Date.now() + config.officialLookupCacheTtlMs
    };
    pageCache.set(source.url, page);
    return page;
  } catch {
    return cached;
  } finally {
    clearTimeout(timeout);
  }
}

export function clearOfficialLookupCache(): void {
  pageCache.clear();
}

/** Fetch any allowed CODESYS URL and return its full extracted text. */
export async function fetchPageByUrl(
  rawUrl: string,
  config: AppConfig,
  timeoutMs = 10000
): Promise<{ title: string; text: string; url: string } | null> {
  if (!isAllowedOfficialUrl(rawUrl)) {
    return null;
  }

  const cached = pageCache.get(rawUrl);
  if (cached && cached.expiresAt > Date.now()) {
    return { title: cached.title, text: cached.text, url: rawUrl };
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(rawUrl, {
      signal: controller.signal,
      headers: { "user-agent": "codesys-mcp-server/0.1 (+https://modelcontextprotocol.io)" }
    });

    if (!response.ok) return null;

    const html = await response.text();
    const $ = cheerio.load(html);
    $("script, style, nav, footer, header, noscript, svg, img").remove();

    const title = $("h1").first().text().trim() || $("title").first().text().trim() || rawUrl;
    const text = $("main, article, body").text().replace(/\s+/g, " ").trim().slice(0, 60000);

    const page = { title, text, expiresAt: Date.now() + config.officialLookupCacheTtlMs };
    pageCache.set(rawUrl, page);
    return { title, text, url: rawUrl };
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

export async function searchOfficialDocs(
  query: string,
  limit: number,
  config: AppConfig
): Promise<SearchResult[]> {
  if (!config.officialLookupEnabled || !query.trim()) {
    return [];
  }

  const tokens = queryTokens(query);
  const sources = pickSources(query, limit);
  const pages = await Promise.all(
    sources.map(async (source) => ({
      source,
      page: await fetchOfficialPage(source, config)
    }))
  );

  return pages
    .flatMap(({ source, page }) => {
      if (!page?.text) {
        return [];
      }

      const textScore = scoreText(`${page.title} ${page.text}`, tokens);
      if (textScore === 0 && tokens.length > 0) {
        return [];
      }

      return {
        id: source.id,
        title: page.title || source.title,
        snippet: createSnippet(page.text, query),
        body: page.text,
        score: textScore + candidateScore(source, tokens),
        sourceType: "official" as const,
        tags: source.tags,
        citations: [{ title: page.title || source.title, url: source.url }]
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}
