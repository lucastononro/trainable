'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, RefreshCw, Rocket, Box, Copy, CheckCircle2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useApp } from '@/lib/AppContext';
import type { RegisteredModel, DeploymentRow } from '@/lib/types';

function formatBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} kB`;
  return `${n} B`;
}

export default function ModelsPage() {
  const { activeProjectId, projects, refreshProjects } = useApp();
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [deployments, setDeployments] = useState<Record<string, DeploymentRow[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const projectId = activeProjectId || projects[0]?.id || null;

  const refresh = async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const ms = await api.listProjectModels(projectId);
      setModels(ms);
      const dep = await Promise.all(ms.map((m) => api.modelDeployments(m.id)));
      const dmap: Record<string, DeploymentRow[]> = {};
      ms.forEach((m, i) => (dmap[m.id] = dep[i]));
      setDeployments(dmap);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projects.length === 0) refreshProjects();
  }, [projects.length, refreshProjects]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const onDeploy = async (modelId: string) => {
    setDeploying(modelId);
    try {
      await api.deployModel(modelId);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeploying(null);
    }
  };

  const copyCurl = async (url: string, modelId: string) => {
    const curl = `curl -X POST '${url}/predict' \\
  -H 'Content-Type: application/json' \\
  -d '{"records": [{"feature_a": 1.0, "feature_b": 2.0}]}'`;
    await navigator.clipboard.writeText(curl);
    setCopied(modelId);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <div className="min-h-screen bg-black text-gray-200">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.08]">
        <Link href="/" className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400">
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <h1 className="text-sm font-semibold text-white">Model registry</h1>
        <div className="flex-1" />
        <button
          onClick={refresh}
          disabled={loading}
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-gray-400 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6">
        {!projectId && (
          <div className="text-xs text-gray-500">Pick a project from the sidebar first.</div>
        )}

        {error && (
          <div className="px-3 py-2 mb-4 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300">
            {error}
          </div>
        )}

        {projectId && models.length === 0 && !loading && (
          <div className="px-4 py-12 text-center bg-white/[0.02] border border-white/[0.06] rounded-xl">
            <Box className="w-8 h-8 text-gray-600 mx-auto mb-2" />
            <p className="text-sm text-gray-400">No models registered yet.</p>
            <p className="text-[11px] text-gray-600 mt-1">
              Promote a trained model from a session to see it here.
            </p>
          </div>
        )}

        {projectId && models.length > 0 && (
          <div className="space-y-3">
            {models.map((m) => {
              const deps = deployments[m.id] || [];
              const live = deps.find((d) => d.status === 'live');
              return (
                <div
                  key={m.id}
                  className="px-4 py-4 bg-white/[0.02] border border-white/[0.06] rounded-xl"
                >
                  <div className="flex items-start gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-2">
                        <h3 className="text-sm font-semibold text-white">{m.name}</h3>
                        <span className="text-[10px] text-gray-500">v{m.version}</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-gray-400">
                          {m.framework ?? 'unknown'}
                        </span>
                        {m.status === 'ready' && (
                          <span className="text-[10px] text-emerald-400">ready</span>
                        )}
                      </div>
                      <div className="mt-1 text-[11px] text-gray-500">
                        {formatBytes(m.artifact_size_bytes)} ·{' '}
                        {m.created_at?.replace('T', ' ').slice(0, 19)} · session{' '}
                        {m.source_session_id.slice(0, 8)}
                      </div>
                      {Object.keys(m.metrics_summary || {}).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {Object.entries(m.metrics_summary || {})
                            .slice(0, 6)
                            .map(([k, v]) => (
                              <span
                                key={k}
                                className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 tabular-nums"
                              >
                                {k}: {Number(v).toFixed(3)}
                              </span>
                            ))}
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {!live && (
                        <button
                          onClick={() => onDeploy(m.id)}
                          disabled={deploying === m.id}
                          className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 disabled:opacity-50"
                        >
                          <Rocket className="w-3 h-3" />
                          {deploying === m.id ? 'Deploying…' : 'Deploy'}
                        </button>
                      )}
                      {live && (
                        <button
                          onClick={() => copyCurl(live.endpoint_url ?? '', m.id)}
                          className="flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300"
                          title={live.endpoint_url ?? 'no url'}
                        >
                          {copied === m.id ? (
                            <CheckCircle2 className="w-3 h-3" />
                          ) : (
                            <Copy className="w-3 h-3" />
                          )}
                          {copied === m.id ? 'Copied' : 'Copy cURL'}
                        </button>
                      )}
                    </div>
                  </div>

                  {live && (
                    <div className="mt-3 px-3 py-2 rounded-lg bg-black/30 border border-white/[0.04] font-mono text-[11px] text-gray-400 break-all">
                      {live.endpoint_url}
                    </div>
                  )}
                  {deps.some((d) => d.status === 'failed') && (
                    <div className="mt-2 text-[11px] text-red-300">
                      Last deploy failed:{' '}
                      {deps.find((d) => d.status === 'failed')?.error ?? 'unknown error'}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </main>
    </div>
  );
}
