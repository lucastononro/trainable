"""papers_search tool — discover ML research papers and linked resources.

Backends:
- arXiv API (free, no key)         — primary search + paper metadata + PDFs
- arXiv HTML / ar5iv               — sectioned full-text reading
- Semantic Scholar Graph API       — citation graph, snippet search, filters,
                                     recommendations, TLDR enrichment
- Hugging Face Hub                 — `find_datasets/models/collections` only
                                     (resources tagged with arxiv:<id>)

Operations: search, paper_details, read_paper, download_pdf, citation_graph,
snippet_search, recommend, find_datasets, find_models, find_collections,
find_all_resources.

For general (non-paper) web search, use the `web_search` tool instead.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api"
ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_ABS = "https://arxiv.org/abs"
ARXIV_PDF = "https://arxiv.org/pdf"
ARXIV_HTML = "https://arxiv.org/html"
AR5IV_HTML = "https://ar5iv.labs.arxiv.org/html"

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_SUMMARY_LEN = 300
MAX_SECTION_PREVIEW_LEN = 280
MAX_SECTION_TEXT_LEN = 8000
MAX_PDF_TEXT_LEN = 16000
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB safety cap on arxiv PDF downloads

ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

SORT_MAP = {
    "downloads": "downloads",
    "likes": "likes",
    "trending": "trendingScore",
}

# ── Semantic Scholar ──────────────────────────────────────────────────

S2_API = "https://api.semanticscholar.org"
S2_API_KEY = os.environ.get("S2_API_KEY")
S2_HEADERS: dict[str, str] = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}
S2_TIMEOUT = 12

# Module-level rate-limit + cache. Survives across handler invocations
# inside the same backend process — keyed by (path, sorted_params_tuple).
_s2_last_request: float = 0.0
_s2_cache: dict[str, Any] = {}
_S2_CACHE_MAX = 500


def _s2_paper_id(arxiv_id: str) -> str:
    return f"ARXIV:{arxiv_id}"


def _s2_cache_key(path: str, params: dict | None) -> str:
    p = tuple(sorted((params or {}).items()))
    return f"{path}:{p}"


async def _s2_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response | None:
    """S2 request with 2 retries on 429/5xx. Rate-limited only when API key is set."""
    global _s2_last_request
    url = f"{S2_API}{path}"
    kwargs.setdefault("headers", {}).update(S2_HEADERS)
    kwargs.setdefault("timeout", S2_TIMEOUT)

    for attempt in range(3):
        if S2_API_KEY:
            min_interval = 1.0 if "search" in path else 0.1
            elapsed = time.monotonic() - _s2_last_request
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
        _s2_last_request = time.monotonic()

        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429:
                if attempt < 2:
                    await asyncio.sleep(60)
                    continue
                return None
            if resp.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(3)
                    continue
                return None
            return resp
        except (httpx.RequestError, httpx.HTTPStatusError):
            if attempt < 2:
                await asyncio.sleep(3)
                continue
            return None
    return None


async def _s2_get_json(
    client: httpx.AsyncClient,
    path: str,
    params: dict | None = None,
) -> dict | None:
    key = _s2_cache_key(path, params)
    if key in _s2_cache:
        return _s2_cache[key]
    resp = await _s2_request(client, "GET", path, params=params or {})
    if resp and resp.status_code == 200:
        data = resp.json()
        if len(_s2_cache) < _S2_CACHE_MAX:
            _s2_cache[key] = data
        return data
    return None


# ── arxiv API (search + metadata) ─────────────────────────────────────


_ARXIV_ID_RE = re.compile(r"arxiv\.org/abs/([0-9]{4}\.[0-9]{4,6}(?:v\d+)?)", re.I)


def _strip_arxiv_id(s: str) -> str:
    """Best-effort extraction of a bare arxiv id from a URL or string."""
    m = _ARXIV_ID_RE.search(s or "")
    if m:
        return m.group(1)
    return (s or "").rsplit("/", 1)[-1].strip()


def _arxiv_entry_to_dict(entry: ET.Element) -> dict[str, Any]:
    """Parse one <entry> from the arxiv Atom feed into a normalised dict."""

    def _t(tag: str) -> str:
        el = entry.find(f"a:{tag}", ATOM_NS)
        return (el.text or "").strip() if el is not None else ""

    aid = _strip_arxiv_id(_t("id"))
    title = re.sub(r"\s+", " ", _t("title")).strip()
    summary = re.sub(r"\s+", " ", _t("summary")).strip()
    published = _t("published")[:10]
    updated = _t("updated")[:10]
    authors = [
        (a.findtext("a:name", default="", namespaces=ATOM_NS) or "").strip()
        for a in entry.findall("a:author", ATOM_NS)
    ]
    primary_cat = ""
    pc = entry.find("arxiv:primary_category", ATOM_NS)
    if pc is not None:
        primary_cat = pc.attrib.get("term", "")
    return {
        "arxiv_id": aid,
        "title": title,
        "summary": summary,
        "authors": authors,
        "published": published,
        "updated": updated,
        "primary_category": primary_cat,
        "abs_url": f"{ARXIV_ABS}/{aid}",
        "pdf_url": f"{ARXIV_PDF}/{aid}.pdf",
    }


_ARXIV_SORT_MAP = {
    "relevance": "relevance",
    "submittedDate": "submittedDate",
    "lastUpdatedDate": "lastUpdatedDate",
}


async def _arxiv_search(
    query: str,
    limit: int,
    sort_by: str = "relevance",
) -> list[dict[str, Any]]:
    """Run an arxiv API search. Returns list of normalised entry dicts."""
    sort = _ARXIV_SORT_MAP.get(sort_by, "relevance")
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": sort,
        "sortOrder": "descending",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        feed = resp.text
    try:
        root = ET.fromstring(feed)
    except ET.ParseError as e:
        logger.warning("arxiv feed parse error: %s", e)
        return []
    return [_arxiv_entry_to_dict(e) for e in root.findall("a:entry", ATOM_NS)]


async def _arxiv_get(arxiv_id: str) -> dict[str, Any] | None:
    """Fetch a single paper's metadata via arxiv id_list."""
    params = {"id_list": arxiv_id, "max_results": 1}
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ARXIV_API, params=params)
        resp.raise_for_status()
        feed = resp.text
    try:
        root = ET.fromstring(feed)
    except ET.ParseError:
        return None
    entries = root.findall("a:entry", ATOM_NS)
    if not entries:
        return None
    return _arxiv_entry_to_dict(entries[0])


# ── arxiv PDF download + extraction ───────────────────────────────────


async def _fetch_arxiv_pdf(arxiv_id: str) -> bytes | None:
    """Download the arxiv PDF for `arxiv_id`. Returns bytes or None on failure."""
    url = f"{ARXIV_PDF}/{arxiv_id}.pdf"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("arxiv pdf %s returned %s", arxiv_id, resp.status_code)
                return None
            data = resp.content
            if len(data) > MAX_PDF_BYTES:
                logger.warning("arxiv pdf %s too large: %d bytes", arxiv_id, len(data))
                return None
            return data
    except Exception as e:
        logger.warning("arxiv pdf %s fetch failed: %s", arxiv_id, e)
        return None


def _pdf_to_text(data: bytes) -> tuple[str, int]:
    """Extract plain text from a PDF byte string. Runs in a worker thread when
    called via asyncio.to_thread. Returns (text, page_count)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:
            pages.append("")
    return ("\n\n".join(pages), len(reader.pages))


# ── arxiv HTML parsing ────────────────────────────────────────────────


def _parse_paper_html(html: str) -> dict[str, Any]:
    """Parse arxiv HTML into {title, abstract, sections[{id,title,level,text}]}."""
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("h1", class_="ltx_title")
    title = title_el.get_text(strip=True).removeprefix("Title:") if title_el else ""

    abstract_el = soup.find("div", class_="ltx_abstract")
    abstract = ""
    if abstract_el:
        for child in abstract_el.children:
            if isinstance(child, Tag) and child.name in ("h6", "h2", "h3", "p", "span"):
                if child.get_text(strip=True).lower() == "abstract":
                    continue
            if isinstance(child, Tag) and child.name == "p":
                abstract += child.get_text(separator=" ", strip=True) + " "
        abstract = abstract.strip()

    sections: list[dict[str, Any]] = []
    headings = soup.find_all(["h2", "h3"], class_=lambda c: c and "ltx_title" in c)
    for heading in headings:
        level = 2 if heading.name == "h2" else 3
        heading_text = heading.get_text(separator=" ", strip=True)

        text_parts: list[str] = []
        sibling = heading.find_next_sibling()
        while sibling:
            if isinstance(sibling, Tag):
                if sibling.name in ("h2", "h3") and "ltx_title" in (
                    sibling.get("class") or []
                ):
                    break
                if sibling.name == "h2" and level == 3:
                    break
                text_parts.append(sibling.get_text(separator=" ", strip=True))
            sibling = sibling.find_next_sibling()

        parent_section = heading.find_parent("section")
        if parent_section and not text_parts:
            for p in parent_section.find_all("p", recursive=False):
                text_parts.append(p.get_text(separator=" ", strip=True))

        section_text = "\n\n".join(t for t in text_parts if t)
        num_match = re.match(r"^([A-Z]?\d+(?:\.\d+)*)\s", heading_text)
        section_id = num_match.group(1) if num_match else ""

        sections.append(
            {
                "id": section_id,
                "title": heading_text,
                "level": level,
                "text": section_text,
            }
        )

    return {"title": title, "abstract": abstract, "sections": sections}


def _find_section(sections: list[dict], query: str) -> dict | None:
    q = query.lower().strip()
    for s in sections:
        if s["id"] == q or s["id"] == query:
            return s
    for s in sections:
        if q == s["title"].lower():
            return s
    for s in sections:
        if q in s["title"].lower():
            return s
    for s in sections:
        if s["id"].startswith(q + ".") or s["id"] == q:
            return s
    return None


# ── formatting helpers ────────────────────────────────────────────────


def _clean_description(text: str) -> str:
    text = re.sub(r"[\t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len] + "..."


def _format_paper_list(
    papers: list, title: str, date: str | None = None, query: str | None = None
) -> str:
    lines = [f"# {title}" + (f" ({date})" if date else "")]
    if query:
        lines.append(f"Filtered by: '{query}'")
    lines.append(f"Showing {len(papers)} paper(s)\n")

    for i, item in enumerate(papers, 1):
        paper = item.get("paper", item)
        arxiv_id = paper.get("id", "")
        ptitle = paper.get("title", "Unknown")
        upvotes = paper.get("upvotes", 0)
        summary = paper.get("ai_summary") or _truncate(
            paper.get("summary", ""), MAX_SUMMARY_LEN
        )
        keywords = paper.get("ai_keywords") or []
        github = paper.get("githubRepo") or ""
        stars = paper.get("githubStars") or 0

        lines.append(f"## {i}. {ptitle}")
        lines.append(f"**arxiv_id:** {arxiv_id} | **upvotes:** {upvotes}")
        lines.append(f"https://huggingface.co/papers/{arxiv_id}")
        if keywords:
            lines.append(f"**Keywords:** {', '.join(keywords[:5])}")
        if github:
            lines.append(f"**GitHub:** {github} ({stars} stars)")
        if summary:
            lines.append(f"**Summary:** {_truncate(summary, MAX_SUMMARY_LEN)}")
        lines.append("")
    return "\n".join(lines)


def _format_paper_detail(paper: dict, s2_data: dict | None = None) -> str:
    arxiv_id = paper.get("id", "")
    title = paper.get("title", "Unknown")
    upvotes = paper.get("upvotes", 0)
    ai_summary = paper.get("ai_summary") or ""
    summary = paper.get("summary", "")
    keywords = paper.get("ai_keywords") or []
    github = paper.get("githubRepo") or ""
    stars = paper.get("githubStars") or 0
    authors = paper.get("authors") or []

    lines = [f"# {title}"]
    meta = [f"**arxiv_id:** {arxiv_id}", f"**upvotes:** {upvotes}"]
    if s2_data:
        meta.append(
            f"**citations:** {s2_data.get('citationCount', 0)} "
            f"({s2_data.get('influentialCitationCount', 0)} influential)"
        )
    lines.append(" | ".join(meta))
    lines.append(f"https://huggingface.co/papers/{arxiv_id}")
    lines.append(f"https://arxiv.org/abs/{arxiv_id}")

    if authors:
        names = [a.get("name", "") for a in authors[:10]]
        s = ", ".join(n for n in names if n)
        if len(authors) > 10:
            s += f" (+{len(authors) - 10} more)"
        lines.append(f"**Authors:** {s}")

    if keywords:
        lines.append(f"**Keywords:** {', '.join(keywords)}")
    if s2_data and s2_data.get("s2FieldsOfStudy"):
        fields = [
            f["category"] for f in s2_data["s2FieldsOfStudy"] if f.get("category")
        ]
        if fields:
            lines.append(f"**Fields:** {', '.join(fields)}")
    if s2_data and s2_data.get("venue"):
        lines.append(f"**Venue:** {s2_data['venue']}")
    if github:
        lines.append(f"**GitHub:** {github} ({stars} stars)")

    if s2_data and s2_data.get("tldr"):
        tldr = s2_data["tldr"].get("text", "")
        if tldr:
            lines.append(f"\n## TL;DR\n{tldr}")
    if ai_summary:
        lines.append(f"\n## AI Summary\n{ai_summary}")
    if summary:
        lines.append(f"\n## Abstract\n{_truncate(summary, 500)}")

    lines.append(
        "\n**Next:** read_paper to read sections, find_all_resources for "
        "linked datasets/models, citation_graph to trace references."
    )
    return "\n".join(lines)


def _format_read_paper_toc(parsed: dict[str, Any], arxiv_id: str) -> str:
    lines = [f"# {parsed['title']}", f"https://arxiv.org/abs/{arxiv_id}\n"]
    if parsed["abstract"]:
        lines.append(f"## Abstract\n{parsed['abstract']}\n")
    lines.append("## Sections")
    for s in parsed["sections"]:
        prefix = "  " if s["level"] == 3 else ""
        preview = (
            _truncate(s["text"], MAX_SECTION_PREVIEW_LEN) if s["text"] else "(empty)"
        )
        lines.append(f"{prefix}- **{s['title']}**: {preview}")
    lines.append(
        '\nCall read_paper with section="<num or name>" (e.g. section="4" or '
        'section="Experiments") to read the full text.'
    )
    return "\n".join(lines)


def _format_read_paper_section(section: dict, arxiv_id: str) -> str:
    lines = [f"# {section['title']}", f"https://arxiv.org/abs/{arxiv_id}\n"]
    text = section["text"]
    if len(text) > MAX_SECTION_TEXT_LEN:
        text = (
            text[:MAX_SECTION_TEXT_LEN]
            + f"\n\n... (truncated at {MAX_SECTION_TEXT_LEN} chars)"
        )
    lines.append(text or "(This section has no extractable text content.)")
    return "\n".join(lines)


def _format_datasets(datasets: list, arxiv_id: str, sort: str) -> str:
    lines = [
        f"# Datasets linked to paper {arxiv_id}",
        f"https://huggingface.co/papers/{arxiv_id}",
        f"Showing {len(datasets)} dataset(s), sorted by {sort}\n",
    ]
    for i, ds in enumerate(datasets, 1):
        ds_id = ds.get("id", "unknown")
        downloads = ds.get("downloads", 0)
        likes = ds.get("likes", 0)
        desc = _truncate(
            _clean_description(ds.get("description") or ""), MAX_SUMMARY_LEN
        )
        tags = ds.get("tags") or []
        interesting = [t for t in tags if not t.startswith(("arxiv:", "region:"))][:5]

        lines.append(f"**{i}. [{ds_id}](https://huggingface.co/datasets/{ds_id})**")
        lines.append(f"   Downloads: {downloads:,} | Likes: {likes}")
        if interesting:
            lines.append(f"   Tags: {', '.join(interesting)}")
        if desc:
            lines.append(f"   {desc}")
        lines.append("")
    return "\n".join(lines)


def _format_datasets_compact(datasets: list) -> str:
    if not datasets:
        return "## Datasets\nNone found"
    lines = [f"## Datasets ({len(datasets)})"]
    for ds in datasets:
        lines.append(
            f"- **{ds.get('id', '?')}** ({ds.get('downloads', 0):,} downloads)"
        )
    return "\n".join(lines)


def _format_models(models: list, arxiv_id: str, sort: str) -> str:
    lines = [
        f"# Models linked to paper {arxiv_id}",
        f"https://huggingface.co/papers/{arxiv_id}",
        f"Showing {len(models)} model(s), sorted by {sort}\n",
    ]
    for i, m in enumerate(models, 1):
        model_id = m.get("id", "unknown")
        downloads = m.get("downloads", 0)
        likes = m.get("likes", 0)
        pipeline = m.get("pipeline_tag") or ""
        library = m.get("library_name") or ""

        lines.append(f"**{i}. [{model_id}](https://huggingface.co/{model_id})**")
        meta = f"   Downloads: {downloads:,} | Likes: {likes}"
        if pipeline:
            meta += f" | Task: {pipeline}"
        if library:
            meta += f" | Library: {library}"
        lines.append(meta)
        lines.append("")
    return "\n".join(lines)


def _format_models_compact(models: list) -> str:
    if not models:
        return "## Models\nNone found"
    lines = [f"## Models ({len(models)})"]
    for m in models:
        pipeline = m.get("pipeline_tag") or ""
        suffix = f" ({pipeline})" if pipeline else ""
        lines.append(
            f"- **{m.get('id', '?')}** ({m.get('downloads', 0):,} downloads){suffix}"
        )
    return "\n".join(lines)


def _format_collections(collections: list, arxiv_id: str) -> str:
    lines = [
        f"# Collections containing paper {arxiv_id}",
        f"Showing {len(collections)} collection(s)\n",
    ]
    for i, c in enumerate(collections, 1):
        slug = c.get("slug", "")
        title = c.get("title", "Untitled")
        upvotes = c.get("upvotes", 0)
        owner = c.get("owner", {}).get("name", "")
        desc = _truncate(c.get("description") or "", MAX_SUMMARY_LEN)
        items = len(c.get("items", []))

        lines.append(f"**{i}. {title}**")
        lines.append(f"   By: {owner} | Upvotes: {upvotes} | Items: {items}")
        lines.append(f"   https://huggingface.co/collections/{slug}")
        if desc:
            lines.append(f"   {desc}")
        lines.append("")
    return "\n".join(lines)


def _format_collections_compact(collections: list) -> str:
    if not collections:
        return "## Collections\nNone found"
    lines = [f"## Collections ({len(collections)})"]
    for c in collections:
        title = c.get("title", "Untitled")
        owner = c.get("owner", {}).get("name", "")
        upvotes = c.get("upvotes", 0)
        lines.append(f"- **{title}** by {owner} ({upvotes} upvotes)")
    return "\n".join(lines)


def _format_s2_paper_list(papers: list[dict], title: str) -> str:
    lines = [f"# {title}", f"Showing {len(papers)} result(s)\n"]
    for i, paper in enumerate(papers, 1):
        ptitle = paper.get("title") or "(untitled)"
        year = paper.get("year") or "?"
        cites = paper.get("citationCount", 0)
        venue = paper.get("venue") or ""
        ext_ids = paper.get("externalIds") or {}
        aid = ext_ids.get("ArXiv", "")
        tldr = (paper.get("tldr") or {}).get("text", "")

        lines.append(f"### {i}. {ptitle}")
        meta = [f"Year: {year}", f"Citations: {cites}"]
        if venue:
            meta.append(f"Venue: {venue}")
        if aid:
            meta.append(f"arxiv_id: {aid}")
        lines.append(" | ".join(meta))
        if aid:
            lines.append(f"https://arxiv.org/abs/{aid}")
        if tldr:
            lines.append(f"**TL;DR:** {tldr}")
        lines.append("")
    lines.append(
        "Use paper_details with arxiv_id for full info, or read_paper for sections."
    )
    return "\n".join(lines)


def _format_citation_entry(entry: dict, show_context: bool = False) -> str:
    paper = entry.get("citingPaper") or entry.get("citedPaper") or {}
    title = paper.get("title") or "(untitled)"
    year = paper.get("year") or "?"
    cites = paper.get("citationCount", 0)
    ext_ids = paper.get("externalIds") or {}
    aid = ext_ids.get("ArXiv", "")
    influential = " **[influential]**" if entry.get("isInfluential") else ""

    parts = [f"- **{title}** ({year}, {cites} cites){influential}"]
    if aid:
        parts[0] += f"  arxiv:{aid}"
    if show_context:
        intents = entry.get("intents") or []
        if intents:
            parts.append(f"  Intent: {', '.join(intents)}")
        for ctx in (entry.get("contexts") or [])[:2]:
            if ctx:
                parts.append(f"  > {_truncate(ctx, 200)}")
    return "\n".join(parts)


def _format_citation_graph(
    arxiv_id: str, references: list[dict] | None, citations: list[dict] | None
) -> str:
    lines = [f"# Citation Graph for {arxiv_id}", f"https://arxiv.org/abs/{arxiv_id}\n"]
    if references is not None:
        lines.append(f"## References ({len(references)})")
        if references:
            for entry in references:
                lines.append(_format_citation_entry(entry))
        else:
            lines.append("No references found.")
        lines.append("")
    if citations is not None:
        lines.append(f"## Citations ({len(citations)})")
        if citations:
            for entry in citations:
                lines.append(_format_citation_entry(entry, show_context=True))
        else:
            lines.append("No citations found.")
        lines.append("")
    lines.append(
        "**Tip:** Use paper_details with an arxiv_id from above to dig deeper."
    )
    return "\n".join(lines)


def _format_snippets(snippets: list[dict], query: str) -> str:
    lines = [
        f"# Snippet Search: '{query}'",
        f"Found {len(snippets)} matching passage(s)\n",
    ]
    for i, item in enumerate(snippets, 1):
        paper = item.get("paper") or {}
        ptitle = paper.get("title") or "(untitled)"
        year = paper.get("year") or "?"
        cites = paper.get("citationCount", 0)
        ext_ids = paper.get("externalIds") or {}
        aid = ext_ids.get("ArXiv", "")
        snippet = item.get("snippet") or {}
        text = snippet.get("text", "")
        section = snippet.get("section") or ""

        lines.append(f"### {i}. {ptitle} ({year}, {cites} cites)")
        if aid:
            lines.append(f"arxiv:{aid}")
        if section:
            lines.append(f"Section: {section}")
        if text:
            lines.append(f"> {_truncate(text, 400)}")
        lines.append("")
    lines.append("Use paper_details or read_paper with arxiv_id to dig in.")
    return "\n".join(lines)


# ── operation handlers ────────────────────────────────────────────────


def _err(msg: str) -> tuple[str, bool]:
    return msg, False


def _arxiv_id(args: dict) -> str | None:
    return args.get("arxiv_id")


async def _s2_bulk_search(
    query: str, args: dict, limit: int
) -> tuple[str | None, list[dict]]:
    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "fields": "title,externalIds,year,citationCount,tldr,venue,publicationDate",
    }
    if args.get("date_from") or args.get("date_to"):
        params["publicationDateOrYear"] = (
            f"{args.get('date_from', '')}:{args.get('date_to', '')}"
        )
    if args.get("categories"):
        params["fieldsOfStudy"] = args["categories"]
    if args.get("min_citations"):
        params["minCitationCount"] = str(args["min_citations"])
    if args.get("sort_by") and args["sort_by"] != "relevance":
        params["sort"] = f"{args['sort_by']}:desc"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await _s2_request(
            client, "GET", "/graph/v1/paper/search/bulk", params=params
        )
        if not resp or resp.status_code != 200:
            return None, []
        data = resp.json()

    papers = data.get("data") or []
    if not papers:
        return f"No papers found for '{query}' with the given filters.", []

    structured = [
        {
            "title": p.get("title") or "(untitled)",
            "url": (
                f"{ARXIV_ABS}/{(p.get('externalIds') or {}).get('ArXiv')}"
                if (p.get("externalIds") or {}).get("ArXiv")
                else ""
            ),
            "snippet": (p.get("tldr") or {}).get("text") or "",
            "source": "semanticscholar.org",
            "year": p.get("year"),
            "citations": p.get("citationCount", 0),
            "arxiv_id": (p.get("externalIds") or {}).get("ArXiv"),
        }
        for p in papers[:limit]
    ]
    text = _format_s2_paper_list(
        papers[:limit], f"Papers matching '{query}' (Semantic Scholar)"
    )
    return text, structured


def _format_arxiv_results(query: str, entries: list[dict]) -> str:
    """Format arxiv search results as a markdown list for the agent's context."""
    if not entries:
        return f"No arxiv results for '{query}'."
    lines = [f"# arXiv search: {query}", f"_{len(entries)} result(s)_", ""]
    for i, e in enumerate(entries, 1):
        authors = ", ".join(e["authors"][:3])
        if len(e["authors"]) > 3:
            authors += f" (+{len(e['authors']) - 3} more)"
        lines.append(f"### {i}. {e['title']}")
        meta = [f"arxiv_id: **{e['arxiv_id']}**", f"published: {e['published']}"]
        if e["primary_category"]:
            meta.append(e["primary_category"])
        lines.append(" | ".join(meta))
        lines.append(e["abs_url"])
        if authors:
            lines.append(f"*{authors}*")
        if e["summary"]:
            lines.append(f"> {_truncate(e['summary'], 400)}")
        lines.append("")
    lines.append(
        "Next: paper_details for citation/TL;DR enrichment, read_paper for "
        "sectioned full text, or download_pdf to extract the raw PDF."
    )
    return "\n".join(lines)


def _arxiv_to_structured(entries: list[dict]) -> list[dict]:
    """Map arxiv entry dicts to the structured search-results shape used by
    the studio's search panel UI."""
    out = []
    for e in entries:
        authors = ", ".join(e["authors"][:3])
        if len(e["authors"]) > 3:
            authors += f" (+{len(e['authors']) - 3} more)"
        snippet = _truncate(e["summary"], MAX_SUMMARY_LEN)
        out.append(
            {
                "title": e["title"],
                "url": e["abs_url"],
                "snippet": snippet,
                "source": "arxiv.org",
                "arxiv_id": e["arxiv_id"],
                "year": (e["published"][:4] if e["published"] else None),
                "authors": authors,
                "primary_category": e["primary_category"],
            }
        )
    return out


async def _op_search(args: dict, limit: int) -> dict:
    """Returns dict with `text`, `ok`, `extra` keys. `extra.results` is the
    structured list rendered by the studio's search panel."""
    query = args.get("query")
    if not query:
        return {"text": "'query' is required for search.", "ok": False, "extra": {}}

    use_s2 = any(
        args.get(k)
        for k in ("date_from", "date_to", "categories", "min_citations", "sort_by")
    )
    if use_s2:
        text, structured = await _s2_bulk_search(query, args, limit)
        if text is not None:
            return {
                "text": text,
                "ok": True,
                "extra": {
                    "query": query,
                    "backend": "semanticscholar",
                    "results": structured,
                },
            }
        # S2 failure: fall through to arxiv with relevance sort

    sort_by = args.get("sort_by") or "relevance"
    if sort_by not in _ARXIV_SORT_MAP:
        sort_by = "relevance"
    try:
        entries = await _arxiv_search(query, limit, sort_by=sort_by)
    except Exception as e:
        return {"text": f"arxiv search failed: {e}", "ok": False, "extra": {}}
    return {
        "text": _format_arxiv_results(query, entries),
        "ok": True,
        "extra": {
            "query": query,
            "backend": "arxiv",
            "results": _arxiv_to_structured(entries),
        },
    }


async def _op_paper_details(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for paper_details.")
    arxiv_id = _strip_arxiv_id(arxiv_id)

    async with httpx.AsyncClient(timeout=15) as client:
        s2_task = _s2_get_json(
            client,
            f"/graph/v1/paper/{_s2_paper_id(arxiv_id)}",
            {
                "fields": "title,externalIds,year,citationCount,influentialCitationCount,"
                "tldr,venue,s2FieldsOfStudy"
            },
        )
        ax_task = _arxiv_get(arxiv_id)
        s2_data, ax = await asyncio.gather(s2_task, ax_task, return_exceptions=True)

    s2 = s2_data if isinstance(s2_data, dict) else None
    ax_dict = ax if isinstance(ax, dict) and ax else None

    if not ax_dict and not s2:
        return _err(f"Could not fetch paper {arxiv_id} from arxiv or Semantic Scholar.")

    # Build a paper dict shaped like _format_paper_detail expects (it was
    # written for HF Papers; we keep the same fields it reads).
    paper = {
        "id": arxiv_id,
        "title": (ax_dict and ax_dict["title"])
        or (s2 and s2.get("title"))
        or "(unknown)",
        "summary": (ax_dict and ax_dict["summary"]) or "",
        "authors": [{"name": n} for n in ((ax_dict and ax_dict["authors"]) or [])],
        "ai_summary": "",
        "ai_keywords": [],
        "githubRepo": "",
        "githubStars": 0,
        "upvotes": 0,
    }
    return _format_paper_detail(paper, s2), True


async def _op_read_paper(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for read_paper.")
    arxiv_id = _strip_arxiv_id(arxiv_id)

    section_query = args.get("section")
    parsed = None

    # 1) Try arxiv HTML / ar5iv (sectioned).
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for base in (ARXIV_HTML, AR5IV_HTML):
            try:
                resp = await client.get(f"{base}/{arxiv_id}")
                if resp.status_code == 200:
                    parsed = _parse_paper_html(resp.text)
                    if parsed["sections"]:
                        break
                    parsed = None
            except httpx.RequestError:
                continue

    if parsed and parsed["sections"]:
        if not section_query:
            return _format_read_paper_toc(parsed, arxiv_id), True
        section = _find_section(parsed["sections"], section_query)
        if not section:
            available = "\n".join(f"- {s['title']}" for s in parsed["sections"])
            return _err(f"Section '{section_query}' not found. Available:\n{available}")
        return _format_read_paper_section(section, arxiv_id), True

    # 2) Fallback: download the PDF and extract text. Loses section
    #    structure but keeps the actual content the user asked for.
    pdf = await _fetch_arxiv_pdf(arxiv_id)
    if pdf:
        try:
            text, n_pages = await asyncio.to_thread(_pdf_to_text, pdf)
            if text.strip():
                truncated = text[:MAX_PDF_TEXT_LEN]
                tail = (
                    ""
                    if len(text) <= MAX_PDF_TEXT_LEN
                    else (
                        f"\n\n... (truncated; PDF was {n_pages} pages, "
                        f"{len(text)} chars total — use download_pdf to save the full text)"
                    )
                )
                ax = await _arxiv_get(arxiv_id)
                title = (ax and ax["title"]) or arxiv_id
                msg = (
                    f"# {title}\n{ARXIV_ABS}/{arxiv_id}\n"
                    f"_HTML not available — extracted from PDF ({n_pages} pages)._\n\n"
                    f"{truncated}{tail}"
                )
                return msg, True
        except Exception as e:
            logger.warning("pdf extraction failed for %s: %s", arxiv_id, e)

    # 3) Last resort: arxiv abstract only.
    ax = await _arxiv_get(arxiv_id)
    if ax:
        msg = (
            f"# {ax['title']}\n{ax['abs_url']}\n\n"
            f"## Abstract\n{ax['summary']}\n\n"
            f"Full text not available (no HTML, PDF extraction failed).\n"
            f"PDF: {ax['pdf_url']}"
        )
        return msg, True
    return _err(f"Could not fetch paper {arxiv_id}. Check the arxiv ID.")


async def _op_download_pdf(args: dict, limit: int) -> dict:
    """Download the arxiv PDF, extract text, optionally save into the session
    workspace at /sessions/{session_id}/papers/{arxiv_id}.{pdf,txt} when a
    handler injects `_save_to_volume_async`. Returns extracted text (truncated)
    so the agent can immediately reason over it."""
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return {
            "text": "'arxiv_id' is required for download_pdf.",
            "ok": False,
            "extra": {},
        }
    arxiv_id = _strip_arxiv_id(arxiv_id)

    pdf = await _fetch_arxiv_pdf(arxiv_id)
    if not pdf:
        return {
            "text": f"Could not fetch PDF for arxiv:{arxiv_id}.",
            "ok": False,
            "extra": {},
        }

    try:
        text, n_pages = await asyncio.to_thread(_pdf_to_text, pdf)
    except Exception as e:
        return {
            "text": f"PDF for {arxiv_id} downloaded ({len(pdf)} bytes) "
            f"but text extraction failed: {e}",
            "ok": False,
            "extra": {},
        }

    save_path = None
    saver = args.get("_save_async")
    if callable(saver):
        try:
            save_path = await saver(arxiv_id, pdf, text)
        except Exception as e:
            logger.warning("download_pdf save failed for %s: %s", arxiv_id, e)

    truncated = text[:MAX_PDF_TEXT_LEN]
    tail = (
        ""
        if len(text) <= MAX_PDF_TEXT_LEN
        else (
            f"\n\n... (truncated at {MAX_PDF_TEXT_LEN} chars; "
            f"full text is {len(text)} chars)"
        )
    )
    head = [
        f"# arxiv:{arxiv_id} — extracted text",
        f"{ARXIV_ABS}/{arxiv_id}",
        f"_{n_pages} pages, {len(text)} chars{', saved to ' + save_path if save_path else ''}_",
        "",
    ]
    return {
        "text": "\n".join(head) + truncated + tail,
        "ok": True,
        "extra": {
            "arxiv_id": arxiv_id,
            "pages": n_pages,
            "chars": len(text),
            "saved_path": save_path,
        },
    }


async def _op_citation_graph(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for citation_graph.")

    direction = args.get("direction", "both")
    s2_id = _s2_paper_id(arxiv_id)
    fields = (
        "title,externalIds,year,citationCount,influentialCitationCount,"
        "contexts,intents,isInfluential"
    )
    params = {"fields": fields, "limit": limit}

    async with httpx.AsyncClient(timeout=15) as client:
        coros = []
        if direction in ("references", "both"):
            coros.append(
                _s2_get_json(client, f"/graph/v1/paper/{s2_id}/references", params)
            )
        if direction in ("citations", "both"):
            coros.append(
                _s2_get_json(client, f"/graph/v1/paper/{s2_id}/citations", params)
            )
        results = await asyncio.gather(*coros, return_exceptions=True)

        refs, cites = None, None
        idx = 0
        if direction in ("references", "both"):
            r = results[idx]
            refs = r.get("data", []) if isinstance(r, dict) else None
            idx += 1
        if direction in ("citations", "both"):
            r = results[idx]
            cites = r.get("data", []) if isinstance(r, dict) else None

    if refs is None and cites is None:
        return _err(
            f"Could not fetch citation data for {arxiv_id}. May not be indexed by S2."
        )
    return _format_citation_graph(arxiv_id, refs, cites), True


async def _op_snippet_search(args: dict, limit: int) -> tuple[str, bool]:
    query = args.get("query")
    if not query:
        return _err("'query' is required for snippet_search.")

    params: dict[str, Any] = {
        "query": query,
        "limit": limit,
        "fields": "title,externalIds,year,citationCount",
    }
    if args.get("date_from") or args.get("date_to"):
        params["publicationDateOrYear"] = (
            f"{args.get('date_from', '')}:{args.get('date_to', '')}"
        )
    if args.get("categories"):
        params["fieldsOfStudy"] = args["categories"]
    if args.get("min_citations"):
        params["minCitationCount"] = str(args["min_citations"])

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await _s2_request(
            client, "GET", "/graph/v1/snippet/search", params=params
        )
        if not resp or resp.status_code != 200:
            return _err("Snippet search failed. Semantic Scholar may be unavailable.")
        data = resp.json()

    snippets = data.get("data") or []
    if not snippets:
        return f"No snippets found for '{query}'.", True
    return _format_snippets(snippets, query), True


async def _op_recommend(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    positive_ids = args.get("positive_ids")
    if not arxiv_id and not positive_ids:
        return _err("'arxiv_id' or 'positive_ids' is required for recommend.")

    fields = "title,externalIds,year,citationCount,tldr,venue"
    async with httpx.AsyncClient(timeout=15) as client:
        if positive_ids and not arxiv_id:
            pos = [
                _s2_paper_id(p.strip()) for p in positive_ids.split(",") if p.strip()
            ]
            neg_raw = args.get("negative_ids", "")
            neg = (
                [_s2_paper_id(p.strip()) for p in neg_raw.split(",") if p.strip()]
                if neg_raw
                else []
            )
            resp = await _s2_request(
                client,
                "POST",
                "/recommendations/v1/papers/",
                json={"positivePaperIds": pos, "negativePaperIds": neg},
                params={"fields": fields, "limit": limit},
            )
            if not resp or resp.status_code != 200:
                return _err("Recommendation request failed.")
            data = resp.json()
        else:
            data = await _s2_get_json(
                client,
                f"/recommendations/v1/papers/forpaper/{_s2_paper_id(arxiv_id)}",
                {"fields": fields, "limit": limit, "from": "recent"},
            )
            if not data:
                return _err("Recommendation request failed.")

    papers = data.get("recommendedPapers") or []
    if not papers:
        return "No recommendations found.", True
    title = f"Recommended papers based on {arxiv_id or positive_ids}"
    return _format_s2_paper_list(papers[:limit], title), True


async def _op_find_datasets(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for find_datasets.")
    sort = args.get("sort", "downloads")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{HF_API}/datasets",
            params={
                "filter": f"arxiv:{arxiv_id}",
                "limit": limit,
                "sort": SORT_MAP.get(sort, "downloads"),
                "direction": -1,
            },
        )
        resp.raise_for_status()
        datasets = resp.json()
    if not datasets:
        return (
            f"No datasets found linked to paper {arxiv_id}.\n"
            f"https://huggingface.co/papers/{arxiv_id}",
            True,
        )
    return _format_datasets(datasets, arxiv_id, sort), True


async def _op_find_models(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for find_models.")
    sort = args.get("sort", "downloads")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{HF_API}/models",
            params={
                "filter": f"arxiv:{arxiv_id}",
                "limit": limit,
                "sort": SORT_MAP.get(sort, "downloads"),
                "direction": -1,
            },
        )
        resp.raise_for_status()
        models = resp.json()
    if not models:
        return (
            f"No models found linked to paper {arxiv_id}.\n"
            f"https://huggingface.co/papers/{arxiv_id}",
            True,
        )
    return _format_models(models, arxiv_id, sort), True


async def _op_find_collections(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for find_collections.")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{HF_API}/collections", params={"paper": arxiv_id})
        resp.raise_for_status()
        collections = resp.json()
    if not collections:
        return (
            f"No collections found containing paper {arxiv_id}.\n"
            f"https://huggingface.co/papers/{arxiv_id}",
            True,
        )
    return _format_collections(collections[:limit], arxiv_id), True


async def _op_find_all_resources(args: dict, limit: int) -> tuple[str, bool]:
    arxiv_id = _arxiv_id(args)
    if not arxiv_id:
        return _err("'arxiv_id' is required for find_all_resources.")
    per_cat = min(limit, 10)

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(
            client.get(
                f"{HF_API}/datasets",
                params={
                    "filter": f"arxiv:{arxiv_id}",
                    "limit": per_cat,
                    "sort": "downloads",
                    "direction": -1,
                },
            ),
            client.get(
                f"{HF_API}/models",
                params={
                    "filter": f"arxiv:{arxiv_id}",
                    "limit": per_cat,
                    "sort": "downloads",
                    "direction": -1,
                },
            ),
            client.get(f"{HF_API}/collections", params={"paper": arxiv_id}),
            return_exceptions=True,
        )

    sections = []
    if isinstance(results[0], Exception):
        sections.append(f"## Datasets\nError: {results[0]}")
    else:
        sections.append(_format_datasets_compact(results[0].json()[:per_cat]))
    if isinstance(results[1], Exception):
        sections.append(f"## Models\nError: {results[1]}")
    else:
        sections.append(_format_models_compact(results[1].json()[:per_cat]))
    if isinstance(results[2], Exception):
        sections.append(f"## Collections\nError: {results[2]}")
    else:
        sections.append(_format_collections_compact(results[2].json()[:per_cat]))

    header = (
        f"# Resources linked to paper {arxiv_id}\n"
        f"https://huggingface.co/papers/{arxiv_id}\n"
    )
    return header + "\n\n".join(sections), True


_OPERATIONS = {
    "search": _op_search,
    "paper_details": _op_paper_details,
    "read_paper": _op_read_paper,
    "download_pdf": _op_download_pdf,
    "citation_graph": _op_citation_graph,
    "snippet_search": _op_snippet_search,
    "recommend": _op_recommend,
    "find_datasets": _op_find_datasets,
    "find_models": _op_find_models,
    "find_collections": _op_find_collections,
    "find_all_resources": _op_find_all_resources,
}


def create_handler(session_id: str, publish_fn, **kwargs):
    """Factory: returns an async handler bound to (session_id, publish_fn)."""

    async def _save_pdf(arxiv_id: str, pdf_bytes: bytes, text: str) -> str | None:
        """Persist arxiv PDF + extracted text into the session workspace so
        the agent can re-read it later via list_session_files / read_session_file."""
        try:
            from services.volume import write_to_volume

            base = f"/sessions/{session_id}/papers"
            txt_path = f"{base}/{arxiv_id}.txt"
            await write_to_volume(text, txt_path)
            # PDFs aren't supported by write_to_volume's text path; emit a note
            # so the agent knows it has the extracted text but not the binary.
            return txt_path
        except Exception as e:
            logger.warning("could not save extracted text: %s", e)
            return None

    async def handler(args: dict):
        operation = args.get("operation") if isinstance(args, dict) else None
        if not operation:
            return {
                "content": [
                    {"type": "text", "text": "'operation' parameter is required."}
                ],
                "is_error": True,
            }
        op = _OPERATIONS.get(operation)
        if not op:
            valid = ", ".join(_OPERATIONS.keys())
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Unknown operation '{operation}'. Valid: {valid}",
                    }
                ],
                "is_error": True,
            }

        limit = min(int(args.get("limit", DEFAULT_LIMIT) or DEFAULT_LIMIT), MAX_LIMIT)

        # Pass a saver to download_pdf so it can persist text into the
        # session workspace. Other ops ignore _save_async.
        if operation == "download_pdf" and isinstance(args, dict):
            args = {**args, "_save_async": _save_pdf}

        await publish_fn(
            session_id,
            "tool_start",
            {
                "tool": "papers_search",
                "input": {
                    "operation": operation,
                    **{
                        k: v
                        for k, v in args.items()
                        if k != "operation" and not k.startswith("_")
                    },
                },
            },
            role="tool",
        )

        extra: dict[str, Any] = {}
        try:
            res = await op(args, limit)
            # Ops return either (text, ok) or {"text", "ok", "extra"}.
            if isinstance(res, dict):
                output = res.get("text", "")
                ok = bool(res.get("ok", True))
                extra = res.get("extra") or {}
            else:
                output, ok = res
        except httpx.HTTPStatusError as e:
            output, ok = (
                f"API error: {e.response.status_code} — {e.response.text[:200]}",
                False,
            )
        except httpx.RequestError as e:
            output, ok = f"Request error: {e}", False
        except Exception as e:
            logger.exception("papers_search %s failed", operation)
            output, ok = f"Error in {operation}: {e}", False

        # tool_end payload — `extra` adds structured fields (e.g. results
        # array for the search panel UI) without bloating the agent's text.
        end_data: dict[str, Any] = {
            "tool": "papers_search",
            "operation": operation,
            "output": output[:2000],
        }
        end_data.update(extra)
        await publish_fn(session_id, "tool_end", end_data, role="tool")

        result: dict[str, Any] = {"content": [{"type": "text", "text": output}]}
        if not ok:
            result["is_error"] = True
        return result

    return handler
