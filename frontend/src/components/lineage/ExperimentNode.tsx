'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FlaskConical } from 'lucide-react';

import type { LineageExperimentNode } from '@/lib/types';

interface Data {
  node: LineageExperimentNode;
}

// Tinted dark surface — same approach as DatasetNode/ModelNode. State
// drives both the tint hue and the border accent so the user can read
// the lifecycle at a glance even without parsing the label text.
const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-500/10 border-amber-500/40 text-amber-200 hover:border-amber-400/70',
  prepping: 'bg-amber-500/10 border-amber-500/40 text-amber-200 hover:border-amber-400/70',
  training: 'bg-amber-500/20 border-amber-400/60 text-amber-100 hover:border-amber-400/80',
  trained: 'bg-emerald-500/10 border-emerald-500/40 text-emerald-200 hover:border-emerald-400/70',
  abandoned: 'bg-rose-500/10 border-rose-500/40 text-rose-200 hover:border-rose-400/70',
  failed: 'bg-rose-500/10 border-rose-500/40 text-rose-200 hover:border-rose-400/70',
};

const HANDLE_STYLE: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  width: 8,
  height: 8,
};

export default function ExperimentNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const tone =
    STATE_TONE[node.state] ??
    'bg-white/[0.04] border-white/[0.12] text-gray-200 hover:border-white/[0.22]';

  return (
    <div
      className={`group w-[220px] h-[86px] rounded-2xl border px-4 py-2.5 shadow-sm shadow-black/20 transition-all hover:shadow-md cursor-pointer flex flex-col justify-center ${tone}`}
    >
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <div className="flex items-center gap-1.5 text-[10px] font-medium opacity-70 uppercase tracking-wide">
        <FlaskConical className="w-3 h-3" />
        Experiment · {node.state}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {node.hypothesis ? (
        <div className="text-[11px] opacity-70 line-clamp-1" title={node.hypothesis}>
          {node.hypothesis}
        </div>
      ) : null}
    </div>
  );
}
