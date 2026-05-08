'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  RefreshCw,
  Rocket,
  Box,
  Copy,
  CheckCircle2,
  Download,
  ExternalLink,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  FileCode,
  Lock,
  BookOpen,
  Power,
  Key,
  RefreshCcw,
  Eye,
  EyeOff,
  Search,
} from 'lucide-react';
import Link from 'next/link';
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
import Sidebar from '@/components/Sidebar';
import type {
  ComputeOption,
  DeploymentRow,
  MetricPoint,
  RegisteredModel,
} from '@/lib/types';

function formatBytes(n: number): string {
  if (!n) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)} kB`;
  return `${n} B`;
}

const SPLIT_TINT: Record<string, string> = {
  train: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  val: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  validation: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
  test: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
};

// Recharts wants {step, [series_name]: value} rows. We pivot the flat
// MetricPoint list into one row per step with a column per (stage,
// metric_name) — same logic as the live MetricsTab, just simplified
// because we're rendering the snapshot, not subscribing to updates.
function buildChartData(points: MetricPoint[]) {
  if (!points.length) return { rows: [], series: [] as string[] };
  const stepMap = new Map<number, Record<string, number>>();
  const seriesSet = new Set<string>();
  for (const p of points) {
    const key = p.stage && p.stage !== 'train' ? `${p.stage}.${p.name}` : p.name;
    seriesSet.add(key);
    if (!stepMap.has(p.step)) stepMap.set(p.step, { step: p.step });
    stepMap.get(p.step)![key] = p.value;
  }
  return {
    rows: Array.from(stepMap.values()).sort((a, b) => a.step - b.step),
    series: Array.from(seriesSet),
  };
}

const SERIES_COLORS = [
  '#34d399', // emerald
  '#7dd3fc', // sky
  '#fbbf24', // amber
  '#a78bfa', // violet
  '#f472b6', // pink
  '#f87171', // red
];

function ModelChart({ points }: { points: MetricPoint[] }) {
  const { rows, series } = useMemo(() => buildChartData(points), [points]);
  if (!rows.length) return null;

  return (
    <div className="mt-3 h-44 rounded-md border border-white/[0.05] bg-black/30 p-2">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
          <XAxis dataKey="step" stroke="#6b7280" fontSize={10} />
          <YAxis stroke="#6b7280" fontSize={10} width={36} />
          <Tooltip
            contentStyle={{
              background: '#0b1220',
              border: '1px solid #1f2937',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: '#9ca3af' }}
          />
          <Legend
            wrapperStyle={{ fontSize: 10, paddingTop: 4 }}
            iconType="line"
            iconSize={8}
          />
          {series.map((s, i) => (
            <Line
              key={s}
              type="monotone"
              dataKey={s}
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ModelCard({
  m,
  deployments,
  computeOptions,
  onDeploy,
  onStop,
  onRotateKey,
  deploying,
  stopping,
  rotating,
}: {
  m: RegisteredModel;
  deployments: DeploymentRow[];
  computeOptions: ComputeOption[];
  onDeploy: (modelId: string, compute: string) => void;
  onStop: (deploymentId: string) => void;
  onRotateKey: (modelId: string) => void;
  deploying: boolean;
  stopping: boolean;
  rotating: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [keyCopied, setKeyCopied] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [chartOpen, setChartOpen] = useState(false);
  // Default to CPU when there's no live deployment to inherit from;
  // otherwise pre-select whatever compute was last shipped so the
  // user's "redeploy" stays on the same target unless they change it.
  const liveDep = deployments.find((d) => d.status === 'live');
  const [compute, setCompute] = useState<string>(liveDep?.compute || 'cpu');
  const live = liveDep;
  const failed = deployments.find((d) => d.status === 'failed');
  const refs = m.dataset_refs || {};
  const splits = Object.keys(refs);
  const hasCurves = (m.metrics_history?.length ?? 0) > 0;
  const hasServingApp = Boolean(m.serving_app_path);
  // Modal endpoints expose Swagger UI at <url>/docs when the app uses
  // `@modal.fastapi_endpoint(docs=True)` — our serving-app generator
  // always sets that flag, so we can link straight to it.
  const docsUrl = live?.endpoint_url ? `${live.endpoint_url}/docs` : null;

  const copyCurl = async (url: string) => {
    // Modal's @modal.fastapi_endpoint serves the predict function at
    // the root URL — no /predict suffix, unlike a typical FastAPI
    // mount. We embed the model's X-API-Key so the example works
    // out of the box; the user can rotate the key from the panel
    // below if they need a fresh one.
    const apiKey = m.api_key || '<paste-your-X-API-Key-here>';
    const curl =
      `curl -X POST '${url}' \\\n` +
      `  -H 'Content-Type: application/json' \\\n` +
      `  -H 'X-API-Key: ${apiKey}' \\\n` +
      `  -d '{"records": [{"feature_a": 1.0, "feature_b": 2.0}]}'`;
    await navigator.clipboard.writeText(curl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-white">{m.name}</h3>
            <span className="text-[10px] text-gray-500">v{m.version}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.06] text-gray-400">
              {m.framework ?? 'unknown'}
            </span>
            {m.status === 'ready' && (
              <span className="text-[10px] text-emerald-400">ready</span>
            )}
          </div>
          {m.description ? (
            <p className="mt-1 text-xs text-gray-400">{m.description}</p>
          ) : null}
          <div className="mt-1 text-[11px] text-gray-500 truncate">
            {formatBytes(m.artifact_size_bytes)} ·{' '}
            {m.created_at?.replace('T', ' ').slice(0, 19)}
            {m.experiment_id ? ' · experiment ' + m.experiment_id.slice(0, 8) : null}
          </div>

          {splits.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {splits.map((role) => {
                const ref = refs[role];
                const top = Object.entries(ref?.metrics || {})[0];
                const tint =
                  SPLIT_TINT[role] ?? 'bg-gray-500/15 text-gray-300 border-gray-500/30';
                return (
                  <span
                    key={role}
                    className={`text-[10px] px-1.5 py-0.5 rounded border ${tint} tabular-nums`}
                    title={`dataset_id=${ref.dataset_id}`}
                  >
                    <span className="opacity-70 uppercase mr-1">{role}</span>
                    {top ? `${top[0]}=${Number(top[1]).toFixed(3)}` : 'no metrics'}
                  </span>
                );
              })}
            </div>
          ) : Object.keys(m.metrics_summary || {}).length ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(m.metrics_summary).slice(0, 6).map(([k, v]) => (
                <span
                  key={k}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 tabular-nums"
                >
                  {k}: {Number(v).toFixed(3)}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {m.experiment_id ? (
            <Link
              href={`/experiments/${m.experiment_id}`}
              className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] text-gray-300"
              title="Open experiment + lineage"
            >
              <ExternalLink className="w-3 h-3" />
              Experiment
            </Link>
          ) : null}
          <a
            href={api.modelDownloadUrl(m.id)}
            className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] text-gray-300"
            title="Download artifact"
          >
            <Download className="w-3 h-3" />
            Download
          </a>
          {!live ? (
            hasServingApp ? (
              <div className="inline-flex items-center gap-1.5">
                <select
                  value={compute}
                  onChange={(e) => setCompute(e.target.value)}
                  disabled={deploying || computeOptions.length === 0}
                  className="text-xs h-7 pl-2 pr-6 rounded-lg bg-white/[0.04] border border-white/[0.06] text-gray-200 hover:bg-white/[0.06] focus:outline-none focus:ring-1 focus:ring-emerald-500/40 disabled:opacity-50"
                  title="Compute target — Modal regenerates app.py with the chosen gpu="
                >
                  {(computeOptions.length > 0
                    ? computeOptions
                    : [{ value: 'cpu', label: 'CPU', blurb: '' }]
                  ).map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => onDeploy(m.id, compute)}
                  disabled={deploying}
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 disabled:opacity-50"
                >
                  <Rocket className="w-3 h-3" />
                  {deploying ? 'Deploying…' : 'Deploy'}
                </button>
              </div>
            ) : (
              <span
                className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-white/[0.03] text-gray-500 cursor-not-allowed"
                title="Ask an agent to run create-serving-app for this model first."
              >
                <Lock className="w-3 h-3" />
                Deploy
              </span>
            )
          ) : (
            <>
              {docsUrl ? (
                <a
                  href={docsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] text-gray-200"
                  title="Open auto-generated Swagger UI"
                >
                  <BookOpen className="w-3 h-3" />
                  Docs
                </a>
              ) : null}
              <button
                onClick={() => copyCurl(live.endpoint_url ?? '')}
                className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300"
                title={live.endpoint_url ?? 'no url'}
              >
                {copied ? <CheckCircle2 className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {copied ? 'Copied' : 'Copy cURL'}
              </button>
              <button
                onClick={() => onStop(live.id)}
                disabled={stopping}
                className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-lg bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 disabled:opacity-50"
                title="Stop the Modal app and mark this deployment offline"
              >
                <Power className="w-3 h-3" />
                {stopping ? 'Stopping…' : 'Stop'}
              </button>
            </>
          )}
        </div>
      </div>

      {live ? (
        <div className="mt-3 px-3 py-2 rounded-lg bg-black/30 border border-white/[0.04] font-mono text-[11px] text-gray-400 break-all">
          <div className="flex items-baseline justify-between gap-2 not-italic">
            <span className="break-all">{live.endpoint_url}</span>
            <span
              className={`shrink-0 px-1.5 py-0.5 rounded font-sans tabular-nums text-[9px] uppercase tracking-wide ${
                (live.compute || 'cpu') === 'cpu'
                  ? 'bg-white/[0.06] text-gray-300'
                  : 'bg-emerald-500/15 text-emerald-300'
              }`}
              title="Compute this deployment is running on"
            >
              {live.compute || 'cpu'}
            </span>
          </div>
        </div>
      ) : null}
      {live && m.api_key ? (
        <div className="mt-2 px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/20">
          <div className="flex items-center gap-1.5 mb-1.5 text-[11px] text-amber-200/90">
            <Key className="w-3 h-3" />
            <span className="font-medium">X-API-Key</span>
            <span className="opacity-70">— include this header on every request.</span>
          </div>
          <div className="flex items-center gap-1.5">
            <code className="flex-1 px-2 py-1 rounded bg-black/40 border border-white/[0.04] font-mono text-[11px] text-gray-200 break-all">
              {showKey ? m.api_key : '•'.repeat(Math.min(m.api_key.length, 32))}
            </code>
            <button
              onClick={() => setShowKey((v) => !v)}
              className="inline-flex items-center justify-center w-7 h-7 rounded-md bg-white/[0.04] hover:bg-white/[0.08] text-gray-300"
              title={showKey ? 'Hide key' : 'Show key'}
            >
              {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={async () => {
                await navigator.clipboard.writeText(m.api_key || '');
                setKeyCopied(true);
                setTimeout(() => setKeyCopied(false), 2000);
              }}
              className="inline-flex items-center justify-center w-7 h-7 rounded-md bg-white/[0.04] hover:bg-white/[0.08] text-gray-300"
              title="Copy key to clipboard"
            >
              {keyCopied ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <button
              onClick={() => onRotateKey(m.id)}
              disabled={rotating}
              className="inline-flex items-center gap-1 px-2 h-7 rounded-md bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-[11px] disabled:opacity-50"
              title="Generate a new key + replace the Modal secret. Redeploy to roll containers immediately."
            >
              <RefreshCcw className={`w-3 h-3 ${rotating ? 'animate-spin' : ''}`} />
              {rotating ? 'Rotating…' : 'Rotate'}
            </button>
          </div>
        </div>
      ) : null}
      {live ? (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-500">
          Redeploy on:
          <select
            value={compute}
            onChange={(e) => setCompute(e.target.value)}
            disabled={deploying}
            className="text-[11px] h-6 pl-1.5 pr-5 rounded bg-white/[0.04] border border-white/[0.06] text-gray-300 focus:outline-none focus:ring-1 focus:ring-emerald-500/40 disabled:opacity-50"
          >
            {(computeOptions.length > 0
              ? computeOptions
              : [{ value: 'cpu', label: 'CPU', blurb: '' }]
            ).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {compute !== (live.compute || 'cpu') ? (
            <button
              onClick={() => onDeploy(m.id, compute)}
              disabled={deploying}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-300 disabled:opacity-50"
              title="Regenerate app.py with the selected compute and redeploy"
            >
              <Rocket className="w-3 h-3" />
              {deploying ? 'Redeploying…' : 'Redeploy'}
            </button>
          ) : null}
        </div>
      ) : null}
      {failed ? (
        <div className="mt-2 text-[11px] text-red-300">
          Last deploy failed: {failed.error ?? 'unknown error'}
        </div>
      ) : null}
      {!hasServingApp ? (
        <div className="mt-3 flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/20 text-[11px] text-amber-200/80">
          <FileCode className="w-3.5 h-3.5 mt-0.5 shrink-0 text-amber-300" />
          <div>
            No Modal serving app yet for this model. Open the chat and ask the
            agent to <span className="font-mono">create-serving-app</span> for{' '}
            <span className="font-mono">{m.name} v{m.version}</span> — that
            writes <span className="font-mono">app.py</span> next to the
            artifact and unlocks Deploy.
          </div>
        </div>
      ) : null}

      {hasCurves ? (
        <div className="mt-3">
          <button
            onClick={() => setChartOpen((v) => !v)}
            className="inline-flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-200"
          >
            {chartOpen ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            Training curves ({m.metrics_history?.length ?? 0} points)
          </button>
          {chartOpen ? <ModelChart points={m.metrics_history!} /> : null}
        </div>
      ) : null}
    </div>
  );
}

export default function ModelsPage() {
  const [models, setModels] = useState<RegisteredModel[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [deployments, setDeployments] = useState<Record<string, DeploymentRow[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deploying, setDeploying] = useState<string | null>(null);
  const [stopping, setStopping] = useState<string | null>(null);
  const [rotating, setRotating] = useState<string | null>(null);
  const [computeOptions, setComputeOptions] = useState<ComputeOption[]>([]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState('');

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const all = await api.listAllModels();
      setModels(all.models);
      setProjects(all.projects);
      // Fetch deployments per model in parallel — we need the live URL +
      // last-failed reason for the action buttons.
      const dep = await Promise.all(all.models.map((m) => api.modelDeployments(m.id)));
      const dmap: Record<string, DeploymentRow[]> = {};
      all.models.forEach((m, i) => (dmap[m.id] = dep[i]));
      setDeployments(dmap);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // Fire-and-forget: compute options are static; one fetch on mount
    // is enough. If it fails the dropdown falls back to a CPU-only
    // entry so deploys still work.
    api
      .deployComputeOptions()
      .then(setComputeOptions)
      .catch(() => undefined);
  }, []);

  const onDeploy = async (modelId: string, compute: string) => {
    setDeploying(modelId);
    try {
      await api.deployModel(modelId, compute);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeploying(null);
    }
  };

  const onStop = async (deploymentId: string) => {
    setStopping(deploymentId);
    try {
      await api.stopDeployment(deploymentId);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStopping(null);
    }
  };

  const onRotateKey = async (modelId: string) => {
    setRotating(modelId);
    try {
      await api.rotateModelKey(modelId);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRotating(null);
    }
  };

  // Search filter (name / description / framework / project name).
  // Applied before grouping so a hit pulls a project's whole header
  // along with the matching row(s).
  const q = query.trim().toLowerCase();
  const filteredModels = useMemo(() => {
    if (!q) return models;
    return models.filter((m) => {
      const hay = [
        m.name,
        m.description,
        m.framework,
        m.project_name,
        `v${m.version}`,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [models, q]);

  // Group models by project. Projects with no models are dropped — the
  // sidebar already lists them, the catalog only cares about projects
  // that have something to show.
  const grouped = useMemo(() => {
    const by: Record<string, RegisteredModel[]> = {};
    for (const m of filteredModels) {
      (by[m.project_id] ||= []).push(m);
    }
    return projects
      .filter((p) => by[p.id])
      .map((p) => ({ project: p, models: by[p.id] }));
  }, [filteredModels, projects]);

  return (
    <div className="h-screen flex bg-black text-gray-200" id="main-content">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          <Box className="w-4 h-4 text-blue-400" />
          <h1 className="text-sm font-semibold text-white">Model registry</h1>
          <span className="text-[11px] text-gray-500">
            {filteredModels.length}/{models.length} model
            {models.length === 1 ? '' : 's'} across {grouped.length}{' '}
            project{grouped.length === 1 ? '' : 's'}
          </span>
          <div className="flex-1" />
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search models…"
              className="text-xs h-7 w-56 pl-7 pr-2 rounded-md bg-white/[0.04] border border-white/[0.06] text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500/40"
            />
          </div>
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

        <main className="flex-1 overflow-y-auto px-6 py-6">
          {error ? (
            <div className="px-3 py-2 mb-4 rounded-lg bg-red-500/15 border border-red-500/30 text-xs text-red-300">
              {error}
            </div>
          ) : null}

          {!loading && models.length === 0 ? (
            <div className="px-4 py-12 text-center bg-white/[0.02] border border-white/[0.06] rounded-xl">
              <Box className="w-8 h-8 text-gray-600 mx-auto mb-2" />
              <p className="text-sm text-gray-400">No models registered yet.</p>
              <p className="text-[11px] text-gray-600 mt-1">
                Run a training experiment and have the agent call <code>register-model</code> to
                see it here.
              </p>
            </div>
          ) : null}

          {grouped.map(({ project, models: pm }) => {
            const isCollapsed = collapsed[project.id];
            return (
              <section key={project.id} className="mb-6">
                <button
                  onClick={() =>
                    setCollapsed((c) => ({ ...c, [project.id]: !c[project.id] }))
                  }
                  className="w-full flex items-center gap-2 mb-2 text-left text-[11px] uppercase tracking-wide text-gray-400 hover:text-gray-200"
                >
                  {isCollapsed ? (
                    <ChevronRight className="w-3.5 h-3.5" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5" />
                  )}
                  <FolderOpen className="w-3.5 h-3.5 text-blue-400" />
                  <span className="text-gray-300 normal-case font-medium">{project.name}</span>
                  <span className="text-gray-600 normal-case">
                    {pm.length} model{pm.length === 1 ? '' : 's'}
                  </span>
                </button>
                {!isCollapsed ? (
                  <div className="space-y-3">
                    {pm.map((m) => {
                      const deps = deployments[m.id] || [];
                      const live = deps.find((d) => d.status === 'live');
                      return (
                        <ModelCard
                          key={m.id}
                          m={m}
                          deployments={deps}
                          computeOptions={computeOptions}
                          onDeploy={onDeploy}
                          onStop={onStop}
                          onRotateKey={onRotateKey}
                          deploying={deploying === m.id}
                          stopping={live ? stopping === live.id : false}
                          rotating={rotating === m.id}
                        />
                      );
                    })}
                  </div>
                ) : null}
              </section>
            );
          })}
        </main>
      </div>
    </div>
  );
}
