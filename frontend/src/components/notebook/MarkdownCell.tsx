'use client';

import { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { MarkdownCell as MarkdownCellT } from '@/lib/notebook/types';
import { sourceToString } from '@/lib/notebook/types';
import { notebookApi } from '@/lib/notebook/api';

interface Props {
  cell: MarkdownCellT;
  sessionId: string;
  onChange: (source: string) => void;
}

function makeImageResolver(sessionId: string) {
  return function resolveSrc(raw: string | undefined): string | undefined {
    if (!raw) return raw;
    if (/^(https?:|data:|blob:|\/api\/)/i.test(raw)) return raw;
    if (raw.startsWith('/sessions/') || raw.startsWith('/datasets/')) {
      return notebookApi.rawFileUrl(raw);
    }
    const trimmed = raw.replace(/^\.?\//, '').replace(/^\/+/, '');
    const absolute = `/sessions/${sessionId}/${trimmed}`;
    return notebookApi.rawFileUrl(absolute);
  };
}

// The project doesn't have @tailwindcss/typography installed, so `prose`
// classes are no-ops. Explicit per-element styling gives us control anyway.
const MD_COMPONENTS = (resolveSrc: (s?: string) => string | undefined) => ({
  h1: (p: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h1 {...p} className="mb-3 mt-1 border-b border-neutral-800 pb-1 text-2xl font-semibold text-white" />
  ),
  h2: (p: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h2 {...p} className="mb-2 mt-4 text-xl font-semibold text-white" />
  ),
  h3: (p: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h3 {...p} className="mb-2 mt-3 text-lg font-semibold text-neutral-100" />
  ),
  h4: (p: React.HTMLAttributes<HTMLHeadingElement>) => (
    <h4 {...p} className="mb-1 mt-2 text-base font-semibold text-neutral-100" />
  ),
  p: (p: React.HTMLAttributes<HTMLParagraphElement>) => (
    <p {...p} className="my-2 text-[14px] leading-relaxed text-neutral-200" />
  ),
  ul: (p: React.HTMLAttributes<HTMLUListElement>) => (
    <ul {...p} className="my-2 list-disc space-y-1 pl-6 text-[14px] text-neutral-200 marker:text-neutral-500" />
  ),
  ol: (p: React.HTMLAttributes<HTMLOListElement>) => (
    <ol {...p} className="my-2 list-decimal space-y-1 pl-6 text-[14px] text-neutral-200 marker:text-neutral-500" />
  ),
  li: (p: React.HTMLAttributes<HTMLLIElement>) => (
    <li {...p} className="leading-relaxed" />
  ),
  strong: (p: React.HTMLAttributes<HTMLElement>) => (
    <strong {...p} className="font-semibold text-white" />
  ),
  em: (p: React.HTMLAttributes<HTMLElement>) => (
    <em {...p} className="italic text-neutral-200" />
  ),
  a: (p: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      {...p}
      target="_blank"
      rel="noreferrer noopener"
      className="text-sky-400 underline decoration-sky-700 hover:text-sky-300"
    />
  ),
  code: (p: React.HTMLAttributes<HTMLElement> & { inline?: boolean }) => {
    const { inline, className, ...rest } = p;
    if (inline ?? true) {
      return (
        <code
          {...rest}
          className={`rounded bg-neutral-800 px-1.5 py-0.5 font-mono text-[12.5px] text-sky-300 ${className || ''}`}
        />
      );
    }
    return <code {...rest} className={className} />;
  },
  pre: (p: React.HTMLAttributes<HTMLPreElement>) => (
    <pre
      {...p}
      className="my-3 overflow-x-auto rounded-md border border-neutral-800 bg-neutral-950 p-3 font-mono text-[12.5px] leading-relaxed text-neutral-200"
    />
  ),
  blockquote: (p: React.HTMLAttributes<HTMLQuoteElement>) => (
    <blockquote {...p} className="my-3 border-l-2 border-sky-700 pl-3 italic text-neutral-300" />
  ),
  hr: () => <hr className="my-4 border-neutral-800" />,
  table: (p: React.HTMLAttributes<HTMLTableElement>) => (
    <div className="my-3 overflow-x-auto">
      <table {...p} className="min-w-full border-collapse text-[13px]" />
    </div>
  ),
  th: (p: React.HTMLAttributes<HTMLTableHeaderCellElement>) => (
    <th {...p} className="border border-neutral-800 bg-neutral-900 px-2 py-1 text-left text-neutral-200" />
  ),
  td: (p: React.HTMLAttributes<HTMLTableDataCellElement>) => (
    <td {...p} className="border border-neutral-800 px-2 py-1 text-neutral-300" />
  ),
  img: (p: React.ImgHTMLAttributes<HTMLImageElement>) => (
    // Dynamic session-workspace paths — next/image can't handle them.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      {...p}
      src={resolveSrc(p.src as string | undefined)}
      alt={p.alt || ''}
      className="my-3 max-w-full rounded-md border border-neutral-800 shadow-sm"
    />
  ),
});

export default function MarkdownCell({ cell, sessionId, onChange }: Props) {
  const [editing, setEditing] = useState(false);
  const src = sourceToString(cell.source);
  const resolveSrc = useMemo(() => makeImageResolver(sessionId), [sessionId]);
  const components = useMemo(() => MD_COMPONENTS(resolveSrc), [resolveSrc]);

  if (editing) {
    return (
      <div className="overflow-hidden rounded-lg border border-sky-700/60 bg-neutral-900">
        <textarea
          autoFocus
          value={src}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => setEditing(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.shiftKey) {
              e.preventDefault();
              setEditing(false);
            }
          }}
          className="h-40 w-full resize-y border-0 bg-neutral-900 px-4 py-3 font-mono text-sm leading-relaxed text-neutral-100 outline-none"
          placeholder="# Markdown here — Shift+Enter to commit"
        />
      </div>
    );
  }

  return (
    <div
      onDoubleClick={() => setEditing(true)}
      className="group cursor-text rounded-lg border border-transparent px-4 py-2 hover:border-neutral-800/80"
      title="Double-click to edit"
    >
      {src.trim() ? (
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {src}
        </ReactMarkdown>
      ) : (
        <div className="italic text-neutral-500">Double-click to edit markdown…</div>
      )}
    </div>
  );
}
