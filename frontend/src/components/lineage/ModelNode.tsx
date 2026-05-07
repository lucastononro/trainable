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

export default function ModelNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const topMetric = Object.entries(node.metrics_summary || {})[0];

  return (
    <div className="group w-[220px] h-[86px] rounded-2xl border border-violet-500/40 bg-violet-500/10 text-violet-200 px-4 py-2.5 shadow-sm shadow-black/20 transition-all hover:shadow-md hover:border-violet-400/70 cursor-pointer flex flex-col justify-center">
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <div className="flex items-center gap-1.5 text-[10px] font-medium opacity-70 uppercase tracking-wide">
        <Box className="w-3 h-3" />
        Model{node.framework ? ` · ${node.framework}` : ''}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {topMetric ? (
        <div className="text-[11px] opacity-70">
          {topMetric[0]}: {Number(topMetric[1]).toFixed(3)}
        </div>
      ) : null}
    </div>
  );
}
