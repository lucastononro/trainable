'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Database } from 'lucide-react';

import type { LineageDatasetNode } from '@/lib/types';

interface Data {
  node: LineageDatasetNode;
}

export default function DatasetNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const isRaw = node.kind === 'raw';
  const tone = isRaw
    ? 'bg-slate-50 border-slate-300 text-slate-800'
    : 'bg-blue-50 border-blue-300 text-blue-900';

  return (
    <div
      className={`min-w-[180px] max-w-[220px] rounded-2xl border-2 px-4 py-2.5 shadow-sm transition-shadow hover:shadow-md cursor-pointer ${tone}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-blue-400" />
      <Handle type="source" position={Position.Right} className="!bg-blue-400" />
      <div className="flex items-center gap-2 text-xs font-medium opacity-70 uppercase tracking-wide">
        <Database className="w-3 h-3" />
        Data source: {isRaw ? 'Raw data' : 'Processed data'}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {node.description ? (
        <div className="mt-1 text-xs opacity-70 line-clamp-2" title={node.description}>
          {node.description}
        </div>
      ) : null}
    </div>
  );
}
