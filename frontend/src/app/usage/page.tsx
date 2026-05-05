'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ChevronDown, ChevronRight, Cpu, RefreshCw, Sparkles } from 'lucide-react';
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
import type { SessionUsageRow, UsageSummary } from '@/lib/types';

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
  if (c < 0.005) return '<$0.01';
  if (c < 1) return `$${c.toFixed(3)}`;
  return `$${c.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatSeconds(s: number): string {
  if (s >= 3600) return `${(s / 3600).toFixed(1)}h`;
  if (s >= 60) return `${(s / 60).toFixed(1)}m`;
  return `${s.toFixed(0)}s`;
}

function shortSid(sid: string): string {
  return sid.length > 12 ? `${sid.slice(0, 8)}…${sid.slice(-4)}` : sid;
}

function formatTimestamp(iso: string | null): string {
  if (!iso) return '—';
  return iso.replace('T', ' ').slice(0, 19);
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
              <Tile
                label="LLM (tokens)"
                value={formatCost(summary.totals.llm_cost_usd)}
                hint={`${formatTokens(summary.totals.input_tokens)} in / ${formatTokens(summary.totals.output_tokens)} out · ${summary.totals.llm_calls} calls`}
                accent="text-violet-300"
                icon={<Sparkles className="w-3.5 h-3.5" />}
              />
              <Tile
                label="Compute (infra)"
                value={formatCost(summary.totals.compute_cost_usd)}
                hint={`${formatSeconds(summary.totals.compute_seconds)} · ${summary.totals.compute_runs} runs`}
                accent="text-emerald-300"
                icon={<Cpu className="w-3.5 h-3.5" />}
              />
              <Tile
                label="Cache hit"
                value={
                  summary.totals.input_tokens + summary.totals.cache_read_input_tokens > 0
                    ? `${(
                        (summary.totals.cache_read_input_tokens /
                          (summary.totals.input_tokens +
                            summary.totals.cache_read_input_tokens)) *
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
                    <Legend wrapperStyle={{ fontSize: '11px' }} />
                    <Line
                      dataKey="llm_cost_usd"
                      name="LLM"
                      stroke="#a78bfa"
                      strokeWidth={2}
                      dot={{ r: 2 }}
                    />
                    <Line
                      dataKey="compute_cost_usd"
                      name="Compute"
                      stroke="#34d399"
                      strokeWidth={2}
                      dot={{ r: 2 }}
                    />
                    <Line
                      dataKey="cost_usd"
                      name="Total"
                      stroke="#9ca3af"
                      strokeWidth={1}
                      strokeDasharray="3 3"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            <SessionBreakdown sessions={summary.by_session} />

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
                      <Legend wrapperStyle={{ fontSize: '11px' }} />
                      <Bar dataKey="llm_cost_usd" stackId="cost" name="LLM" fill="#a78bfa" />
                      <Bar
                        dataKey="compute_cost_usd"
                        stackId="cost"
                        name="Compute"
                        fill="#34d399"
                      />
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
                      <XAxis
                        dataKey="model"
                        stroke="#6b7280"
                        fontSize={9}
                        angle={-15}
                        textAnchor="end"
                        height={50}
                      />
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
                      <Bar dataKey="cost_usd" name="cost">
                        {summary.by_model.map((_, i) => (
                          <Cell key={i} fill={AGENT_COLORS[i % AGENT_COLORS.length]} />
                        ))}
                      </Bar>
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
                      <th className="px-3 py-2 text-left">Session</th>
                      <th className="px-3 py-2 text-left">Agent</th>
                      <th className="px-3 py-2 text-left">Model</th>
                      <th className="px-3 py-2 text-right">In</th>
                      <th className="px-3 py-2 text-right">Out</th>
                      <th className="px-3 py-2 text-right">Cache</th>
                      <th className="px-3 py-2 text-right">Compute</th>
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
                            {formatTimestamp(ev.created_at)}
                          </td>
                          <td className="px-3 py-1.5">
                            {ev.kind === 'sandbox' ? (
                              <span className="text-emerald-300">compute</span>
                            ) : (
                              <span className="text-violet-300">llm</span>
                            )}
                          </td>
                          <td className="px-3 py-1.5 text-gray-500 font-mono tabular-nums">
                            {ev.session_id ? shortSid(ev.session_id) : '—'}
                          </td>
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
                        <td colSpan={10} className="px-3 py-8 text-center text-gray-600">
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

// ---------------------------------------------------------------------------
// By-session collapsible breakdown — header row shows totals split into LLM
// + compute, click to expand for per-agent / per-model detail.
// ---------------------------------------------------------------------------

function SessionBreakdown({ sessions }: { sessions: SessionUsageRow[] }) {
  const [openId, setOpenId] = useState<string | null>(null);

  // Sort to put highest-cost sessions on top — those are the ones the user
  // is most likely investigating first.
  const sorted = useMemo(
    () => [...sessions].sort((a, b) => b.cost_usd - a.cost_usd),
    [sessions],
  );

  return (
    <section className="space-y-2">
      <div className="flex items-center gap-2">
        <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
          By session
        </h2>
        <span className="text-[10px] text-gray-600">{sorted.length} sessions</span>
      </div>
      <div className="bg-white/[0.02] border border-white/[0.06] rounded-xl overflow-hidden">
        <div className="grid grid-cols-12 gap-2 px-3 py-2 text-[10px] uppercase tracking-wider text-gray-500 border-b border-white/[0.06]">
          <div className="col-span-3">Session</div>
          <div className="col-span-2">Last seen</div>
          <div className="col-span-2 text-right">LLM</div>
          <div className="col-span-2 text-right">Compute</div>
          <div className="col-span-2 text-right">Total</div>
          <div className="col-span-1" />
        </div>
        {sorted.length === 0 && (
          <div className="px-3 py-8 text-center text-xs text-gray-600">
            No session activity recorded yet.
          </div>
        )}
        {sorted.map((s) => {
          const isOpen = openId === s.session_id;
          return (
            <div key={s.session_id} className="border-b border-white/[0.04] last:border-b-0">
              <button
                onClick={() => setOpenId(isOpen ? null : s.session_id)}
                className="w-full grid grid-cols-12 gap-2 px-3 py-2 text-xs items-center hover:bg-white/[0.03] transition-colors text-left"
              >
                <div className="col-span-3 flex items-center gap-2">
                  {isOpen ? (
                    <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
                  ) : (
                    <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
                  )}
                  <span className="font-mono tabular-nums text-gray-300">
                    {shortSid(s.session_id)}
                  </span>
                </div>
                <div className="col-span-2 text-gray-500 tabular-nums">
                  {formatTimestamp(s.last_seen)}
                </div>
                <div className="col-span-2 text-right tabular-nums text-violet-300">
                  {formatCost(s.llm_cost_usd)}
                </div>
                <div className="col-span-2 text-right tabular-nums text-emerald-300">
                  {formatCost(s.compute_cost_usd)}
                </div>
                <div className="col-span-2 text-right tabular-nums font-semibold text-white">
                  {formatCost(s.cost_usd)}
                </div>
                <div className="col-span-1" />
              </button>
              {isOpen && <SessionDetail row={s} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function SessionDetail({ row }: { row: SessionUsageRow }) {
  return (
    <div className="px-3 py-3 bg-black/30 border-t border-white/[0.04] grid grid-cols-1 md:grid-cols-2 gap-4">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-1.5">
          LLM (tokens)
        </div>
        <dl className="text-xs grid grid-cols-2 gap-y-1">
          <dt className="text-gray-500">Calls</dt>
          <dd className="text-right tabular-nums">{row.llm_calls}</dd>
          <dt className="text-gray-500">Input tokens</dt>
          <dd className="text-right tabular-nums">{formatTokens(row.input_tokens)}</dd>
          <dt className="text-gray-500">Output tokens</dt>
          <dd className="text-right tabular-nums">{formatTokens(row.output_tokens)}</dd>
          <dt className="text-gray-500">Cache reads</dt>
          <dd className="text-right tabular-nums text-violet-300">
            {formatTokens(row.cache_read_input_tokens)}
          </dd>
          <dt className="text-gray-500">Cost</dt>
          <dd className="text-right tabular-nums text-violet-300 font-semibold">
            {formatCost(row.llm_cost_usd)}
          </dd>
        </dl>
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-1.5">
          Compute (infrastructure)
        </div>
        <dl className="text-xs grid grid-cols-2 gap-y-1">
          <dt className="text-gray-500">Runs</dt>
          <dd className="text-right tabular-nums">{row.compute_runs}</dd>
          <dt className="text-gray-500">Seconds</dt>
          <dd className="text-right tabular-nums">{formatSeconds(row.compute_seconds)}</dd>
          <dt className="text-gray-500">Cost</dt>
          <dd className="text-right tabular-nums text-emerald-300 font-semibold">
            {formatCost(row.compute_cost_usd)}
          </dd>
        </dl>
      </div>

      <div className="md:col-span-2 pt-2 border-t border-white/[0.04] flex flex-wrap gap-x-6 gap-y-1 text-[11px]">
        <div>
          <span className="text-gray-500">Agents:</span>{' '}
          <span className="text-gray-300">
            {row.agents.length > 0 ? row.agents.join(', ') : '—'}
          </span>
        </div>
        <div>
          <span className="text-gray-500">Models:</span>{' '}
          <span className="text-gray-300">
            {row.models.length > 0 ? row.models.join(', ') : '—'}
          </span>
        </div>
        <div>
          <span className="text-gray-500">First seen:</span>{' '}
          <span className="text-gray-400 tabular-nums">{formatTimestamp(row.first_seen)}</span>
        </div>
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
  accent,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="px-4 py-3 bg-white/[0.02] border border-white/[0.06] rounded-xl">
      <div
        className={`text-[10px] uppercase tracking-wider font-semibold flex items-center gap-1.5 ${
          accent ?? 'text-gray-500'
        }`}
      >
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-semibold text-white tabular-nums">{value}</div>
      {hint && <div className="text-[10px] text-gray-600 mt-0.5">{hint}</div>}
    </div>
  );
}
