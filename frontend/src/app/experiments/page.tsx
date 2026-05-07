'use client';

/**
 * Standalone experiments list page.
 *
 * Sister page to /models — table of all experiments across the active
 * project, with state badge, hypothesis snippet, dataset name (where the
 * agent registered one), model name (where register-model fired). Click
 * a row to drill into /experiments/{id}.
 *
 * Lives at /experiments rather than /projects/{id}/experiments because
 * the AppContext already tracks an active project; the page picks that
 * up and falls back to "all projects" when none is selected.
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  ArrowRight,
  Box,
  Database,
  FlaskConical,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import type { Experiment, ExperimentFullDetail } from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-50 text-amber-800 border-amber-300',
  prepping: 'bg-amber-50 text-amber-800 border-amber-300',
  training: 'bg-amber-100 text-amber-900 border-amber-400',
  trained: 'bg-emerald-50 text-emerald-800 border-emerald-300',
  abandoned: 'bg-rose-50 text-rose-800 border-rose-300',
  failed: 'bg-rose-50 text-rose-800 border-rose-300',
};

interface RowDetail {
  id: string;
  name: string;
  hypothesis: string;
  state: string;
  project_id: string;
  session_id: string | null;
  created_at: string;
  // Pulled async per-row for the fuller payload.
  datasetName?: string;
  modelName?: string;
  modelMetric?: string;
}

function formatTopMetric(metrics: Record<string, number> | undefined): string {
  if (!metrics) return '';
  const entry = Object.entries(metrics)[0];
  if (!entry) return '';
  const v = typeof entry[1] === 'number' ? entry[1].toFixed(3) : String(entry[1]);
  return `${entry[0]} = ${v}`;
}

export default function ExperimentsListPage() {
  const { activeProjectId, projects } = useApp();
  const [rows, setRows] = useState<RowDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState(false);

  const fetchExperiments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.listExperiments(
        activeProjectId ? { projectId: activeProjectId } : undefined,
      );
      const base: RowDetail[] = list.map((e: Experiment) => ({
        id: e.id,
        name: e.name,
        hypothesis: e.hypothesis ?? '',
        state: e.state ?? 'created',
        project_id: e.project_id,
        session_id: e.session_id ?? e.latest_session_id ?? null,
        created_at: e.created_at,
      }));
      setRows(base);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [activeProjectId]);

  useEffect(() => {
    fetchExperiments();
  }, [fetchExperiments]);

  // Hydrate dataset/model names per row in parallel after the table renders.
  useEffect(() => {
    if (rows.length === 0) return;
    let cancelled = false;
    setHydrating(true);
    Promise.all(
      rows.map((r) =>
        api
          .getExperimentDetail(r.id)
          .then((d: ExperimentFullDetail) => ({
            id: r.id,
            datasetName: d.datasets?.find((x) => x.role === 'input')?.name,
            modelName: d.model?.name ? `${d.model.name} v${d.model.version}` : undefined,
            modelMetric: formatTopMetric(d.model?.metrics_summary),
          }))
          .catch(() => ({ id: r.id })),
      ),
    ).then((details) => {
      if (cancelled) return;
      const byId = new Map(details.map((d) => [d.id, d]));
      setRows((prev) =>
        prev.map((r) => {
          const d = byId.get(r.id);
          return d ? { ...r, ...d } : r;
        }),
      );
      setHydrating(false);
    });
    return () => {
      cancelled = true;
    };
    // Only re-hydrate when the row IDs change (not when we update the row
    // itself with hydrated values — that would loop forever).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.map((r) => r.id).join(',')]);

  const projectLabel = activeProjectId
    ? (projects.find((p) => p.id === activeProjectId)?.name ?? 'project')
    : 'all projects';

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
            Experiments
          </h1>
          <span className="text-xs text-gray-500">· {projectLabel}</span>
        </div>
        <button
          onClick={fetchExperiments}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </header>

      {error ? (
        <div className="m-6 rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      ) : null}

      <main className="flex-1 p-6">
        {loading && rows.length === 0 ? (
          <div className="flex items-center justify-center py-20 text-gray-500">
            <Loader2 className="w-5 h-5 mr-2 animate-spin" />
            Loading experiments…
          </div>
        ) : rows.length === 0 ? (
          <div className="text-center py-20 text-gray-500">
            <FlaskConical className="w-8 h-8 mx-auto mb-2 text-gray-400" />
            <p className="text-sm">
              No experiments yet. They&apos;ll appear here once an agent calls create-experiment in
              any session.
            </p>
          </div>
        ) : (
          <div className="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs uppercase tracking-wide">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Name</th>
                  <th className="text-left px-4 py-2 font-medium">State</th>
                  <th className="text-left px-4 py-2 font-medium">
                    <span className="inline-flex items-center gap-1">
                      <Database className="w-3 h-3" /> Dataset
                    </span>
                  </th>
                  <th className="text-left px-4 py-2 font-medium">
                    <span className="inline-flex items-center gap-1">
                      <Box className="w-3 h-3" /> Model
                    </span>
                  </th>
                  <th className="text-left px-4 py-2 font-medium">Top metric</th>
                  <th className="text-right px-4 py-2 font-medium">Created</th>
                  <th className="px-2"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-gray-100 hover:bg-gray-50">
                    <td className="px-4 py-2.5">
                      <Link
                        href={`/experiments/${r.id}`}
                        className="font-medium text-gray-900 hover:underline"
                      >
                        {r.name}
                      </Link>
                      {r.hypothesis ? (
                        <div
                          className="text-[11px] text-gray-500 truncate max-w-[400px]"
                          title={r.hypothesis}
                        >
                          {r.hypothesis}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full border ${
                          STATE_TONE[r.state] ?? 'bg-gray-50 text-gray-700 border-gray-300'
                        }`}
                      >
                        {r.state}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-700">
                      {r.datasetName ?? (hydrating ? '—' : '')}
                    </td>
                    <td className="px-4 py-2.5 text-gray-700">
                      {r.modelName ?? (hydrating ? '—' : '')}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-[11px] text-gray-700">
                      {r.modelMetric ?? ''}
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs text-gray-500">
                      {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                    </td>
                    <td className="px-2 py-2.5">
                      <Link
                        href={`/experiments/${r.id}`}
                        className="text-gray-400 hover:text-gray-700"
                      >
                        <ArrowRight className="w-4 h-4" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
