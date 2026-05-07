'use client';

import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Box } from 'lucide-react';

import type { LineageModelNode } from '@/lib/types';

interface Data {
  node: LineageModelNode;
}

export default function ModelNode({ data }: NodeProps) {
  const node = (data as unknown as Data).node;
  const topMetric = Object.entries(node.metrics_summary || {})[0];

  return (
    <div className="min-w-[180px] max-w-[220px] rounded-2xl border-2 border-violet-300 bg-violet-50 text-violet-900 px-4 py-2.5 shadow-sm transition-shadow hover:shadow-md cursor-pointer">
      <Handle type="target" position={Position.Left} className="!bg-violet-400" />
      <Handle type="source" position={Position.Right} className="!bg-violet-400" />
      <div className="flex items-center gap-2 text-xs font-medium opacity-70 uppercase tracking-wide">
        <Box className="w-3 h-3" />
        Model{node.framework ? ` · ${node.framework}` : ''}
      </div>
      <div className="mt-1 text-sm font-semibold truncate" title={node.name}>
        {node.name}
      </div>
      {topMetric ? (
        <div className="mt-1 text-xs opacity-70">
          {topMetric[0]}: {Number(topMetric[1]).toFixed(3)}
        </div>
      ) : null}
    </div>
  );
}
