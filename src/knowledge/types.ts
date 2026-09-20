export type SourceMode = "curated" | "official" | "pdf" | "crawled" | "both" | "all";

export interface Citation {
  title: string;
  url: string;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  summary: string;
  body: string;
  tags: string[];
  citations: Citation[];
}

export interface SearchResult {
  id: string;
  title: string;
  snippet: string;
  body: string;
  score: number;
  sourceType: "curated" | "official" | "pdf" | "crawled";
  tags: string[];
  citations: Citation[];
}

export interface PdfDocument {
  id: string;
  fileName: string;
  title: string;
  text: string;
  pageCount?: number;
  sizeBytes: number;
  modifiedMs: number;
}

export interface TopicResponse {
  topic: string;
  answer: string;
  examples?: string[];
  citations: Citation[];
  relatedTopics: string[];
}
