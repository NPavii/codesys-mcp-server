import MiniSearch from "minisearch";
import { curatedDocs } from "./curated.js";
import type { KnowledgeDocument, SearchResult } from "./types.js";

type SearchableDocument = KnowledgeDocument & {
  tagText: string;
};

const searchableDocs: SearchableDocument[] = curatedDocs.map((doc) => ({
  ...doc,
  tagText: doc.tags.join(" ")
}));

const docsById = new Map(curatedDocs.map((doc) => [doc.id, doc]));

const miniSearch = new MiniSearch<SearchableDocument>({
  fields: ["title", "summary", "body", "tagText"],
  storeFields: ["id"],
  searchOptions: {
    boost: {
      title: 3,
      summary: 2,
      tagText: 2
    },
    prefix: true,
    fuzzy: 0.2
  }
});

miniSearch.addAll(searchableDocs);

function normalizeQuery(query: string): string {
  return query.trim().replace(/\s+/g, " ");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function createSnippet(text: string, query: string, maxLength = 1500): string {
  const cleanText = text.replace(/\s+/g, " ").trim();
  if (cleanText.length <= maxLength) {
    return cleanText;
  }

  const tokens = normalizeQuery(query)
    .toLowerCase()
    .split(" ")
    .filter((token) => token.length > 2);

  const matchIndex = tokens.reduce((best, token) => {
    if (best >= 0) {
      return best;
    }
    return cleanText.toLowerCase().search(new RegExp(`\\b${escapeRegExp(token)}`));
  }, -1);

  if (matchIndex < 0) {
    return `${cleanText.slice(0, maxLength - 1).trim()}...`;
  }

  const start = Math.max(0, matchIndex - Math.floor(maxLength / 3));
  const end = Math.min(cleanText.length, start + maxLength);
  const prefix = start > 0 ? "..." : "";
  const suffix = end < cleanText.length ? "..." : "";
  return `${prefix}${cleanText.slice(start, end).trim()}${suffix}`;
}

export function searchCuratedDocs(query: string, limit = 5): SearchResult[] {
  const cleanQuery = normalizeQuery(query);
  if (!cleanQuery) {
    return curatedDocs.slice(0, limit).map((doc, index) => ({
      id: doc.id,
      title: doc.title,
      snippet: doc.summary,
      body: doc.body,
      score: limit - index,
      sourceType: "curated",
      tags: doc.tags,
      citations: doc.citations
    }));
  }

  return miniSearch
    .search(cleanQuery)
    .slice(0, limit)
    .flatMap((hit) => {
      const doc = docsById.get(String(hit.id));
      if (!doc) {
        return [];
      }

      return {
        id: doc.id,
        title: doc.title,
        snippet: createSnippet(`${doc.summary}\n\n${doc.body}`, cleanQuery),
        body: doc.body,
        score: hit.score,
        sourceType: "curated" as const,
        tags: doc.tags,
        citations: doc.citations
      };
    });
}

export function findBestCuratedTopic(topic: string): KnowledgeDocument | undefined {
  const cleanTopic = normalizeQuery(topic).toLowerCase();
  if (!cleanTopic) {
    return undefined;
  }

  const exact = curatedDocs.find((doc) => {
    const title = doc.title.toLowerCase();
    return (
      doc.id === cleanTopic ||
      title === cleanTopic ||
      doc.tags.some((tag) => tag.toLowerCase() === cleanTopic)
    );
  });

  if (exact) {
    return exact;
  }

  const [firstHit] = searchCuratedDocs(cleanTopic, 1);
  return firstHit ? docsById.get(firstHit.id) : undefined;
}

export function listCuratedTopics(): KnowledgeDocument[] {
  return [...curatedDocs];
}
