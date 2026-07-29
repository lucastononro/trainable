---
name: web-search
description: Search the open web (blog posts, repos, dataset homepages, news).
when_to_use: For anything that isn't a research paper. Use papers-search for arxiv / Semantic Scholar.
version: '0.1'
kind: capability
---

# web-search

Search the open web. Use this for anything that isn't a paper:

- blog posts and tutorials
- GitHub repos / framework docs
- dataset homepages, license pages, government open-data portals
- news / "what's the current SOTA on X" style queries

## Backends (auto-selected, no agent action needed)

- **Tavily** — if `TAVILY_API_KEY` is set on the backend
- **Brave Search** — if `BRAVE_API_KEY` is set
- **DuckDuckGo** — fallback, no key required

Returns a ranked list of `{title, url, snippet, source}`. The studio
shows a rich preview card panel as soon as results land — the user
can click through to any source.

## When NOT to use

- Paper discovery → use `papers-search` (arxiv + Semantic Scholar)
- Reading a known arxiv paper → use `papers-search(operation="read_paper", ...)`
- HF Hub model/dataset lookup → call the Hub API directly via execute-code
