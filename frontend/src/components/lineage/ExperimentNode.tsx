'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FlaskConical } from 'lucide-react';

import type { LineageExperimentNode } from '@/lib/types';

interface Data {
  node: LineageExperimentNode;
}

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-50 border-amber-300 text-amber-900 hover:border-amber-400',
  prepping: 'bg-amber-50 border-amber-300 text-amber-900 hover:border-amber-400',
  training: 'bg-amber-100 border-amber-400 text-amber-900 hover:border-amber-500',
  trained: 'bg-emerald-50 border-emerald-300 text-emerald-900 hover:border-emerald-400',
  abandoned: 'bg-rose-50 border-rose-300 text-rose-900 hover:border-rose-400',
  failed: 'bg-rose-50 border-rose-300 text-rose-900 hover:border-rose-400',
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
    STATE_TONE[node.state] ?? 'bg-gray-50 border-gray-300 text-gray-800 hover:border-gray-400';

  return (
    <div
      className={`group w-[220px] h-[86px] rounded-2xl border px-4 py-2.5 shadow-sm transition-all hover:shadow-md cursor-pointer flex flex-col justify-center ${tone}`}
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
