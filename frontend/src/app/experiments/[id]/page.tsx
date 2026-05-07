'use client';

/**
 * Experiment detail page.
 *
 * Replaces the previous redirect-only page with a real detail view that
 * shows the experiment's lineage subgraph, linked datasets, registered
 * model, and reproducibility snapshot. The "Open in chat" header button
 * preserves the deep-link-into-chat workflow.
 *
 * URL with `?session=X` query param keeps the legacy redirect-to-chat
 * behavior for bookmarks and inbound links (e.g. SSE notifications).
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  ArrowRight,
  Box,
  Database,
  FlaskConical,
  GitBranch,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import LineageGraph from '@/components/lineage/LineageGraph';
import NodeMetadataPanel from '@/components/lineage/NodeMetadataPanel';
import type {
  ExperimentFullDetail,
  LineageGraph as LineageGraphPayload,
  LineageNode,
} from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-50 text-amber-800 border-amber-300',
  prepping: 'bg-amber-50 text-amber-800 border-amber-300',
  training: 'bg-amber-100 text-amber-900 border-amber-400',
  trained: 'bg-emerald-50 text-emerald-800 border-emerald-300',
  abandoned: 'bg-rose-50 text-rose-800 border-rose-300',
  failed: 'bg-rose-50 text-rose-800 border-rose-300',
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

  // Legacy redirect: if a `?session=X` query param is present, preserve
  // the old "land in chat" behavior so existing bookmarks still work.
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

  const openInChat = () => {
    if (!detail?.session_id) return;
    setActiveExperiment(experimentId, detail.session_id);
    router.push('/');
  };

  if (sessionParam) {
    // Redirecting — show a brief loader.
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-gray-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="text-gray-500 hover:text-gray-900 inline-flex items-center gap-1 text-sm"
          >
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <span className="text-gray-300">/</span>
          <h1 className="text-base font-semibold text-gray-900 inline-flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-amber-600" />
            {detail?.name ?? 'Experiment'}
          </h1>
          {detail?.state ? (
            <span
              className={`text-xs px-2 py-0.5 rounded-full border ${
                STATE_TONE[detail.state] ?? 'bg-gray-50 text-gray-700 border-gray-300'
              }`}
            >
              {detail.state}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {detail?.session_id ? (
            <button
              onClick={openInChat}
              className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50"
            >
              Open in chat
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          ) : null}
          <button
            onClick={refresh}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </header>

      {error ? (
        <div className="m-6 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      ) : null}

      <main className="flex-1 p-6 space-y-6">
        {detail?.hypothesis ? (
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
              Hypothesis
            </h2>
            <p className="text-sm text-gray-800 leading-relaxed">{detail.hypothesis}</p>
          </section>
        ) : null}

        <section className="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="border-b border-gray-200 px-4 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 inline-flex items-center gap-2">
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

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-2 inline-flex items-center gap-2">
              <Database className="w-3.5 h-3.5" />
              Datasets
            </h2>
            {detail?.datasets?.length ? (
              <ul className="space-y-2">
                {detail.datasets.map((d) => (
                  <li
                    key={d.id}
                    className="rounded-md border border-gray-200 px-3 py-2 hover:bg-gray-50"
                  >
                    <div className="flex items-center justify-between">
                      <div className="text-sm font-medium text-gray-900">{d.name}</div>
                      <div className="text-xs text-gray-500">
                        {d.kind} · {d.role} · {fmtBytes(d.size_bytes)}
                      </div>
                    </div>
                    {d.description ? (
                      <div className="text-xs text-gray-600 mt-1">{d.description}</div>
                    ) : null}
                    <div className="text-xs text-gray-400 font-mono mt-1 truncate">{d.path}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="text-sm text-gray-400 italic">
                No datasets registered for this experiment yet.
              </div>
            )}
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-2 inline-flex items-center gap-2">
              <Box className="w-3.5 h-3.5" />
              Model
            </h2>
            {detail?.model ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium text-gray-900">
                    {detail.model.name} v{detail.model.version}
                  </div>
                  <div className="text-xs text-gray-500">{detail.model.framework || '—'}</div>
                </div>
                {detail.model.metrics_summary &&
                Object.keys(detail.model.metrics_summary).length ? (
                  <div className="grid grid-cols-2 gap-1 text-xs">
                    {Object.entries(detail.model.metrics_summary).map(([k, v]) => (
                      <div key={k} className="flex justify-between border-b py-0.5">
                        <span className="text-gray-500">{k}</span>
                        <span className="font-mono">
                          {typeof v === 'number' ? v.toFixed(4) : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="text-xs text-gray-400 font-mono break-all">
                  {detail.model.artifact_uri}
                </div>
              </div>
            ) : (
              <div className="text-sm text-gray-400 italic">
                No model registered yet.
                {detail?.state === 'training'
                  ? ' Training is in progress — register-model has not been called.'
                  : null}
              </div>
            )}
          </section>
        </div>

        {detail?.snapshot ? (
          <section className="rounded-lg border border-gray-200 bg-white p-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-2">
              Reproducibility snapshot
            </h2>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex justify-between border-b py-0.5">
                <span className="text-gray-500">dataset_hash</span>
                <span className="font-mono">
                  {detail.snapshot.dataset_hash
                    ? detail.snapshot.dataset_hash.slice(0, 12) + '…'
                    : '—'}
                </span>
              </div>
              <div className="flex justify-between border-b py-0.5">
                <span className="text-gray-500">code_hash</span>
                <span className="font-mono">
                  {detail.snapshot.code_hash ? detail.snapshot.code_hash.slice(0, 12) + '…' : '—'}
                </span>
              </div>
              <div className="flex justify-between border-b py-0.5 col-span-2">
                <span className="text-gray-500">manifest_uri</span>
                <span className="font-mono break-all">{detail.snapshot.manifest_uri || '—'}</span>
              </div>
            </div>
          </section>
        ) : null}
      </main>

      {selected ? <NodeMetadataPanel node={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
