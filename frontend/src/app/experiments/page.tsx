'use client';

/**
 * Experiments index — lives inside the main app shell (Sidebar + dark
 * canvas background) instead of a standalone page. The header and
 * sidebar are preserved so the user can navigate back to chat or jump
 * to other projects without losing the chrome.
 *
 * Rows are grouped by project. Clicking a row opens the chat for that
 * experiment's session (matching the sidebar's behavior). A small
 * "details" icon on each row goes to /experiments/{id} for the full
 * lineage subgraph view.
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  Box,
  Database,
  ExternalLink,
  FlaskConical,
  Folder,
  Loader2,
  RefreshCw,
} from 'lucide-react';

import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import Sidebar from '@/components/Sidebar';
import type { Experiment, ExperimentFullDetail, Project } from '@/lib/types';

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  prepping: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  training: 'bg-amber-500/20 text-amber-200 border-amber-500/40',
  trained: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  abandoned: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
  failed: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

interface RowDetail {
  id: string;
  name: string;
  hypothesis: string;
  state: string;
  project_id: string;
  session_id: string | null;
  created_at: string;
  // Hydrated async per-row.
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
  const router = useRouter();
  const { projects, setActiveProject, setActiveExperiment } = useApp();
  const [rows, setRows] = useState<RowDetail[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState(false);

  const fetchExperiments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Always fetch across all projects on this page; per-project
      // grouping happens in render. Lets the user spot orphan experiments
      // and switch projects without losing the page.
      const list = await api.listExperiments();
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
  }, []);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.map((r) => r.id).join(',')]);

  // Bucket by project, preserving the project order from useApp().
  const grouped = (() => {
    const byProject = new Map<string, RowDetail[]>();
    for (const r of rows) {
      if (!byProject.has(r.project_id)) byProject.set(r.project_id, []);
      byProject.get(r.project_id)!.push(r);
    }
    // Order: known projects (in sidebar order), then unknown buckets last.
    const ordered: Array<{ project: Project | null; rows: RowDetail[] }> = [];
    for (const p of projects) {
      const rs = byProject.get(p.id);
      if (rs && rs.length) {
        ordered.push({ project: p, rows: rs });
        byProject.delete(p.id);
      }
    }
    byProject.forEach((rs, pid) => {
      ordered.push({
        project: { id: pid, name: 'Unknown project' } as Project,
        rows: rs,
      });
    });
    return ordered;
  })();

  const openInChat = (r: RowDetail) => {
    setActiveProject(r.project_id);
    setActiveExperiment(r.id, r.session_id);
    router.push('/');
  };

  return (
    <div className="h-screen flex bg-black" id="main-content">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          <FlaskConical className="w-4 h-4 text-amber-400" />
          <h1 className="text-sm font-semibold text-white">Experiments</h1>
          {hydrating ? (
            <Loader2 className="w-3 h-3 text-gray-500 animate-spin" />
          ) : null}
          <div className="flex-1" />
          <button
            onClick={fetchExperiments}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.06] px-2 py-1 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </header>

        <main className="flex-1 overflow-y-auto p-6">
          {error ? (
            <div className="mb-4 rounded-md border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300">
              {error}
            </div>
          ) : null}

          {loading && rows.length === 0 ? (
            <div className="flex items-center justify-center py-20 text-gray-500">
              <Loader2 className="w-5 h-5 mr-2 animate-spin" />
              Loading experiments…
            </div>
          ) : rows.length === 0 ? (
            <div className="text-center py-20 text-gray-500">
              <FlaskConical className="w-8 h-8 mx-auto mb-2 text-gray-700" />
              <p className="text-sm">
                No experiments yet. They&apos;ll appear here once an agent calls
                create-experiment in any session.
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {grouped.map(({ project, rows: projectRows }) => (
                <section
                  key={project?.id ?? 'unknown'}
                  className="rounded-lg border border-surface-border bg-surface overflow-hidden"
                >
                  <div className="flex items-center gap-2 px-4 py-2 border-b border-surface-border bg-white/[0.02]">
                    <Folder className="w-3.5 h-3.5 text-gray-500" />
                    <h2 className="text-sm font-medium text-gray-200">
                      {project?.name ?? 'Unknown project'}
                    </h2>
                    <span className="text-xs text-gray-500">
                      · {projectRows.length}{' '}
                      {projectRows.length === 1 ? 'experiment' : 'experiments'}
                    </span>
                  </div>
                  <table className="w-full text-sm">
                    <thead className="text-gray-500 text-[11px] uppercase tracking-wide">
                      <tr className="border-b border-surface-border">
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
                      {projectRows.map((r) => (
                        <tr
                          key={r.id}
                          onClick={() => openInChat(r)}
                          className="border-b border-surface-border last:border-b-0 hover:bg-white/[0.04] cursor-pointer text-gray-300"
                        >
                          <td className="px-4 py-2.5">
                            <div className="font-medium text-gray-100">{r.name}</div>
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
                              className={`text-[11px] px-2 py-0.5 rounded-full border ${
                                STATE_TONE[r.state] ??
                                'bg-white/[0.04] text-gray-400 border-white/[0.08]'
                              }`}
                            >
                              {r.state}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            {r.datasetName ?? (hydrating ? '—' : '')}
                          </td>
                          <td className="px-4 py-2.5">
                            {r.modelName ?? (hydrating ? '—' : '')}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11px] text-gray-400">
                            {r.modelMetric ?? ''}
                          </td>
                          <td className="px-4 py-2.5 text-right text-[11px] text-gray-500">
                            {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
                          </td>
                          <td className="px-2 py-2.5">
                            <div className="flex items-center justify-end gap-1">
                              <Link
                                href={`/experiments/${r.id}`}
                                onClick={(e) => e.stopPropagation()}
                                className="p-1 rounded text-gray-500 hover:text-gray-200 hover:bg-white/[0.06]"
                                title="Open detail page"
                              >
                                <ExternalLink className="w-3.5 h-3.5" />
                              </Link>
                              <ArrowRight className="w-3.5 h-3.5 text-gray-600" />
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </section>
              ))}
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
