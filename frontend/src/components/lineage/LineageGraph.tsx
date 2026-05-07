'use client';

/**
 * LineageGraph — renders a {nodes, edges} payload from the backend lineage
 * endpoints as an n8n-style flow with rounded-rectangle nodes and smooth
 * edges. Three node kinds (dataset, experiment, model) get their own custom
 * components so we can tune label + color hint without conditionals
 * scattered through the renderer.
 *
 * Layout is computed via a simple longest-path leveling: raw datasets at
 * x=0, processed at the next column based on parent depth, experiments
 * after their inputs, models after their experiment. The user can still
 * drag nodes around; we just supply sensible initial positions.
 */

import { useMemo } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';

import '@xyflow/react/dist/style.css';

import type { LineageEdge, LineageGraph as LineageGraphPayload, LineageNode } from '@/lib/types';
import DatasetNode from './DatasetNode';
import ExperimentNode from './ExperimentNode';
import ModelNode from './ModelNode';

const NODE_TYPES = {
  dataset: DatasetNode,
  experiment: ExperimentNode,
  model: ModelNode,
};

interface Props {
  data: LineageGraphPayload | null;
  loading?: boolean;
  onNodeClick?: (node: LineageNode) => void;
  height?: number | string;
}

const COL_WIDTH = 240;
const ROW_HEIGHT = 130;

function levelOf(nodeId: string, edges: LineageEdge[], cache: Map<string, number>): number {
  if (cache.has(nodeId)) return cache.get(nodeId)!;
  const incoming = edges.filter((e) => e.target === nodeId);
  if (incoming.length === 0) {
    cache.set(nodeId, 0);
    return 0;
  }
  // Mark in-progress to break cycles defensively.
  cache.set(nodeId, 0);
  const lvl = 1 + Math.max(...incoming.map((e) => levelOf(e.source, edges, cache)));
  cache.set(nodeId, lvl);
  return lvl;
}

function layout(payload: LineageGraphPayload): {
  nodes: Node[];
  edges: Edge[];
} {
  const cache = new Map<string, number>();
  const levels = new Map<string, number>();
  for (const n of payload.nodes) {
    levels.set(n.id, levelOf(n.id, payload.edges, cache));
  }
  // Bucket by level → row index per level.
  const rowCounters = new Map<number, number>();
  const positioned: Node[] = payload.nodes.map((n) => {
    const lvl = levels.get(n.id) ?? 0;
    const row = rowCounters.get(lvl) ?? 0;
    rowCounters.set(lvl, row + 1);
    return {
      id: n.id,
      type: n.type,
      data: { node: n },
      position: { x: lvl * COL_WIDTH, y: row * ROW_HEIGHT },
    } as Node;
  });

  const edges: Edge[] = payload.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'smoothstep',
    animated: e.kind === 'feeds',
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    style: { stroke: '#3b82f6', strokeWidth: 1.5 },
  }));

  return { nodes: positioned, edges };
}

export default function LineageGraph({ data, loading = false, onNodeClick, height = 600 }: Props) {
  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    return layout(data);
  }, [data]);

  if (loading) {
    return (
      <div className="flex items-center justify-center text-gray-500 text-sm" style={{ height }}>
        Loading lineage…
      </div>
    );
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center text-gray-500 text-sm" style={{ height }}>
        No lineage to show yet. Once an agent runs create-experiment + register-dataset +
        register-model, the graph will populate here.
      </div>
    );
  }

  return (
    <div style={{ height, width: '100%' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_, node) => {
          const payloadNode = (node.data as { node: LineageNode }).node;
          onNodeClick?.(payloadNode);
        }}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="#e5e7eb" />
        <Controls position="bottom-right" showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
