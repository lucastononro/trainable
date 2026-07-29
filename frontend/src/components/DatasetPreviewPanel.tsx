'use client';

import { useEffect, useState } from 'react';
import { Loader2, Table2, AlertTriangle } from 'lucide-react';
import { api } from '@/lib/api';
import type { RawDatasetPreview } from '@/lib/types';

interface Props {
  projectId: string;
  /** Volume path (or datasets-root-relative path) of the raw uploaded file. */
  path: string;
  /** Head rows to fetch (server caps at 200). */
  limit?: number;
}

function formatCell(value: string | number | boolean | null): string {
  if (value === null) return '∅';
  if (typeof value === 'number' && !Number.isInteger(value)) {
    // Keep floats readable without truncating integers or strings.
    return String(Math.round(value * 10000) / 10000);
  }
  return String(value);
}

function missingTone(pct: number): string {
  if (pct === 0) return 'text-gray-500';
  if (pct < 20) return 'text-amber-400';
  return 'text-rose-400';
}

/**
 * Raw dataset preview + quick profile, shown right after upload — before any
 * prep has run. Fetches head rows, dtypes, row/col counts, and per-column
 * missing %/cardinality from the raw file in the volume.
 */
export function DatasetPreviewPanel({ projectId, path, limit = 50 }: Props) {
  const [loading, setLoading] = useState(false);
  const [preview, setPreview] = useState<RawDatasetPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !path) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPreview(null);
    api
      .previewProjectDataset(projectId, path, limit)
      .then((res) => {
        if (!cancelled) setPreview(res);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message || 'Failed to load preview');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, path, limit]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-8 justify-center">
        <Loader2 className="w-4 h-4 animate-spin" />
        Scanning file…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <div className="break-all">{error}</div>
      </div>
    );
  }

  if (!preview) return null;

  return (
    <div className="space-y-4">
      {/* Summary line */}
      <div className="flex items-center gap-2 text-xs text-gray-400">
        <Table2 className="w-3.5 h-3.5 text-emerald-400" />
        <span className="font-medium text-gray-300">{preview.name}</span>
        <span className="text-gray-600">·</span>
        <span className="tabular-nums">
          {preview.row_count.toLocaleString()} row{preview.row_count === 1 ? '' : 's'}
        </span>
        <span className="text-gray-600">·</span>
        <span className="tabular-nums">
          {preview.column_count} column{preview.column_count === 1 ? '' : 's'}
        </span>
        <span className="text-gray-600">·</span>
        <span className="uppercase text-[10px] tracking-wider">{preview.format}</span>
      </div>

      {/* Per-column profile */}
      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-1.5">
          Columns
        </div>
        <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-gray-500 bg-white/[0.02]">
                <th className="px-3 py-1.5 font-medium">name</th>
                <th className="px-3 py-1.5 font-medium">dtype</th>
                <th className="px-3 py-1.5 font-medium text-right">missing</th>
                <th className="px-3 py-1.5 font-medium text-right">unique</th>
              </tr>
            </thead>
            <tbody>
              {preview.columns.map((c) => (
                <tr key={c.name} className="border-t border-white/[0.04]">
                  <td className="px-3 py-1.5 text-gray-300 font-mono">{c.name}</td>
                  <td className="px-3 py-1.5 text-sky-400/80 font-mono">{c.dtype}</td>
                  <td
                    className={`px-3 py-1.5 text-right tabular-nums ${missingTone(c.missing_pct)}`}
                  >
                    {c.missing_pct.toFixed(1)}%
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-gray-400">
                    {c.unique_count.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Head rows */}
      <div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold mb-1.5">
          First {preview.head_rows.length} row{preview.head_rows.length === 1 ? '' : 's'}
        </div>
        <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
          <table className="w-full text-xs whitespace-nowrap">
            <thead>
              <tr className="text-left text-gray-500 bg-white/[0.02]">
                {preview.head_columns.map((col) => (
                  <th key={col} className="px-3 py-1.5 font-medium font-mono">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.head_rows.map((row, i) => (
                <tr key={i} className="border-t border-white/[0.04]">
                  {row.map((cell, j) => (
                    <td
                      key={j}
                      className={`px-3 py-1.5 tabular-nums ${
                        cell === null ? 'text-gray-700' : 'text-gray-300'
                      }`}
                    >
                      {formatCell(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
