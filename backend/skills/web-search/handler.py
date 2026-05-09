"""web_search tool — general web search across multiple backends.

Backends, in priority order:
1. Tavily   — if TAVILY_API_KEY is set. LLM-tuned, returns clean snippets.
2. Brave    — if BRAVE_API_KEY is set. High quality, JSON API.
3. DuckDuckGo (HTML) — no key required, fragile but free.

Emits a structured `results` array on the tool_end SSE event so the
frontend can render rich cards (ChatGPT-style search panel) instead of
just the markdown text. The text output is also returned for the agent's
context.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse, parse_qs, unquote

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 8
MAX_LIMIT = 25
MAX_SNIPPET_LEN = 300

TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
BRAVE_KEY = os.environ.get("BRAVE_API_KEY")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _truncate(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


# ── backends ──────────────────────────────────────────────────────────


async def _search_tavily(query: str, limit: int) -> list[dict] | None:
    """LLM-tuned search, requires TAVILY_API_KEY."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_KEY,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                    "include_answer": False,
                },
            )
            if resp.status_code != 200:
                logger.warning("tavily returned %s", resp.status_code)
                return None
            data = resp.json()
        return [
            {
                "title": r.get("title", "(untitled)"),
                "url": r.get("url", ""),
                "snippet": _truncate(r.get("content") or "", MAX_SNIPPET_LEN),
                "source": _domain(r.get("url", "")),
                "backend": "tavily",
            }
            for r in (data.get("results") or [])
        ]
    except Exception as e:
        logger.warning("tavily search failed: %s", e)
        return None


async def _search_brave(query: str, limit: int) -> list[dict] | None:
    """High-quality JSON search, requires BRAVE_API_KEY."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(limit, 20)},
                headers={
                    "X-Subscription-Token": BRAVE_KEY or "",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                logger.warning("brave returned %s", resp.status_code)
                return None
            data = resp.json()
        web = data.get("web") or {}
        return [
            {
                "title": r.get("title", "(untitled)"),
                "url": r.get("url", ""),
                "snippet": _truncate(
                    re.sub(r"<[^>]+>", "", r.get("description") or ""), MAX_SNIPPET_LEN
                ),
                "source": _domain(r.get("url", "")),
                "backend": "brave",
            }
            for r in (web.get("results") or [])
        ]
    except Exception as e:
        logger.warning("brave search failed: %s", e)
        return None


def _ddg_unwrap(href: str) -> str:
    """DDG result links are wrapped: //duckduckgo.com/l/?uddg=<encoded>&...
    Unwrap to the real URL."""
    if href.startswith("//"):
        href = "https:" + href
    if "/l/?uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            return unquote(qs.get("uddg", [href])[0])
        except Exception:
            return href
    return href


async def _search_ddg(query: str, limit: int) -> list[dict] | None:
    """No-key fallback. Scrapes DuckDuckGo's HTML SERP. Fragile to template
    changes — when DDG breaks, set a real key (Tavily/Brave) instead of
    fixing this scraper."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
                headers={"User-Agent": USER_AGENT},
            )
            if resp.status_code != 200:
                logger.warning("ddg returned %s", resp.status_code)
                return None
            html = resp.text
    except Exception as e:
        logger.warning("ddg search failed: %s", e)
        return None

    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    for block in soup.find_all("div", class_="result"):
        a = block.find("a", class_="result__a")
        if not a:
            continue
        href = _ddg_unwrap(a.get("href", ""))
        title = a.get_text(strip=True)
        snippet_el = block.find("a", class_="result__snippet") or block.find(
            "div", class_="result__snippet"
        )
        snippet = snippet_el.get_text(separator=" ", strip=True) if snippet_el else ""
        if href and title:
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": _truncate(snippet, MAX_SNIPPET_LEN),
                    "source": _domain(href),
                    "backend": "duckduckgo",
                }
            )
        if len(results) >= limit:
            break
    return results or None


async def _do_search(query: str, limit: int) -> tuple[list[dict], str]:
    """Run search via best available backend. Returns (results, backend_name)."""
    if TAVILY_KEY:
        r = await _search_tavily(query, limit)
        if r is not None:
            return r, "tavily"
    if BRAVE_KEY:
        r = await _search_brave(query, limit)
        if r is not None:
            return r, "brave"
    r = await _search_ddg(query, limit)
    return (r or []), "duckduckgo"


# ── output formatting (for the agent's text context) ──────────────────


def _format(query: str, results: list[dict], backend: str) -> str:
    if not results:
        return f"No web results for '{query}' (backend={backend})."
    lines = [f"# Web search: {query}", f"_via {backend}, {len(results)} result(s)_", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"### {i}. {r['title']}")
        lines.append(f"{r['url']}")
        if r.get("source"):
            lines.append(f"*{r['source']}*")
        if r.get("snippet"):
            lines.append(f"> {r['snippet']}")
        lines.append("")
    return "\n".join(lines)


# ── tool factory ──────────────────────────────────────────────────────


def create_handler(session_id: str, publish_fn, **kwargs):
    async def handler(args: dict):
        if not isinstance(args, dict):
            return _err("args must be a dict")
        query = (args.get("query") or "").strip()
        if not query:
            return _err("'query' is required")
        limit = max(1, min(int(args.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))

        await publish_fn(
            session_id,
            "tool_start",
            {"tool": "web_search", "input": {"query": query, "limit": limit}},
            role="tool",
        )

        try:
            results, backend = await _do_search(query, limit)
        except Exception as e:
            logger.exception("web_search failed")
            text = f"Search error: {e}"
            await publish_fn(
                session_id,
                "tool_end",
                {"tool": "web_search", "output": text, "query": query, "results": []},
                role="tool",
            )
            return {"content": [{"type": "text", "text": text}], "is_error": True}

        text = _format(query, results, backend)
        # Structured payload for the rich UI; agent gets the markdown text.
        await publish_fn(
            session_id,
            "tool_end",
            {
                "tool": "web_search",
                "output": text[:2000],
                "query": query,
                "backend": backend,
                "results": results,
            },
            role="tool",
        )
        return {"content": [{"type": "text", "text": text}]}

    return handler


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}
