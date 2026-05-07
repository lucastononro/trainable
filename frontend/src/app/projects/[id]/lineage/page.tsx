'use client';

/**
 * Standalone lineage page: Project → all sessions × all experiments as
 * one graph. Reachable directly so the user can audit the full project
 * without entering a specific session. Reuses LineageGraph + NodeMetadataPanel.
 */

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, RefreshCw } from 'lucide-react';

import { api } from '@/lib/api';
import type { LineageGraph as LineageGraphPayload, LineageNode } from '@/lib/types';
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
          <h1 className="text-base font-semibold text-gray-900">Project lineage</h1>
        </div>
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <Legend />
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

      <main className="flex-1 p-6">
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden">
          <LineageGraph
            data={data}
            loading={loading}
            height="calc(100vh - 140px)"
            onNodeClick={(n) => setSelected(n)}
          />
        </div>
      </main>

      {selected ? <NodeMetadataPanel node={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-3">
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-slate-300" />
        Raw
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-blue-300" />
        Processed
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-300" />
        Experiment
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="inline-block w-2.5 h-2.5 rounded-sm bg-violet-300" />
        Model
      </span>
    </div>
  );
}
