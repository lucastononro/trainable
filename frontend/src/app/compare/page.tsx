'use client';

import { useEffect, useMemo, useState, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { ArrowLeft, X, RefreshCw } from 'lucide-react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '@/lib/api';
import type { CompareResponse, Experiment } from '@/lib/types';

const SERIES_COLORS = [
  '#60a5fa',
  '#34d399',
  '#fbbf24',
  '#f472b6',
  '#a78bfa',
  '#fb923c',
  '#22d3ee',
  '#9ca3af',
];

function CompareInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionIds = useMemo(
    () =>
      (searchParams.get('sessions') || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    [searchParams],
  );

  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [allExperiments, setAllExperiments] = useState<Experiment[]>([]);

  useEffect(() => {
    api
      .listExperiments()
      .then(setAllExperiments)
      .catch(() => setAllExperiments([]));
  }, []);

  const refresh = async () => {
    if (sessionIds.length === 0) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setData(await api.compareSessions(sessionIds));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const removeSession = (id: string) => {
    const next = sessionIds.filter((s) => s !== id);
    const qs = next.length ? `?sessions=${next.join(',')}` : '';
    router.replace(`/compare${qs}`);
  };

  const addSession = (id: string) => {
    if (!id || sessionIds.includes(id)) return;
    const next = [...sessionIds, id];
    router.replace(`/compare?sessions=${next.join(',')}`);
  };

  const sessionLabel = (sid: string) => {
    const fromCompare = data?.sessions.find((s) => s.id === sid);
    if (fromCompare && !fromCompare.missing) {
      return `${fromCompare.experiment_name} · ${sid.slice(0, 8)}`;
    }
    const exp = allExperiments.find((e) => e.latest_session_id === sid);
    if (exp) return `${exp.name} · ${sid.slice(0, 8)}`;
    return sid.slice(0, 12);
  };

  const colorFor = (sid: string) => SERIES_COLORS[sessionIds.indexOf(sid) % SERIES_COLORS.length];

  return (
    <div className="min-h-screen bg-black text-gray-200">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.08]">
        <Link href="/" className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-sm font-semibold text-white">Compare runs</h1>
        <div className="flex-1" />
        <button
          onClick={refresh}
          disabled={loading || sessionIds.length === 0}
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400 disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <section className="flex flex-wrap items-center gap-2">
          {sessionIds.length === 0 && (
            <p className="text-xs text-gray-500">No sessions selected. Pick one to compare:</p>
          )}
          {sessionIds.map((sid) => (
            <span
              key={sid}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/[0.04] border border-white/[0.08] text-xs"
              style={{ borderColor: `${colorFor(sid)}55` }}
            >
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: colorFor(sid) }} />
              <span className="text-gray-200">{sessionLabel(sid)}</span>
              <button
                onClick={() => removeSession(sid)}
                className="p-0.5 rounded hover:bg-white/[0.08] text-gray-500"
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
          <select
            onChange={(e) => {
              addSession(e.target.value);
              e.target.value = '';
            }}
            value=""
            className="text-xs bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15]"
          >
            <option value="">+ add session…</option>
            {allExperiments
              .filter((e) => e.latest_session_id && !sessionIds.includes(e.latest_session_id))
              .slice(0, 50)
              .map((e) => (
                <option key={e.id} value={e.latest_session_id ?? ''}>
                  {e.name}
                </option>
              ))}
          </select>
        </section>

        {error && (
          <div className="px-3 py-2 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300">
            {error}
          </div>
        )}

        {data && (
          <>
            <section>
              <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold mb-2">
                Cost & sandbox
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {sessionIds.map((sid) => {
                  const t = data.totals[sid];
                  const meta = data.sessions.find((s) => s.id === sid);
                  return (
                    <div
                      key={sid}
                      className="px-4 py-3 bg-white/[0.02] border border-white/[0.06] rounded-xl"
                      style={{ borderLeft: `3px solid ${colorFor(sid)}` }}
                    >
                      <div className="text-xs font-medium text-gray-200 truncate">
                        {sessionLabel(sid)}
                      </div>
                      <div className="text-[10px] text-gray-500 mt-0.5">
                        {meta?.state ?? 'unknown'} · {meta?.model ?? '—'}
                      </div>
                      {t && (
                        <div className="mt-2 grid grid-cols-3 gap-2 text-[11px] tabular-nums">
                          <Stat label="Cost" value={`$${t.cost_usd.toFixed(3)}`} />
                          <Stat
                            label="Tokens"
                            value={`${formatTokens(t.input_tokens)}/${formatTokens(t.output_tokens)}`}
                          />
                          <Stat label="Sandbox" value={`${t.sandbox_seconds.toFixed(0)}s`} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>

            {Object.keys(data.metrics).length > 0 && (
              <section className="space-y-3">
                <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  Metrics overlay
                </h2>
                {Object.entries(data.metrics).map(([metricName, series]) => {
                  // Build a single chart with one line per session.
                  // Recharts wants: rows of {step, [sessionId]: value, ...}
                  const stepsSet = new Set<number>();
                  series.forEach((s) => s.points.forEach((p) => stepsSet.add(p.step)));
                  const steps = Array.from(stepsSet).sort((a, b) => a - b);
                  const data2 = steps.map((step) => {
                    const row: Record<string, number | string | null> = { step };
                    for (const s of series) {
                      const point = s.points.find((p) => p.step === step);
                      row[s.session_id] = point ? point.value : null;
                    }
                    return row;
                  });
                  return (
                    <div key={metricName}>
                      <div className="text-xs text-gray-400 mb-1">{metricName}</div>
                      <div className="h-48 bg-white/[0.02] border border-white/[0.06] rounded-xl px-3 py-2">
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={data2}>
                            <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
                            <XAxis dataKey="step" stroke="#6b7280" fontSize={10} />
                            <YAxis stroke="#6b7280" fontSize={10} />
                            <Tooltip
                              contentStyle={{
                                backgroundColor: '#000',
                                border: '1px solid rgba(255,255,255,0.1)',
                                fontSize: '11px',
                              }}
                            />
                            <Legend wrapperStyle={{ fontSize: '10px' }} />
                            {series.map((s) => (
                              <Line
                                key={s.session_id}
                                type="monotone"
                                dataKey={s.session_id}
                                name={sessionLabel(s.session_id)}
                                stroke={colorFor(s.session_id)}
                                dot={false}
                                strokeWidth={2}
                                connectNulls
                              />
                            ))}
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  );
                })}
              </section>
            )}

            {data.feature_overlap && (
              <section className="space-y-2">
                <h2 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">
                  Feature columns
                </h2>
                <div className="px-4 py-3 bg-white/[0.02] border border-white/[0.06] rounded-xl text-xs space-y-2">
                  <div>
                    <span className="text-gray-500">Common across runs:</span>{' '}
                    <span className="text-gray-200">
                      {data.feature_overlap.common.length === 0
                        ? '—'
                        : data.feature_overlap.common.join(', ')}
                    </span>
                  </div>
                  {Object.entries(data.feature_overlap.per_session).map(([sid, feats]) => {
                    const unique = feats.filter((f) => !data.feature_overlap!.common.includes(f));
                    if (unique.length === 0) return null;
                    return (
                      <div key={sid}>
                        <span className="text-gray-500">Only in {sessionLabel(sid)}:</span>{' '}
                        <span className="text-gray-200">{unique.join(', ')}</span>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-gray-600">{label}</div>
      <div className="text-gray-200">{value}</div>
    </div>
  );
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="p-8 text-xs text-gray-500">Loading…</div>}>
      <CompareInner />
    </Suspense>
  );
}
