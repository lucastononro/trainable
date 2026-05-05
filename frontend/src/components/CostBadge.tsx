'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ChevronDown, DollarSign, Database, Zap } from 'lucide-react';
import type { UsageEvent } from '@/lib/types';

export interface UsageTotals {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  llm_calls: number;
  sandbox_seconds: number;
}

interface Props {
  totals: UsageTotals;
  recent?: UsageEvent[];
}

const ZERO: UsageTotals = {
  cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_input_tokens: 0,
  llm_calls: 0,
  sandbox_seconds: 0,
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

  const totalInput = totals.input_tokens + totals.cache_read_input_tokens;
  const cacheHit = totalInput > 0 ? (totals.cache_read_input_tokens / totalInput) * 100 : 0;

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
            <Stat label="Total cost" value={formatCost(totals.cost_usd)} icon="$" />
            <Stat
              label="LLM calls"
              value={`${totals.llm_calls}`}
              hint={`${formatTokens(totals.input_tokens)} in · ${formatTokens(totals.output_tokens)} out`}
              icon={<Zap className="w-3 h-3 text-amber-400" />}
            />
            {totals.cache_read_input_tokens > 0 && (
              <Stat
                label="Cache hit"
                value={`${cacheHit.toFixed(0)}%`}
                hint={`${formatTokens(totals.cache_read_input_tokens)} cached input tokens`}
                icon={<Database className="w-3 h-3 text-violet-400" />}
              />
            )}
            {totals.sandbox_seconds > 0 && (
              <Stat
                label="Compute"
                value={`${totals.sandbox_seconds.toFixed(0)}s`}
                hint={`${recent.filter((e) => e.kind === 'sandbox').length} runs`}
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
