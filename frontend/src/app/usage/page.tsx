'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, RefreshCw } from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import type { UsageSummary } from '@/lib/types';

const AGENT_COLORS = [
  '#a78bfa', // violet
  '#60a5fa', // blue
  '#fbbf24', // amber
  '#34d399', // emerald
  '#fb923c', // orange
  '#f472b6', // rose
  '#22d3ee', // cyan
  '#9ca3af', // gray
];

function formatCost(c: number): string {
  if (c < 0.01) return `<$0.01`;
  if (c < 1) return `$${c.toFixed(3)}`;
  return `$${c.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function UsagePage() {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.usageSummary();
      setSummary(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return (
    <div className="min-h-screen bg-black text-gray-200">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.08]">
        <Link
          href="/"
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400"
          title="Back"
        >
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-sm font-semibold text-white">Usage & cost</h1>
        <div className="flex-1" />
        <button
          onClick={refresh}
          disabled={loading}
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400 disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-8">
        {error && (
          <div className="px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300">
            Failed to load: {error}
          </div>
        )}

        {!summary && !error && (
          <div className="text-center text-xs text-gray-600 py-12">Loading…</div>
        )}

        {summary && (
          <>
            <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Tile label="Total cost" value={formatCost(summary.totals.cost_usd)} />
              <Tile label="LLM calls" value={String(summary.totals.llm_calls)} />
              <Tile
                label="Tokens"
                value={`${formatTokens(summary.totals.input_tokens)} / ${formatTokens(summary.totals.output_tokens)}`}
                hint="input / output"
              />
              <Tile
                label="Cache hit"
                value={
                  summary.totals.input_tokens + summary.totals.cache_read_input_tokens > 0
                    ? `${(
                        (summary.totals.cache_read_input_tokens /
                          (summary.totals.input_tokens + summary.totals.cache_read_input_tokens)) *
                          100
                      ).toFixed(0)}%`
                    : '—'
                }
                hint={`${formatTokens(summary.totals.cache_read_input_tokens)} cached`}
              />
            </section>

            <section className="space-y-2">
              <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                Cost by day
              </h2>
              <div className="h-56 bg-white/[0.02] border border-white/[0.06] rounded-xl px-3 py-2">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={summary.by_day}>
                    <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
                    <XAxis dataKey="date" stroke="#6b7280" fontSize={10} />
                    <YAxis
                      stroke="#6b7280"
                      fontSize={10}
                      tickFormatter={(v) => `$${v.toFixed(2)}`}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#000',
                        border: '1px solid rgba(255,255,255,0.1)',
                        fontSize: '12px',
                      }}
                      formatter={(v: number) => formatCost(v)}
                    />
                    <Line
                      dataKey="cost_usd"
                      stroke="#34d399"
                      strokeWidth={2}
                      dot={{ r: 3 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  By agent
                </h2>
                <div className="h-64 bg-white/[0.02] border border-white/[0.06] rounded-xl px-3 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={summary.by_agent}>
                      <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
                      <XAxis dataKey="agent" stroke="#6b7280" fontSize={10} />
                      <YAxis
                        stroke="#6b7280"
                        fontSize={10}
                        tickFormatter={(v) => `$${v.toFixed(2)}`}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: '#000',
                          border: '1px solid rgba(255,255,255,0.1)',
                          fontSize: '12px',
                        }}
                        formatter={(v: number) => formatCost(v)}
                      />
                      <Bar dataKey="cost_usd">
                        {summary.by_agent.map((_, i) => (
                          <Cell key={i} fill={AGENT_COLORS[i % AGENT_COLORS.length]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="space-y-2">
                <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  By model
                </h2>
                <div className="h-64 bg-white/[0.02] border border-white/[0.06] rounded-xl px-3 py-2">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={summary.by_model}>
                      <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
                      <XAxis dataKey="model" stroke="#6b7280" fontSize={9} angle={-15} textAnchor="end" height={50} />
                      <YAxis
                        stroke="#6b7280"
                        fontSize={10}
                        tickFormatter={(v) => `$${v.toFixed(2)}`}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: '#000',
                          border: '1px solid rgba(255,255,255,0.1)',
                          fontSize: '12px',
                        }}
                        formatter={(v: number) => formatCost(v)}
                      />
                      <Legend wrapperStyle={{ fontSize: '11px' }} />
                      <Bar dataKey="cost_usd" name="cost" fill="#60a5fa" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </section>

            <section className="space-y-2">
              <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                Recent events ({summary.events.length})
              </h2>
              <div className="overflow-x-auto bg-white/[0.02] border border-white/[0.06] rounded-xl">
                <table className="w-full text-xs">
                  <thead className="text-[10px] uppercase tracking-wider text-gray-500">
                    <tr className="border-b border-white/[0.06]">
                      <th className="px-3 py-2 text-left">When</th>
                      <th className="px-3 py-2 text-left">Kind</th>
                      <th className="px-3 py-2 text-left">Agent</th>
                      <th className="px-3 py-2 text-left">Model</th>
                      <th className="px-3 py-2 text-right">In</th>
                      <th className="px-3 py-2 text-right">Out</th>
                      <th className="px-3 py-2 text-right">Cache</th>
                      <th className="px-3 py-2 text-right">Sandbox</th>
                      <th className="px-3 py-2 text-right">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.events
                      .slice()
                      .reverse()
                      .slice(0, 100)
                      .map((ev) => (
                        <tr key={ev.id} className="border-b border-white/[0.04]">
                          <td className="px-3 py-1.5 text-gray-500 tabular-nums whitespace-nowrap">
                            {ev.created_at?.replace('T', ' ').slice(0, 19)}
                          </td>
                          <td className="px-3 py-1.5">{ev.kind}</td>
                          <td className="px-3 py-1.5 text-gray-400">{ev.agent_type ?? '—'}</td>
                          <td className="px-3 py-1.5 text-gray-400 truncate max-w-[180px]">
                            {ev.model ?? ev.provider ?? '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums">
                            {ev.input_tokens ? formatTokens(ev.input_tokens) : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums">
                            {ev.output_tokens ? formatTokens(ev.output_tokens) : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-violet-400">
                            {ev.cache_read_input_tokens
                              ? formatTokens(ev.cache_read_input_tokens)
                              : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-gray-400">
                            {ev.sandbox_seconds ? `${ev.sandbox_seconds.toFixed(1)}s` : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right tabular-nums">
                            {formatCost(ev.cost_usd)}
                          </td>
                        </tr>
                      ))}
                    {summary.events.length === 0 && (
                      <tr>
                        <td colSpan={9} className="px-3 py-8 text-center text-gray-600">
                          No usage events yet — start a session to see costs here.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="px-4 py-3 bg-white/[0.02] border border-white/[0.06] rounded-xl">
      <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold text-white tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-gray-600 mt-0.5">{hint}</div>}
    </div>
  );
}
