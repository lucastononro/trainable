'use client';

/**
 * Comparison leaderboard — /compare?sessions=a,b,c[&project=<id>]
 *
 * Consumes the backend /compare payload (routers/compare.py): one
 * round-trip returns session/experiment headers, per-metric series for
 * every session, feature overlap, and cost totals for up to 8 sessions.
 *
 * Renders a sortable leaderboard table (one row per session; one column
 * per metric showing the latest value, plus total cost and created-at)
 * and overlaid per-metric charts reusing the MetricsTab chart stack
 * (palette, tooltip, formatters). Lives inside the app shell like the
 * experiments index.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpDown,
  Loader2,
  RefreshCw,
  Trophy,
} from 'lucide-react';

import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import Sidebar from '@/components/Sidebar';
import {
  PALETTE,
  ChartTooltip,
  compactFormat,
  smartFormat,
  isLowerBetter,
  prettyMetricName,
} from '@/components/MetricsTab';
import type { CompareFeatureOverlap, CompareResponse } from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  prepping: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  training: 'bg-amber-500/20 text-amber-200 border-amber-500/40',
  trained: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  abandoned: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
  failed: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

function formatCost(c: number): string {
  if (c === 0) return '$0';
  if (c < 0.01) return '<$0.01';
  if (c < 1) return `$${c.toFixed(3)}`;
  return `$${c.toFixed(2)}`;
}

interface LeaderRow {
  id: string;
  missing: boolean;
  label: string; // experiment name (disambiguated with a short id if duplicated)
  state?: string;
  model?: string | null;
  created_at?: string;
  cost: number;
  // metric name → latest (highest-step) value for this session
  latest: Record<string, number>;
}

type SortDir = 'asc' | 'desc';

function CompareContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { projects } = useApp();

  const sessionIds = useMemo(
    () =>
      (searchParams?.get('sessions') || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    [searchParams],
  );
  const projectId = searchParams?.get('project') || null;
  const project = projectId ? projects.find((p) => p.id === projectId) || null : null;

  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<string | null>(null); // 'name' | 'cost' | 'created' | 'metric:<name>'
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [hiddenSessions, setHiddenSessions] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    if (sessionIds.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api.compare(sessionIds));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [sessionIds]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ── Derived: metric names, per-session rows, colors, labels ──
  const metricNames = useMemo(() => Object.keys(data?.metrics || {}).sort(), [data]);

  const rows = useMemo<LeaderRow[]>(() => {
    if (!data) return [];
    // Disambiguate duplicate experiment names with a short session id.
    const nameCounts = new Map<string, number>();
    for (const s of data.sessions) {
      const n = s.experiment_name || '';
      nameCounts.set(n, (nameCounts.get(n) || 0) + 1);
    }
    return data.sessions.map((s) => {
      const base = s.experiment_name || `session ${s.id.slice(0, 8)}`;
      const label =
        s.experiment_name && (nameCounts.get(s.experiment_name) || 0) > 1
          ? `${base} (${s.id.slice(0, 6)})`
          : base;
      const latest: Record<string, number> = {};
      for (const name of metricNames) {
        const series = (data.metrics[name] || []).find((x) => x.session_id === s.id);
        const pts = series?.points || [];
        if (pts.length > 0) latest[name] = pts[pts.length - 1].value;
      }
      return {
        id: s.id,
        missing: s.missing,
        label,
        state: s.state,
        model: s.model,
        created_at: s.created_at,
        cost: data.totals[s.id]?.cost_usd ?? 0,
        latest,
      };
    });
  }, [data, metricNames]);

  const colorOf = useCallback(
    (sessionId: string) => {
      const i = rows.findIndex((r) => r.id === sessionId);
      return PALETTE[(i >= 0 ? i : 0) % PALETTE.length];
    },
    [rows],
  );

  // ── Sorting — default: rank by the first metric, best value first ──
  const effectiveSortKey =
    sortKey ?? (metricNames.length > 0 ? `metric:${metricNames[0]}` : 'created');
  const effectiveSortDir: SortDir =
    sortKey !== null
      ? sortDir
      : metricNames.length > 0
        ? isLowerBetter(metricNames[0])
          ? 'asc'
          : 'desc'
        : 'asc';

  const toggleSort = (key: string, defaultDir: SortDir = 'asc') => {
    if (effectiveSortKey === key) {
      setSortKey(key);
      setSortDir(effectiveSortDir === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir(defaultDir);
    }
  };

  const sortedRows = useMemo(() => {
    const key = effectiveSortKey;
    const dir = effectiveSortDir === 'asc' ? 1 : -1;
    const val = (r: LeaderRow): string | number | null => {
      if (key === 'name') return r.label.toLowerCase();
      if (key === 'cost') return r.cost;
      if (key === 'created') return r.created_at || null;
      if (key.startsWith('metric:')) {
        const v = r.latest[key.slice('metric:'.length)];
        return v === undefined ? null : v;
      }
      return null;
    };
    return [...rows].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      // Rows without a value (missing session / metric never logged) sink
      // to the bottom regardless of direction.
      if (va === null && vb === null) return 0;
      if (va === null) return 1;
      if (vb === null) return -1;
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
  }, [rows, effectiveSortKey, effectiveSortDir]);

  // Best value per metric column (for the trophy highlight).
  const bestPerMetric = useMemo(() => {
    const best = new Map<string, number>();
    for (const name of metricNames) {
      const lower = isLowerBetter(name);
      let b: number | null = null;
      for (const r of rows) {
        const v = r.latest[name];
        if (v === undefined) continue;
        if (b === null || (lower ? v < b : v > b)) b = v;
      }
      if (b !== null) best.set(name, b);
    }
    return best;
  }, [rows, metricNames]);

  // ── Chart data: one merged step-indexed table per metric ──
  const charts = useMemo(() => {
    if (!data) return [];
    return metricNames.map((name) => {
      const stepMap = new Map<number, Record<string, number>>();
      for (const series of data.metrics[name] || []) {
        if (hiddenSessions.has(series.session_id)) continue;
        for (const p of series.points) {
          if (!stepMap.has(p.step)) stepMap.set(p.step, { step: p.step });
          stepMap.get(p.step)![series.session_id] = p.value;
        }
      }
      const sessionIdsWithData = (data.metrics[name] || []).map((s) => s.session_id);
      return {
        name,
        data: Array.from(stepMap.values()).sort((a, b) => a.step - b.step),
        sessionIds: sessionIdsWithData,
      };
    });
  }, [data, metricNames, hiddenSessions]);

  const toggleSession = (id: string) => {
    setHiddenSessions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const labelOf = useCallback(
    (sessionId: string) => rows.find((r) => r.id === sessionId)?.label || sessionId.slice(0, 8),
    [rows],
  );

  const overlap: CompareFeatureOverlap | null =
    data && !Array.isArray(data.feature_overlap) ? data.feature_overlap : null;

  const SortIndicator = ({ colKey }: { colKey: string }) =>
    effectiveSortKey === colKey ? (
      effectiveSortDir === 'asc' ? (
        <ArrowUp className="w-3 h-3 inline" />
      ) : (
        <ArrowDown className="w-3 h-3 inline" />
      )
    ) : (
      <ArrowUpDown className="w-3 h-3 inline opacity-30" />
    );

  return (
    <div className="h-screen flex bg-black" id="main-content">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          <button
            onClick={() => router.push('/experiments')}
            className="inline-flex items-center gap-1 rounded-md text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.06] px-2 py-1 transition-colors"
            title="Back to experiments"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
          </button>
          <Trophy className="w-4 h-4 text-amber-400" />
          <h1 className="text-sm font-semibold text-white">Comparison leaderboard</h1>
          {project ? <span className="text-xs text-gray-500">· {project.name}</span> : null}
          <span className="text-xs text-gray-600">
            · {sessionIds.length} session{sessionIds.length === 1 ? '' : 's'}
          </span>
          <div className="flex-1" />
          <button
            onClick={refresh}
            disabled={loading || sessionIds.length === 0}
            className="inline-flex items-center gap-1 rounded-md text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.06] px-2 py-1 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </header>

        <main className="flex-1 overflow-y-auto p-6 space-y-4">
          {error ? (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300">
              {error}
            </div>
          ) : null}

          {sessionIds.length < 2 ? (
            <div className="text-center py-20 text-gray-500">
              <Trophy className="w-8 h-8 mx-auto mb-2 text-gray-700" />
              <p className="text-sm">
                Select at least two sessions to compare — pick experiments on the{' '}
                <Link href="/experiments" className="text-amber-300 hover:underline">
                  experiments page
                </Link>{' '}
                and hit &ldquo;Compare selected&rdquo;.
              </p>
            </div>
          ) : loading && !data ? (
            <div className="flex items-center justify-center py-20 text-gray-500">
              <Loader2 className="w-5 h-5 mr-2 animate-spin" />
              Loading comparison…
            </div>
          ) : data ? (
            <>
              {/* ── Leaderboard table ── */}
              <section className="rounded-lg border border-surface-border bg-surface overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-gray-500 text-[11px] uppercase tracking-wide">
                      <tr className="border-b border-surface-border">
                        <th className="text-left px-4 py-2 font-medium">
                          <button
                            onClick={() => toggleSort('name')}
                            className="inline-flex items-center gap-1 hover:text-gray-300 transition-colors uppercase tracking-wide"
                          >
                            Experiment <SortIndicator colKey="name" />
                          </button>
                        </th>
                        <th className="text-left px-4 py-2 font-medium">State</th>
                        {metricNames.map((name) => (
                          <th key={name} className="text-right px-4 py-2 font-medium">
                            <button
                              onClick={() =>
                                toggleSort(`metric:${name}`, isLowerBetter(name) ? 'asc' : 'desc')
                              }
                              className="inline-flex items-center gap-1 hover:text-gray-300 transition-colors uppercase tracking-wide"
                              title={`Sort by ${prettyMetricName(name)} (latest value)`}
                            >
                              {prettyMetricName(name)} <SortIndicator colKey={`metric:${name}`} />
                            </button>
                          </th>
                        ))}
                        <th className="text-right px-4 py-2 font-medium">
                          <button
                            onClick={() => toggleSort('cost')}
                            className="inline-flex items-center gap-1 hover:text-gray-300 transition-colors uppercase tracking-wide"
                          >
                            Cost <SortIndicator colKey="cost" />
                          </button>
                        </th>
                        <th className="text-right px-4 py-2 font-medium">
                          <button
                            onClick={() => toggleSort('created')}
                            className="inline-flex items-center gap-1 hover:text-gray-300 transition-colors uppercase tracking-wide"
                          >
                            Created <SortIndicator colKey="created" />
                          </button>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedRows.map((r) => (
                        <tr
                          key={r.id}
                          className={`border-b border-surface-border last:border-b-0 text-gray-300 ${
                            hiddenSessions.has(r.id) ? 'opacity-40' : ''
                          }`}
                        >
                          <td className="px-4 py-2.5">
                            <div className="flex items-center gap-2 min-w-0">
                              <span
                                className="w-2.5 h-2.5 rounded-full shrink-0"
                                style={{ backgroundColor: colorOf(r.id) }}
                              />
                              {r.missing ? (
                                <span className="text-gray-600 italic">
                                  session not found ({r.id.slice(0, 8)})
                                </span>
                              ) : (
                                <div className="min-w-0">
                                  <div className="font-medium text-gray-100 truncate max-w-[280px]">
                                    {r.label}
                                  </div>
                                  {r.model ? (
                                    <div className="text-[11px] text-gray-500 truncate max-w-[280px]">
                                      {r.model}
                                    </div>
                                  ) : null}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-2.5">
                            {r.state ? (
                              <span
                                className={`text-[11px] px-2 py-0.5 rounded-full border ${
                                  STATE_TONE[r.state] ??
                                  'bg-white/[0.04] text-gray-400 border-white/[0.08]'
                                }`}
                              >
                                {r.state}
                              </span>
                            ) : null}
                          </td>
                          {metricNames.map((name) => {
                            const v = r.latest[name];
                            const isBest = v !== undefined && bestPerMetric.get(name) === v;
                            return (
                              <td
                                key={name}
                                className="px-4 py-2.5 text-right font-mono tabular-nums"
                              >
                                {v === undefined ? (
                                  <span className="text-gray-700">&mdash;</span>
                                ) : (
                                  <span
                                    className={
                                      isBest ? 'text-emerald-400 font-semibold' : 'text-gray-200'
                                    }
                                  >
                                    {smartFormat(v)}
                                  </span>
                                )}
                              </td>
                            );
                          })}
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-gray-300">
                            {formatCost(r.cost)}
                          </td>
                          <td className="px-4 py-2.5 text-right text-[11px] text-gray-500">
                            {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* ── Session legend — toggles a session's series across all charts ── */}
              {rows.length > 0 && metricNames.length > 0 ? (
                <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                  {rows
                    .filter((r) => !r.missing)
                    .map((r) => {
                      const hidden = hiddenSessions.has(r.id);
                      const c = colorOf(r.id);
                      return (
                        <button
                          key={r.id}
                          onClick={() => toggleSession(r.id)}
                          className={`flex items-center gap-1.5 transition-opacity group ${
                            hidden ? 'opacity-25 hover:opacity-50' : 'opacity-100'
                          }`}
                          title={hidden ? 'Show in charts' : 'Hide from charts'}
                        >
                          <svg width="14" height="8" viewBox="0 0 14 8" className="shrink-0">
                            <line
                              x1="0"
                              y1="4"
                              x2="14"
                              y2="4"
                              stroke={c}
                              strokeWidth="2"
                              strokeLinecap="round"
                              strokeDasharray={hidden ? '2 2' : 'none'}
                            />
                            <circle
                              cx="7"
                              cy="4"
                              r="2.5"
                              fill={hidden ? 'transparent' : c}
                              stroke={c}
                              strokeWidth="1"
                            />
                          </svg>
                          <span className="text-[11px] text-gray-400 group-hover:text-gray-300 transition-colors">
                            {r.label}
                          </span>
                        </button>
                      );
                    })}
                </div>
              ) : null}

              {/* ── Overlaid metric charts ── */}
              {metricNames.length > 0 ? (
                <div className="grid grid-cols-1 2xl:grid-cols-2 gap-3">
                  {charts.map((chart) => (
                    <div
                      key={chart.name}
                      className="bg-white/[0.02] border border-white/[0.06] rounded-lg overflow-hidden"
                    >
                      <div className="flex items-center justify-between px-3 py-2 border-b border-white/[0.04]">
                        <span className="text-[11px] font-semibold text-gray-400">
                          {prettyMetricName(chart.name)}
                        </span>
                        <span className="text-[10px] text-gray-600 tabular-nums">
                          {chart.data.length} pts
                        </span>
                      </div>
                      <div className="px-2 pt-2 pb-1">
                        <div className="h-[220px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <LineChart
                              data={chart.data}
                              margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
                            >
                              <CartesianGrid
                                strokeDasharray="4 4"
                                stroke="rgba(255,255,255,0.04)"
                                vertical={false}
                              />
                              <XAxis
                                dataKey="step"
                                stroke="transparent"
                                tick={{ fill: '#555', fontSize: 10 }}
                                tickLine={false}
                                axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                              />
                              <YAxis
                                stroke="transparent"
                                tick={{ fill: '#555', fontSize: 10 }}
                                tickLine={false}
                                axisLine={false}
                                width={55}
                                tickFormatter={compactFormat}
                              />
                              <Tooltip
                                content={<ChartTooltip />}
                                cursor={{ stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 }}
                              />
                              {chart.sessionIds
                                .filter((sid) => !hiddenSessions.has(sid))
                                .map((sid) => (
                                  <Line
                                    key={sid}
                                    type="monotone"
                                    dataKey={sid}
                                    name={labelOf(sid)}
                                    stroke={colorOf(sid)}
                                    strokeWidth={1.5}
                                    dot={false}
                                    animationDuration={300}
                                    connectNulls
                                  />
                                ))}
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-10 text-gray-600 text-sm">
                  None of the selected sessions logged metrics yet.
                </div>
              )}

              {/* ── Feature overlap ── */}
              {overlap ? (
                <section className="rounded-lg border border-surface-border bg-surface overflow-hidden">
                  <div className="px-4 py-2 border-b border-surface-border bg-white/[0.02]">
                    <h2 className="text-sm font-medium text-gray-200">Feature overlap</h2>
                  </div>
                  <div className="p-4 space-y-3">
                    <div>
                      <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">
                        Common to all ({overlap.common.length})
                      </div>
                      {overlap.common.length > 0 ? (
                        <div className="flex flex-wrap gap-1.5">
                          {overlap.common.map((f) => (
                            <span
                              key={f}
                              className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 font-mono"
                            >
                              {f}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <div className="text-xs text-gray-600">
                          No features shared by all sessions.
                        </div>
                      )}
                    </div>
                    {Object.entries(overlap.per_session).map(([sid, feats]) => {
                      const unique = feats.filter((f) => !overlap.common.includes(f));
                      return (
                        <div key={sid}>
                          <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5 flex items-center gap-1.5">
                            <span
                              className="w-2 h-2 rounded-full inline-block"
                              style={{ backgroundColor: colorOf(sid) }}
                            />
                            {labelOf(sid)} —{' '}
                            {unique.length ? `+${unique.length} extra` : 'no extras'} (
                            {feats.length} total)
                          </div>
                          {unique.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {unique.map((f) => (
                                <span
                                  key={f}
                                  className="text-[11px] px-2 py-0.5 rounded-full bg-white/[0.04] text-gray-400 border border-white/[0.08] font-mono"
                                >
                                  {f}
                                </span>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ) : null}
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

// useSearchParams requires a Suspense boundary for static prerendering.
export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="h-screen flex items-center justify-center bg-black text-gray-500">
          <Loader2 className="w-5 h-5 mr-2 animate-spin" />
          Loading…
        </div>
      }
    >
      <CompareContent />
    </Suspense>
  );
}
