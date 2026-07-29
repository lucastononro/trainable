'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box } from 'lucide-react';

import type { LineageModelNode } from '@/lib/types';

interface Data {
  node: LineageModelNode;
}

const HANDLE_STYLE: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  width: 8,
  height: 8,
};

// Order splits sensibly when iterating: train first, val, test, then any
// custom roles in alphabetical order. Without this, the model card might
// list "test" above "train" depending on dict insertion order.
const SPLIT_ORDER = ['train', 'val', 'validation', 'test', 'holdout'];
function orderSplits(refs: Record<string, unknown>): string[] {
  const keys = Object.keys(refs);
  return keys.sort((a, b) => {
    const ai = SPLIT_ORDER.indexOf(a);
    const bi = SPLIT_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
}

const SPLIT_TINT: Record<string, string> = {
  train: 'text-emerald-300',
  val: 'text-sky-300',
  validation: 'text-sky-300',
  test: 'text-amber-300',
};

export default function ModelNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const refs = node.dataset_refs || {};
  const splits = orderSplits(refs);
  // Pick a "headline" metric per split — the first numeric value. The
  // splits row stays compact; the side panel shows the full dict.
  const headline = (m: Record<string, number> | undefined): string | null => {
    if (!m) return null;
    const entry = Object.entries(m)[0];
    if (!entry) return null;
    const [k, v] = entry;
    return `${k} ${Number(v).toFixed(3)}`;
  };
  // If we have per-split metrics, render them. Otherwise fall back to
  // the legacy "first metric_summary entry" layout so old rows still
  // look reasonable.
  const fallbackTopMetric = Object.entries(node.metrics_summary || {})[0];

  // Card height adapts to content — base 86px, +14px per visible split row.
  const heightPx = splits.length > 0 ? 86 + Math.min(splits.length, 3) * 14 : 86;

  return (
    <div
      className="group w-[240px] rounded-2xl border border-violet-500/40 bg-violet-500/10 text-violet-200 px-4 py-2.5 shadow-sm shadow-black/20 transition-all hover:shadow-md hover:border-violet-400/70 cursor-pointer flex flex-col justify-center"
      style={{ height: heightPx }}
    >
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <div className="flex items-center gap-1.5 text-[10px] font-medium opacity-70 uppercase tracking-wide">
        <Box className="w-3 h-3" />
        Model{node.framework ? ` · ${node.framework}` : ''}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {splits.length > 0 ? (
        <div className="mt-1 space-y-0.5 text-[11px] leading-tight">
          {splits.slice(0, 3).map((role) => {
            const ref = refs[role];
            const label = headline(ref?.metrics);
            return (
              <div key={role} className="flex items-center gap-1.5">
                <span
                  className={`uppercase tracking-wide text-[9px] font-medium ${SPLIT_TINT[role] || 'opacity-60'}`}
                >
                  {role}
                </span>
                <span className="opacity-80">
                  {label ?? <span className="opacity-40">no metrics</span>}
                </span>
              </div>
            );
          })}
        </div>
      ) : fallbackTopMetric ? (
        <div className="text-[11px] opacity-70">
          {fallbackTopMetric[0]}: {Number(fallbackTopMetric[1]).toFixed(3)}
        </div>
      ) : null}
    </div>
  );
}
