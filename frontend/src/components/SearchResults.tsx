'use client';

import { useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink, Search } from 'lucide-react';
import { SearchResult } from '@/lib/types';

const COMPACT_VISIBLE = 4;

function FaviconBadge({ source }: { source?: string }) {
  // Free favicon endpoint, no auth. Falls back to the source initial if it
  // doesn't load (most domains do load).
  if (!source) {
    return (
      <span className="w-4 h-4 rounded-sm bg-white/[0.08] text-gray-500 flex items-center justify-center text-[9px] uppercase shrink-0">
        ?
      </span>
    );
  }
  const url = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(source)}&sz=32`;
  // Plain <img> on purpose: favicons are tiny external assets and using
  // next/image would require allowlisting the domain in next.config.
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={url}
      alt=""
      className="w-4 h-4 rounded-sm shrink-0 bg-white/[0.04]"
      onError={(e) => {
        (e.currentTarget as HTMLImageElement).style.visibility = 'hidden';
      }}
    />
  );
}

function ResultCard({ r, index }: { r: SearchResult; index: number }) {
  return (
    <a
      href={r.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex flex-col gap-1.5 p-3 rounded-lg bg-white/[0.02] hover:bg-white/[0.05] border border-white/[0.05] hover:border-primary-500/30 transition-colors min-w-0"
    >
      <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
        <span className="text-gray-600 tabular-nums">{index + 1}.</span>
        <FaviconBadge source={r.source} />
        <span className="truncate">{r.source || new URL(r.url || 'http://x').hostname}</span>
        <ExternalLink className="w-3 h-3 ml-auto text-gray-700 group-hover:text-primary-400 shrink-0" />
      </div>
      <div className="text-sm font-medium text-gray-100 group-hover:text-white line-clamp-2 leading-snug">
        {r.title}
      </div>
      {r.snippet && (
        <div className="text-[12px] text-gray-400 line-clamp-3 leading-relaxed">
          {r.snippet}
        </div>
      )}
      {(r.arxiv_id || r.year || typeof r.citations === 'number') && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-gray-500 pt-0.5">
          {r.arxiv_id && (
            <span className="text-violet-400/80">arxiv:{r.arxiv_id}</span>
          )}
          {r.year != null && <span>{r.year}</span>}
          {typeof r.citations === 'number' && r.citations > 0 && (
            <span>{r.citations.toLocaleString()} cites</span>
          )}
          {r.primary_category && <span className="text-gray-600">{r.primary_category}</span>}
        </div>
      )}
    </a>
  );
}

interface Props {
  query: string;
  backend?: string;
  results: SearchResult[];
  // Display label — "Searched" for web, "Papers" for arxiv etc. Optional.
  label?: string;
}

export default function SearchResults({ query, backend, results, label }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (!results.length) {
    return (
      <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] p-3 text-sm text-gray-500">
        <div className="flex items-center gap-2 mb-1 text-gray-400">
          <Search className="w-3.5 h-3.5" />
          <span>{label || 'Searched'}: &ldquo;{query}&rdquo;</span>
        </div>
        No results found.
      </div>
    );
  }

  const visible = expanded ? results : results.slice(0, COMPACT_VISIBLE);
  const more = results.length - COMPACT_VISIBLE;

  return (
    <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.04] text-[12px]">
        <Search className="w-3.5 h-3.5 text-gray-500" />
        <span className="text-gray-400">{label || 'Searched'}:</span>
        <span className="text-gray-100 font-medium truncate">{query}</span>
        <span className="ml-auto text-[10px] text-gray-600 shrink-0">
          {results.length} result{results.length === 1 ? '' : 's'}
          {backend && <span className="ml-1.5 text-gray-700">via {backend}</span>}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
        {visible.map((r, i) => (
          <ResultCard key={`${r.url}-${i}`} r={r} index={i} />
        ))}
      </div>

      {more > 0 && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-full flex items-center justify-center gap-1 py-1.5 text-[11px] text-gray-500 hover:text-gray-300 hover:bg-white/[0.03] border-t border-white/[0.04]"
        >
          {expanded ? (
            <>
              <ChevronUp className="w-3 h-3" /> Show less
            </>
          ) : (
            <>
              <ChevronDown className="w-3 h-3" /> Show {more} more
            </>
          )}
        </button>
      )}
    </div>
  );
}
