'use client';

import { useEffect, useState } from 'react';
import { CheckCircle2, ChevronRight, Code2, Loader2 } from 'lucide-react';
import type { ChatItem } from '@/lib/chatItems';

// ---------------------------------------------------------------------------
// Fun verbs for tool cards
// ---------------------------------------------------------------------------

const FUN_VERBS = [
  'Schlepping',
  'Noodling',
  'Crunching',
  'Wrangling',
  'Percolating',
  'Tinkering',
  'Brewing',
  'Conjuring',
  'Finagling',
  'Rummaging',
  'Simmering',
  'Whittling',
  'Pondering',
  'Juggling',
  'Untangling',
];

const PAST_VERBS = [
  'Schlepped',
  'Noodled',
  'Crunched',
  'Wrangled',
  'Percolated',
  'Tinkered',
  'Brewed',
  'Conjured',
  'Finagled',
  'Rummaged',
  'Simmered',
  'Whittled',
  'Pondered',
  'Juggled',
  'Untangled',
];

function useFunVerb(isAnimating: boolean) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * FUN_VERBS.length));
  useEffect(() => {
    if (!isAnimating) return;
    const id = setInterval(() => {
      setIndex((prev) => (prev + 1) % FUN_VERBS.length);
    }, 10000);
    return () => clearInterval(id);
  }, [isAnimating]);
  return FUN_VERBS[index];
}

// ---------------------------------------------------------------------------
// CollapsibleToolCard -- amber themed tool execution card
// ---------------------------------------------------------------------------

export default function CollapsibleToolCard({
  item,
  inline,
}: {
  item: ChatItem;
  inline?: boolean;
}) {
  const isStart = item.type === 'tool_start';
  const [collapsed, setCollapsed] = useState(true);
  const funVerb = useFunVerb(isStart);
  const [doneLabel] = useState(() => PAST_VERBS[Math.floor(Math.random() * PAST_VERBS.length)]);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!isStart) return;
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - item.timestamp) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [isStart, item.timestamp]);

  const card = (
    <div
      className={`${inline ? '' : 'max-w-[85%] '}rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border overflow-hidden`}
    >
      <button
        type="button"
        aria-expanded={!collapsed}
        className={`w-full flex items-center gap-2 ${inline ? 'px-3 py-1.5' : 'px-4 py-2.5'} cursor-pointer select-none text-left`}
        onClick={() => setCollapsed((prev) => !prev)}
      >
        {isStart ? (
          <Loader2
            className={`${inline ? 'w-3 h-3' : 'w-3.5 h-3.5'} text-amber-400 animate-spin`}
          />
        ) : (
          <CheckCircle2 className={`${inline ? 'w-3 h-3' : 'w-3.5 h-3.5'} text-green-400`} />
        )}
        <span className={`${inline ? 'text-xs' : 'text-sm'} text-gray-300 flex-1`}>
          {isStart
            ? `${funVerb}...${elapsed > 0 ? ` ${elapsed}s` : ''}`
            : item.meta?.duration
              ? `${doneLabel} for ${item.meta.duration}s`
              : doneLabel}
        </span>
        <ChevronRight
          className={`w-3.5 h-3.5 text-gray-500 transition-transform duration-150 ${
            !collapsed ? 'rotate-90' : ''
          }`}
        />
      </button>
      {!collapsed && (
        <>
          {item.meta?.code && (
            <pre className="px-4 py-2 text-xs text-gray-400 font-mono max-h-24 overflow-y-auto border-t border-surface-border whitespace-pre-wrap">
              {item.meta.code.length > 300 ? item.meta.code.slice(0, 300) + '...' : item.meta.code}
            </pre>
          )}
          {(item.meta?.outputs?.length ?? 0) > 0 && (
            <div className="px-4 py-2 border-t border-surface-border max-h-32 overflow-y-auto">
              {item.meta?.outputs?.map((o, i) => (
                <pre
                  key={i}
                  className={`text-xs font-mono whitespace-pre-wrap break-all ${
                    o.stream === 'stderr' ? 'text-red-400/70' : 'text-gray-500'
                  }`}
                >
                  {o.text}
                </pre>
              ))}
            </div>
          )}
          {item.meta?.output && (
            <pre className="px-4 py-2 text-xs text-green-400/80 font-mono max-h-32 overflow-y-auto border-t border-surface-border whitespace-pre-wrap">
              {item.meta.output.length > 500
                ? item.meta.output.slice(0, 500) + '...'
                : item.meta.output}
            </pre>
          )}
        </>
      )}
    </div>
  );

  if (inline) return card;

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-amber-500/20">
        <Code2 className="w-3.5 h-3.5 text-amber-400" />
      </div>
      {card}
    </div>
  );
}
