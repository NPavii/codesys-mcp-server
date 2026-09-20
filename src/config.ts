import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export interface AppConfig {
  host: string;
  port: number;
  officialLookupEnabled: boolean;
  officialLookupTimeoutMs: number;
  officialLookupCacheTtlMs: number;
  pdfSearchEnabled: boolean;
  pdfDocsDir: string;
  pdfMaxTextChars: number;
}

function parseIntegerEnv(name: string, fallback: number): number {
  const value = process.env[name];
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseBooleanEnv(name: string, fallback: boolean): boolean {
  const value = process.env[name];
  if (!value) {
    return fallback;
  }

  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function loadDotEnvFile(): void {
  const envPath = path.resolve(process.cwd(), ".env");
  if (!existsSync(envPath)) {
    return;
  }

  const lines = readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex <= 0) {
      continue;
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    let value = trimmed.slice(separatorIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

export function loadConfig(): AppConfig {
  loadDotEnvFile();

  return {
    host: process.env.HOST ?? "0.0.0.0",
    port: parseIntegerEnv("PORT", 3000),
    officialLookupEnabled: parseBooleanEnv("OFFICIAL_LOOKUP_ENABLED", true),
    officialLookupTimeoutMs: parseIntegerEnv("OFFICIAL_LOOKUP_TIMEOUT_MS", 3500),
    officialLookupCacheTtlMs: parseIntegerEnv("OFFICIAL_LOOKUP_CACHE_TTL_SECONDS", 3600) * 1000,
    pdfSearchEnabled: parseBooleanEnv("PDF_SEARCH_ENABLED", true),
    pdfDocsDir: process.env.PDF_DOCS_DIR ?? "docs/pdfs",
    pdfMaxTextChars: parseIntegerEnv("PDF_MAX_TEXT_CHARS", 200000)
  };
}
