'use client';

/**
 * "Reproduce" action for a reproducibility snapshot.
 *
 * Re-executes the snapshot's captured scripts in a sandbox (backend
 * POST /sessions/{id}/snapshot/reproduce) and renders the metric diff
 * vs the original run, flagging drift. Self-contained (button + state
 * + result table) so the experiment detail page only mounts it.
 */

import { useState } from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Play, XCircle } from 'lucide-react';

import { api } from '@/lib/api';
import type { MetricDiffRow, ReproduceReport } from '@/lib/types';

const STATUS_TONE: Record<ReproduceReport['status'], string> = {
  match: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  drift: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  error: 'bg-rose-500/10 text-rose-300 border-rose-500/30',
};

const ROW_TONE: Record<MetricDiffRow['status'], string> = {
  match: 'text-emerald-300',
  drift: 'text-amber-300',
  missing: 'text-rose-300',
  new: 'text-sky-300',
};

function fmt(v: number | null): string {
  if (v === null || v === undefined) return '—';
  return Math.abs(v) !== 0 && (Math.abs(v) < 1e-4 || Math.abs(v) >= 1e6)
    ? v.toExponential(3)
    : v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
}

export default function SnapshotReproduce({ sessionId }: { sessionId: string }) {
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<ReproduceReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setReport(await api.reproduceSnapshot(sessionId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const inputsDirty =
    report && (!report.inputs.dataset_verified || !report.inputs.code_verified);

  return (
    <div className="mt-3 space-y-3">
      <div className="flex items-center gap-3">
        <button
          onClick={run}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-md border border-surface-border bg-white/[0.04] hover:bg-white/[0.08] px-2.5 py-1 text-xs text-gray-200 transition-colors disabled:opacity-50"
          title="Re-run the snapshot's captured scripts and diff resulting metrics against the original run"
        >
          {running ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Play className="w-3.5 h-3.5" />
          )}
          {running ? 'Reproducing…' : 'Reproduce'}
        </button>
        {report ? (
          <span
            className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border ${STATUS_TONE[report.status]}`}
          >
            {report.status === 'match' ? (
              <CheckCircle2 className="w-3 h-3" />
            ) : report.status === 'drift' ? (
              <AlertTriangle className="w-3 h-3" />
            ) : (
              <XCircle className="w-3 h-3" />
            )}
            {report.status === 'match'
              ? 'reproduced — metrics match'
              : report.status === 'drift'
                ? 'drift detected'
                : 'replay failed'}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 p-2 text-[11px] text-rose-300">
          {error}
        </div>
      ) : null}

      {inputsDirty ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-300">
          Workspace no longer matches the snapshot (
          {report!.inputs.changed_files.length} file
          {report!.inputs.changed_files.length === 1 ? '' : 's'} changed) — the replay
          ran against the current files, not the frozen ones.
        </div>
      ) : null}

      {report && report.status === 'error' ? (
        <pre className="rounded-md border border-surface-border bg-black/40 p-2 text-[10px] text-gray-400 max-h-40 overflow-auto whitespace-pre-wrap">
          {report.execution.stderr_tail || `exit code ${report.execution.returncode}`}
        </pre>
      ) : null}

      {report && report.metrics.rows.length ? (
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-gray-500 border-b border-surface-border">
              <th className="text-left font-medium py-1">metric</th>
              <th className="text-right font-medium py-1">original</th>
              <th className="text-right font-medium py-1">reproduced</th>
              <th className="text-right font-medium py-1">Δ</th>
              <th className="text-right font-medium py-1">status</th>
            </tr>
          </thead>
          <tbody>
            {report.metrics.rows.map((r) => (
              <tr key={r.name} className="border-b border-surface-border/50">
                <td className="py-1 text-gray-300">{r.name}</td>
                <td className="py-1 text-right font-mono text-gray-300">{fmt(r.original)}</td>
                <td className="py-1 text-right font-mono text-gray-300">{fmt(r.reproduced)}</td>
                <td className="py-1 text-right font-mono text-gray-400">{fmt(r.abs_diff)}</td>
                <td className={`py-1 text-right font-mono ${ROW_TONE[r.status]}`}>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {report && !report.metrics.rows.length ? (
        <div className="text-[11px] text-gray-500 italic">
          No metrics were recorded for this run or its reproduction.
        </div>
      ) : null}
    </div>
  );
}
