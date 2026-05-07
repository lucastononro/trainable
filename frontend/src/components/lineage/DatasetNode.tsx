'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Database } from 'lucide-react';

import type { LineageDatasetNode } from '@/lib/types';

interface Data {
  node: LineageDatasetNode;
}

const HANDLE_STYLE: React.CSSProperties = {
  background: 'transparent',
  border: 'none',
  width: 8,
  height: 8,
};

export default function DatasetNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const isRaw = node.kind === 'raw';
  const tone = isRaw
    ? 'bg-slate-50 border-slate-300 text-slate-800 hover:border-slate-400'
    : 'bg-blue-50 border-blue-300 text-blue-900 hover:border-blue-400';

  return (
    <div
      className={`group w-[220px] h-[86px] rounded-2xl border px-4 py-2.5 shadow-sm transition-all hover:shadow-md cursor-pointer flex flex-col justify-center ${tone}`}
    >
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <div className="flex items-center gap-1.5 text-[10px] font-medium opacity-70 uppercase tracking-wide">
        <Database className="w-3 h-3" />
        Data source: {isRaw ? 'Raw data' : 'Processed data'}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {node.description ? (
        <div className="text-[11px] opacity-70 line-clamp-1" title={node.description}>
          {node.description}
        </div>
      ) : null}
    </div>
  );
}
