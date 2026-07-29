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
  // Tinted dark surface — readable on bg-surface without the white-on-white
  // glare of the original light-theme nodes. Borders pop just enough to
  // separate adjacent nodes; opacity-driven accents keep the eye calm.
  const tone = isRaw
    ? 'bg-slate-500/10 border-slate-500/40 text-slate-200 hover:border-slate-400/70'
    : 'bg-sky-500/10 border-sky-500/40 text-sky-200 hover:border-sky-400/70';

  return (
    <div
      className={`group w-[220px] h-[86px] rounded-2xl border px-4 py-2.5 shadow-sm shadow-black/20 transition-all hover:shadow-md cursor-pointer flex flex-col justify-center ${tone}`}
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
