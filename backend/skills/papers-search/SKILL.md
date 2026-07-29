---
name: papers-search
description: Search and read ML research papers (arXiv + Semantic Scholar + HF Hub).
when_to_use: When the agent needs to find or read research papers, citations, or paper-linked datasets/models. Use web-search for non-paper queries.
version: '0.1'
kind: capability
---

# papers-search

Search and read ML research papers. Backends:

- **arXiv API** (free, no key) — primary search + metadata + PDFs
- **arXiv HTML / ar5iv** — sectioned full-text reading
- **Semantic Scholar Graph** — citation graph, filtered search, snippet search, TL;DR, recommendations
- **Hugging Face Hub** — `find_datasets/models/collections` only, for resources tagged `arxiv:<id>`

Use `web-search` for non-paper queries (blog posts, GitHub repos, dataset homepages, news).

## Typical research flows

```
search → read_paper → find_all_resources
search → paper_details → citation_graph → read_paper   (trace influence)
snippet_search → paper_details → read_paper             (find specific claims)
search → download_pdf                                   (when HTML/ar5iv don't exist)
```

## Operations

- `search` — arXiv search by default. Supplying any of `date_from` / `date_to` / `categories` / `min_citations` / `sort_by` switches to Semantic Scholar bulk search with those filters.
- `paper_details` — title, abstract, authors via arXiv. Enriched with Semantic Scholar data when available (citation count, TLDR, venue).
- `read_paper` — sectioned full text. Without `section`, returns abstract + TOC. With `section` (e.g. `"3"` or `"Experiments"`), returns the section's full text. Fallback chain: arXiv HTML → ar5iv → PDF text → abstract.
- `download_pdf` — download a PDF, extract text via pypdf, and persist it under `/sessions/{session_id}/papers/{arxiv_id}.txt` so other agents can re-read it via list-session-files / read-session-file. Returns the extracted text (truncated for the agent's context).
- `citation_graph` — references + citations with influence flags and intent labels. Direction defaults to `"both"`.
- `snippet_search` — semantic search over 12M+ full-text passages on Semantic Scholar.
- `recommend` — find similar papers from a single paper or a list of positive/negative examples.
- `find_datasets` / `find_models` / `find_collections` — HF Hub resources tagged with `arxiv:<id>`.
- `find_all_resources` — parallel fetch of datasets + models + collections for one paper.
