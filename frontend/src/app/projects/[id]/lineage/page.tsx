'use client';

/**
 * Project-level lineage page.
 *
 * Lives inside the main app shell (Sidebar + dark surface) so the
 * navigation chrome stays consistent with /experiments and the chat.
 * Reuses LineageGraph + NodeMetadataPanel.
 */

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { GitBranch, RefreshCw } from 'lucide-react';

import { api } from '@/lib/api';
import type { LineageGraph as LineageGraphPayload, LineageNode } from '@/lib/types';
import Sidebar from '@/components/Sidebar';
import LineageGraph from '@/components/lineage/LineageGraph';
import NodeMetadataPanel from '@/components/lineage/NodeMetadataPanel';

export default function ProjectLineagePage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id;
  const [data, setData] = useState<LineageGraphPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LineageNode | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.projectLineage(projectId);
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="h-screen flex bg-black" id="main-content">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          <GitBranch className="w-4 h-4 text-violet-400" />
          <h1 className="text-sm font-semibold text-white">Project lineage</h1>
          <div className="flex-1" />
          <Legend />
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

        {error ? (
          <div className="m-4 rounded-md border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300">
            {error}
          </div>
        ) : null}

        <main className="flex-1 p-4 overflow-hidden">
          <div className="h-full rounded-lg border border-surface-border bg-surface shadow-sm overflow-hidden">
            <LineageGraph
              data={data}
              loading={loading}
              height="100%"
              onNodeClick={(n) => setSelected(n)}
            />
          </div>
        </main>

        {selected ? <NodeMetadataPanel node={selected} onClose={() => setSelected(null)} /> : null}
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3 text-[11px] text-gray-500">
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-slate-500/40 border border-slate-500/60" />
        Raw
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-sky-500/40 border border-sky-500/60" />
        Processed
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-500/40 border border-amber-500/60" />
        Experiment
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-violet-500/40 border border-violet-500/60" />
        Model
      </span>
    </div>
  );
}
