'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ChevronDown, Cpu, Database, DollarSign, Sparkles } from 'lucide-react';
import type { UsageEvent } from '@/lib/types';

export interface UsageTotals {
  cost_usd: number;
  llm_cost_usd: number;
  compute_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  llm_calls: number;
  sandbox_seconds: number;
  compute_runs: number;
}

interface Props {
  totals: UsageTotals;
  recent?: UsageEvent[];
}

const ZERO: UsageTotals = {
  cost_usd: 0,
  llm_cost_usd: 0,
  compute_cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
  llm_calls: 0,
  sandbox_seconds: 0,
  compute_runs: 0,
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatCost(c: number): string {
  if (c < 0.01) return `<$0.01`;
  if (c < 1) return `$${c.toFixed(3)}`;
  return `$${c.toFixed(2)}`;
}

export default function CostBadge({ totals = ZERO, recent = [] }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // "Cache hit" denominator includes everything that contributed to input
  // billing this session: fresh input + cache-creation writes (paid 1.25× of
  // input) + cache reads (paid 0.10× of input). The numerator is just reads.
  // This way "100%" only happens when we literally only read cache and never
  // wrote / sent fresh tokens — which is rare and meaningful.
  const totalInputCharged =
    totals.input_tokens +
    totals.cache_read_input_tokens +
    totals.cache_creation_input_tokens;
  const cacheHit =
    totalInputCharged > 0
      ? (totals.cache_read_input_tokens / totalInputCharged) * 100
      : 0;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg transition-colors text-xs hover:bg-white/[0.06] text-gray-400"
        title="Session cost & token usage"
      >
        <DollarSign className="w-3 h-3 text-emerald-400" />
        <span className="tabular-nums font-medium text-gray-300">
          {formatCost(totals.cost_usd)}
        </span>
        <ChevronDown
          className={`w-3 h-3 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-80 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
          <div className="px-3 py-2 border-b border-white/[0.06]">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
              Session usage
            </span>
          </div>

          <div className="px-3 py-3 space-y-3">
            <Stat
              label="Total cost"
              value={formatCost(totals.cost_usd)}
              icon={<DollarSign className="w-3 h-3 text-emerald-400" />}
            />

            <div className="space-y-1.5">
              <Stat
                label="LLM (tokens)"
                value={formatCost(totals.llm_cost_usd)}
                hint={`${totals.llm_calls} calls`}
                icon={<Sparkles className="w-3 h-3 text-violet-400" />}
              />
              <TokenBreakdown
                fresh={totals.input_tokens}
                cacheRead={totals.cache_read_input_tokens}
                cacheWrite={totals.cache_creation_input_tokens}
                output={totals.output_tokens}
              />
            </div>

            <Stat
              label="Compute (infra)"
              value={formatCost(totals.compute_cost_usd)}
              hint={
                totals.compute_runs > 0 || totals.sandbox_seconds > 0
                  ? `${totals.compute_runs} runs · ${totals.sandbox_seconds.toFixed(1)}s`
                  : 'no runs yet'
              }
              icon={<Cpu className="w-3 h-3 text-emerald-400" />}
            />

            {(totals.cache_read_input_tokens > 0 ||
              totals.cache_creation_input_tokens > 0) && (
              <Stat
                label="Cache hit"
                value={`${cacheHit.toFixed(0)}%`}
                hint={`${formatTokens(totals.cache_read_input_tokens)} read of ${formatTokens(totalInputCharged)} input total`}
                icon={<Database className="w-3 h-3 text-violet-400" />}
              />
            )}
          </div>

          <div className="px-3 py-2 border-t border-white/[0.06] flex items-center justify-between">
            <Link
              href="/usage"
              className="text-[11px] text-gray-400 hover:text-gray-200 transition-colors"
              onClick={() => setOpen(false)}
            >
              Full usage report →
            </Link>
            <span className="text-[10px] text-gray-600">est. pricing</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 w-4 h-4 flex items-center justify-center">{icon}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[11px] text-gray-500">{label}</span>
          <span className="text-xs font-medium text-gray-200 tabular-nums">{value}</span>
        </div>
        {hint && <div className="text-[10px] text-gray-600 mt-0.5">{hint}</div>}
      </div>
    </div>
  );
}

/**
 * Sub-rows under "LLM (tokens)" showing the four token buckets that
 * combine to make the LLM cost: fresh input, cache reads (cheap),
 * cache writes (expensive), output. Empty buckets are hidden.
 */
function TokenBreakdown({
  fresh,
  cacheRead,
  cacheWrite,
  output,
}: {
  fresh: number;
  cacheRead: number;
  cacheWrite: number;
  output: number;
}) {
  const rows: Array<{ label: string; value: number; color: string }> = [];
  if (fresh > 0) rows.push({ label: 'fresh input', value: fresh, color: 'text-gray-400' });
  if (cacheRead > 0)
    rows.push({ label: 'cache read', value: cacheRead, color: 'text-violet-300' });
  if (cacheWrite > 0)
    rows.push({ label: 'cache write', value: cacheWrite, color: 'text-amber-300' });
  if (output > 0) rows.push({ label: 'output', value: output, color: 'text-emerald-300' });

  if (rows.length === 0) return null;

  return (
    <div className="ml-6 pl-3 border-l border-white/[0.06] space-y-0.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-baseline justify-between gap-2 text-[10px]">
          <span className="text-gray-600">{r.label}</span>
          <span className={`tabular-nums ${r.color}`}>{formatTokens(r.value)}</span>
        </div>
      ))}
    </div>
  );
}
