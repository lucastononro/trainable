'use client';

/**
 * Experiment detail page.
 *
 * Lives inside the main app shell (Sidebar + dark canvas) so the
 * sidebar nav stays visible while the user inspects the experiment's
 * lineage subgraph, datasets, model, snapshot, and sessions.
 *
 * URL with `?session=X` query param keeps the legacy redirect-to-chat
 * behavior for bookmarks and inbound links.
 */

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  ArrowRight,
  Box,
  Database,
  FlaskConical,
  GitBranch,
  Loader2,
  MessageSquare,
  RefreshCw,
} from 'lucide-react';

import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import Sidebar from '@/components/Sidebar';
import LineageGraph from '@/components/lineage/LineageGraph';
import NodeMetadataPanel from '@/components/lineage/NodeMetadataPanel';
import type {
  ExperimentFullDetail,
  LineageGraph as LineageGraphPayload,
  LineageNode,
  Session,
} from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  prepping: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  training: 'bg-amber-500/20 text-amber-200 border-amber-500/40',
  trained: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  abandoned: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
  failed: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

function fmtBytes(n: number): string {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${u[i]}`;
}

export default function ExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const experimentId = params?.id || '';
  const sessionParam = searchParams?.get('session');

  // Legacy redirect: `?session=X` lands the user back in chat (preserves
  // existing bookmarks + inbound SSE notifications).
  const { setActiveExperiment } = useApp();
  useEffect(() => {
    if (sessionParam) {
      setActiveExperiment(experimentId, sessionParam);
      window.location.href = '/';
    }
  }, [sessionParam, experimentId, setActiveExperiment]);

  const [detail, setDetail] = useState<ExperimentFullDetail | null>(null);
  const [lineage, setLineage] = useState<LineageGraphPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LineageNode | null>(null);

  const refresh = useCallback(async () => {
    if (!experimentId) return;
    setLoading(true);
    setError(null);
    try {
      const [d, g] = await Promise.all([
        api.getExperimentDetail(experimentId),
        api.experimentLineage(experimentId),
      ]);
      setDetail(d);
      setLineage(g);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [experimentId]);

  useEffect(() => {
    if (!sessionParam) refresh();
  }, [sessionParam, refresh]);

  const openSessionInChat = (sess: Session) => {
    setActiveExperiment(experimentId, sess.id);
    router.push('/');
  };

  if (sessionParam) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-gray-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="h-screen flex bg-black" id="main-content">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          <FlaskConical className="w-4 h-4 text-amber-400" />
          <h1 className="text-sm font-semibold text-white truncate">
            {detail?.name ?? 'Experiment'}
          </h1>
          {detail?.state ? (
            <span
              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                STATE_TONE[detail.state] ?? 'bg-white/[0.04] text-gray-400 border-white/[0.08]'
              }`}
            >
              {detail.state}
            </span>
          ) : null}
          <div className="flex-1" />
          <button
            onClick={refresh}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.06] px-2 py-1 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </header>

        <main className="flex-1 overflow-y-auto p-6 space-y-6">
          {error ? (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300">
              {error}
            </div>
          ) : null}

          {detail?.hypothesis ? (
            <section className="rounded-lg border border-surface-border bg-surface p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-gray-500 mb-1">
                Hypothesis
              </h2>
              <p className="text-sm text-gray-200 leading-relaxed">{detail.hypothesis}</p>
            </section>
          ) : null}

          <section className="rounded-lg border border-surface-border bg-surface shadow-sm overflow-hidden">
            <div className="border-b border-surface-border px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-gray-500 inline-flex items-center gap-2">
              <GitBranch className="w-3.5 h-3.5" />
              Lineage
            </div>
            <LineageGraph
              data={lineage}
              loading={loading}
              height={420}
              onNodeClick={(n) => setSelected(n)}
            />
          </section>

          {/* Sessions attached to this experiment. With the cardinality
              flip, an agent-declared experiment has exactly one canonical
              session (Experiment.session_id); legacy experiments may
              have N. The backend dedupes both directions in the detail
              payload — we just render the list. */}
          <section className="rounded-lg border border-surface-border bg-surface p-4">
            <h2 className="text-[11px] font-medium uppercase tracking-wide text-gray-500 mb-2 inline-flex items-center gap-2">
              <MessageSquare className="w-3.5 h-3.5" />
              Sessions
              {detail?.sessions?.length ? (
                <span className="text-gray-600">· {detail.sessions.length}</span>
              ) : null}
            </h2>
            {detail?.sessions?.length ? (
              <ul className="space-y-1.5">
                {detail.sessions.map((s) => (
                  <li
                    key={s.id}
                    onClick={() => openSessionInChat(s)}
                    className="rounded-md border border-surface-border bg-white/[0.02] hover:bg-white/[0.05] px-3 py-2 cursor-pointer flex items-center gap-3"
                  >
                    <MessageSquare className="w-3.5 h-3.5 text-gray-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="font-mono text-xs text-gray-200 truncate">{s.id}</div>
                      <div className="text-[11px] text-gray-500">
                        {s.state ? `state=${s.state}` : ''}
                        {s.model ? ` · model=${s.model}` : ''}
                        {s.created_at ? ` · ${new Date(s.created_at).toLocaleString()}` : ''}
                      </div>
                    </div>
                    <ArrowRight className="w-3.5 h-3.5 text-gray-600" />
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-gray-500 italic">
                No sessions linked to this experiment.
              </div>
            )}
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <section className="rounded-lg border border-surface-border bg-surface p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-gray-500 mb-2 inline-flex items-center gap-2">
                <Database className="w-3.5 h-3.5" />
                Datasets
              </h2>
              {detail?.datasets?.length ? (
                <ul className="space-y-2">
                  {detail.datasets.map((d) => (
                    <li
                      key={d.id}
                      className="rounded-md border border-surface-border bg-white/[0.02] px-3 py-2"
                    >
                      <div className="flex items-center justify-between">
                        <div className="text-sm font-medium text-gray-200">{d.name}</div>
                        <div className="text-[11px] text-gray-500">
                          {d.kind} · {d.role} · {fmtBytes(d.size_bytes)}
                        </div>
                      </div>
                      {d.description ? (
                        <div className="text-[11px] text-gray-400 mt-1">{d.description}</div>
                      ) : null}
                      <div className="text-[11px] text-gray-600 font-mono mt-1 truncate">
                        {d.path}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-gray-500 italic">
                  No datasets registered for this experiment yet.
                </div>
              )}
            </section>

            <section className="rounded-lg border border-surface-border bg-surface p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-gray-500 mb-2 inline-flex items-center gap-2">
                <Box className="w-3.5 h-3.5" />
                Model
              </h2>
              {detail?.model ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="text-sm font-medium text-gray-200">
                      {detail.model.name} v{detail.model.version}
                    </div>
                    <div className="text-[11px] text-gray-500">{detail.model.framework || '—'}</div>
                  </div>
                  {detail.model.metrics_summary &&
                  Object.keys(detail.model.metrics_summary).length ? (
                    <div className="grid grid-cols-2 gap-1 text-[11px]">
                      {Object.entries(detail.model.metrics_summary).map(([k, v]) => (
                        <div
                          key={k}
                          className="flex justify-between border-b border-surface-border py-0.5"
                        >
                          <span className="text-gray-500">{k}</span>
                          <span className="font-mono text-gray-300">
                            {typeof v === 'number' ? v.toFixed(4) : String(v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="text-[11px] text-gray-600 font-mono break-all">
                    {detail.model.artifact_uri}
                  </div>
                </div>
              ) : (
                <div className="text-sm text-gray-500 italic">
                  No model registered yet.
                  {detail?.state === 'training'
                    ? ' Training is in progress — register-model has not been called.'
                    : null}
                </div>
              )}
            </section>
          </div>

          {detail?.snapshot ? (
            <section className="rounded-lg border border-surface-border bg-surface p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-gray-500 mb-2">
                Reproducibility snapshot
              </h2>
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="flex justify-between border-b border-surface-border py-0.5">
                  <span className="text-gray-500">dataset_hash</span>
                  <span className="font-mono text-gray-300">
                    {detail.snapshot.dataset_hash
                      ? detail.snapshot.dataset_hash.slice(0, 12) + '…'
                      : '—'}
                  </span>
                </div>
                <div className="flex justify-between border-b border-surface-border py-0.5">
                  <span className="text-gray-500">code_hash</span>
                  <span className="font-mono text-gray-300">
                    {detail.snapshot.code_hash ? detail.snapshot.code_hash.slice(0, 12) + '…' : '—'}
                  </span>
                </div>
                <div className="flex justify-between border-b border-surface-border py-0.5 col-span-2">
                  <span className="text-gray-500">manifest_uri</span>
                  <span className="font-mono text-gray-300 break-all">
                    {detail.snapshot.manifest_uri || '—'}
                  </span>
                </div>
              </div>
            </section>
          ) : null}
        </main>

        {selected ? <NodeMetadataPanel node={selected} onClose={() => setSelected(null)} /> : null}
      </div>
    </div>
  );
}
