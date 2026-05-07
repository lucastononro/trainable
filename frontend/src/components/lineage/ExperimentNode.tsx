'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { FlaskConical } from 'lucide-react';

import type { LineageExperimentNode } from '@/lib/types';

interface Data {
  node: LineageExperimentNode;
}

const STATE_TONE: Record<string, string> = {
  created: 'bg-amber-50 border-amber-300 text-amber-900',
  prepping: 'bg-amber-50 border-amber-300 text-amber-900',
  training: 'bg-amber-100 border-amber-400 text-amber-900',
  trained: 'bg-emerald-50 border-emerald-300 text-emerald-900',
  abandoned: 'bg-rose-50 border-rose-300 text-rose-900',
  failed: 'bg-rose-50 border-rose-300 text-rose-900',
};

export default function ExperimentNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const tone = STATE_TONE[node.state] ?? 'bg-gray-50 border-gray-300 text-gray-800';

  return (
    <div
      className={`min-w-[180px] max-w-[220px] rounded-2xl border-2 px-4 py-2.5 shadow-sm transition-shadow hover:shadow-md cursor-pointer ${tone}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-amber-400" />
      <Handle type="source" position={Position.Right} className="!bg-amber-400" />
      <div className="flex items-center gap-2 text-xs font-medium opacity-70 uppercase tracking-wide">
        <FlaskConical className="w-3 h-3" />
        Experiment · {node.state}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {node.hypothesis ? (
        <div className="mt-1 text-xs opacity-70 line-clamp-2" title={node.hypothesis}>
          {node.hypothesis}
        </div>
      ) : null}
    </div>
  );
}
