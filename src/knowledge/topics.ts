import type { AppConfig } from "../config.js";
import type { Citation, SearchResult, SourceMode, TopicResponse } from "./types.js";
import { findBestCuratedTopic, searchCuratedDocs } from "./search.js";
import { searchOfficialDocs } from "./liveLookup.js";
import { searchPdfDocuments } from "./pdfDocs.js";
import { searchCrawledDocs } from "./helpSiteCrawler.js";

export type TopicDetail = "brief" | "normal" | "deep";

function uniqueCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  return citations.filter((citation) => {
    if (seen.has(citation.url)) {
      return false;
    }
    seen.add(citation.url);
    return true;
  });
}

function firstParagraph(body: string): string {
  return body.split(/\n\s*\n/)[0]?.trim() ?? body.trim();
}

function extractExamples(body: string): string[] {
  const examples: string[] = [];
  const codeLikeBlocks = body.match(
    /(?:FUNCTION_BLOCK|TYPE|VAR\n)[\s\S]+?(?:END_CASE|END_TYPE|END_VAR|;)\n?/g
  );
  if (codeLikeBlocks) {
    examples.push(...codeLikeBlocks.slice(0, 2).map((block) => block.trim()));
  }
  return examples;
}

function relatedTopicsFromResults(results: SearchResult[], currentTopic: string): string[] {
  const lowerCurrent = currentTopic.toLowerCase();
  return results
    .map((result) => result.title)
    .filter((title) => title.toLowerCase() !== lowerCurrent)
    .slice(0, 5);
}

export async function searchCodesysDocs(
  query: string,
  limit: number,
  sourceMode: SourceMode,
  config: AppConfig
): Promise<SearchResult[]> {
  const normalizedLimit = Math.min(Math.max(limit, 1), 10);

  const curated = ["curated", "both", "all"].includes(sourceMode)
    ? searchCuratedDocs(query, normalizedLimit)
    : [];
  const official = ["official", "both", "all"].includes(sourceMode)
    ? await searchOfficialDocs(query, normalizedLimit, config)
    : [];
  const pdf = ["pdf", "all"].includes(sourceMode)
    ? await searchPdfDocuments(query, normalizedLimit, config)
    : [];

  const crawled = ["crawled", "all"].includes(sourceMode)
    ? await searchCrawledDocs(query, normalizedLimit)
    : [];

  const seen = new Set<string>();
  return [...curated, ...official, ...pdf, ...crawled]
    .sort((a, b) => b.score - a.score)
    .filter((result) => {
      const key = `${result.sourceType}:${result.id}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .slice(0, normalizedLimit);
}

export async function getCodesysTopic(
  topic: string,
  detail: TopicDetail,
  includeExamples: boolean,
  config: AppConfig
): Promise<TopicResponse> {
  const doc = findBestCuratedTopic(topic);
  const searchResults = await searchCodesysDocs(topic, 5, "all", config);

  if (!doc) {
    return {
      topic,
      answer:
        "I could not find a curated CODESYS topic for this query. Use search_codesys_docs for broader results, and verify any runtime or library details in the target CODESYS project.",
      citations: uniqueCitations(searchResults.flatMap((result) => result.citations)),
      relatedTopics: relatedTopicsFromResults(searchResults, topic)
    };
  }

  const answer =
    detail === "brief"
      ? `${doc.summary}\n\n${firstParagraph(doc.body)}`
      : detail === "deep"
        ? `${doc.body}\n\nImplementation note: adapt this guidance to the target runtime, device, installed libraries, and existing project conventions before compiling and testing in CODESYS.`
        : doc.body;

  return {
    topic: doc.title,
    answer,
    examples: includeExamples ? extractExamples(doc.body) : undefined,
    citations: uniqueCitations([
      ...doc.citations,
      ...searchResults.flatMap((result) => result.citations)
    ]),
    relatedTopics: relatedTopicsFromResults(searchResults, doc.title)
  };
}

export function buildWritingGuidance(input: {
  task: string;
  target?: string;
  language?: string;
  constraints?: string;
}): TopicResponse {
  const target = input.target?.trim() || "function_block";
  const language = input.language?.trim() || "Structured Text";
  const constraints = input.constraints?.trim();
  const safetyRelevant =
    /safety|motion|axis|fieldbus|ethercat|profinet|modbus|io|i\/o|hardware|drive/i.test(
      `${input.task} ${constraints ?? ""}`
    );

  const answer = `
Use ${language} and shape the solution as a ${target}. Start from the project's existing naming and library conventions, then compile and test in the target CODESYS runtime.

Recommended workflow:
1. Identify the POU type: use a function block for stateful behavior, a function for pure calculations, and a program for task-level orchestration.
2. Define a small interface: VAR_INPUT for commands and measurements, VAR_OUTPUT for status and commands, and VAR for internal state.
3. Keep cyclic execution explicit: call timers, triggers, and child function blocks once per intended scan.
4. Use named states, timers, and interlock variables that can be monitored online during commissioning.
5. Avoid inventing library APIs. Confirm Standard, vendor, and project library functions in the CODESYS Library Manager.
6. Add only comments that explain machine intent, timing assumptions, or non-obvious constraints.
${constraints ? `\nUser constraints to preserve:\n${constraints}` : ""}
${safetyRelevant ? "\nSafety note: because the task mentions safety, motion, fieldbus, hardware, or I/O, verify behavior against the exact device manual, library version, risk assessment, and machine safety requirements before operation." : ""}

For the requested task:
${input.task}
`.trim();

  return {
    topic: "CODESYS Writing Guidance",
    answer,
    citations: [
      {
        title: "Programming in Structured Text",
        url: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_programming_in_st.html"
      },
      {
        title: "Object: POU",
        url: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_f_obj_pou.html"
      },
      {
        title: "Object: Function Block",
        url: "https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_obj_function_block.html"
      }
    ],
    relatedTopics: [
      "Choosing The Right POU Type",
      "Function Block Design",
      "Structured Text Basics"
    ]
  };
}
