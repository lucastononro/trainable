'use client';

/**
 * LineageGraph — renders a {nodes, edges} payload from the backend lineage
 * endpoints as a left-to-right DAG with rounded-rectangle nodes and
 * smooth bezier edges. Layout is computed via dagre (hierarchical layered
 * graph) so columns line up cleanly even when the graph branches.
 *
 * The node renderers (DatasetNode / ModelNode) are kept lean — color,
 * label, and a one-line subtitle — so the canvas reads cleanly at the
 * in-session 30/70 split. Experiment-type nodes were intentionally
 * dropped: the layout is Data → Models with the experiment implicit
 * (each model carries experiment_id internally), matching the
 * reference screenshot the user supplied.
 */

import { useMemo } from 'react';
import dagre from '@dagrejs/dagre';
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';

import '@xyflow/react/dist/style.css';

import type { LineageGraph as LineageGraphPayload, LineageNode } from '@/lib/types';
import DatasetNode from './DatasetNode';
import ModelNode from './ModelNode';

const NODE_TYPES = {
  dataset: DatasetNode,
  model: ModelNode,
};

const NODE_W = 220;
const NODE_H = 86;

interface Props {
  data: LineageGraphPayload | null;
  loading?: boolean;
  onNodeClick?: (node: LineageNode) => void;
  height?: number | string;
}

function layout(payload: LineageGraphPayload): {
  nodes: Node[];
  edges: Edge[];
} {
  const g = new dagre.graphlib.Graph({ compound: false })
    .setDefaultEdgeLabel(() => ({}))
    .setGraph({
      rankdir: 'LR',
      // Comfortable spacing between columns and within a level so the
      // graph reads at a glance at the in-session 30/70 split width.
      ranksep: 90,
      nodesep: 36,
      edgesep: 16,
      marginx: 24,
      marginy: 24,
    });

  for (const n of payload.nodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of payload.edges) {
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  const positioned: Node[] = payload.nodes.map((n) => {
    const layoutNode = g.node(n.id);
    return {
      id: n.id,
      type: n.type,
      data: { node: n },
      // dagre centers nodes; React Flow positions by top-left.
      position: {
        x: (layoutNode?.x ?? 0) - NODE_W / 2,
        y: (layoutNode?.y ?? 0) - NODE_H / 2,
      },
      sourcePosition: 'right' as const,
      targetPosition: 'left' as const,
    } as unknown as Node;
  });

  // Edge colour by role: train=emerald, val=sky, test=amber, anything
  // else falls back to the neutral sky used for derives_from. The label
  // shows the role so the canvas reads "test", "val", "train" without
  // having to click into the model card.
  const ROLE_COLORS: Record<string, string> = {
    train: '#34d399',
    val: '#7dd3fc',
    validation: '#7dd3fc',
    test: '#fbbf24',
  };
  const edges: Edge[] = payload.edges.map((e) => {
    // Backend collapses overlapping role edges (same source+target)
    // into one with `roles: [...]` — the edge label shows all roles.
    // For single-role edges fall back to the legacy `role` field.
    const roles =
      e.roles && e.roles.length > 0
        ? e.roles.filter((r) => r !== 'legacy')
        : e.role && e.role !== 'legacy'
          ? [e.role]
          : [];
    const isMultiRole = roles.length > 1;
    // For multi-role edges use a neutral sky stroke so no single split
    // dominates the colour. Single-role uses its split colour.
    const primaryRole = roles[0];
    const color =
      e.kind === 'trained_into'
        ? isMultiRole
          ? '#7dd3fc'
          : (ROLE_COLORS[primaryRole ?? ''] ?? '#7dd3fc')
        : '#7dd3fc';
    const label = isMultiRole
      ? roles.map((r) => r.toUpperCase()).join(' / ')
      : primaryRole;
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: 'smoothstep',
      animated: e.kind === 'feeds',
      label,
      labelStyle: {
        fill: color,
        fontSize: 10,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      },
      labelBgStyle: { fill: '#0b1220', fillOpacity: 0.85 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color,
      },
      // Multi-role edges get a slightly thicker stroke so they read as
      // "this carries multiple roles" without visual ambiguity.
      style: { stroke: color, strokeWidth: isMultiRole ? 2.0 : 1.6 },
    };
  });

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
      <div
        className="flex items-center justify-center text-gray-500 text-sm px-6 text-center"
        style={{ height }}
      >
        No lineage to show yet. Once an agent runs create-experiment + register-dataset +
        register-model, the graph will populate here.
      </div>
    );
  }

  return (
    <div style={{ height, width: '100%' }} className="bg-surface">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeClick={(_, node) => {
          const payloadNode = (node.data as { node: LineageNode }).node;
          onNodeClick?.(payloadNode);
        }}
        fitView
        fitViewOptions={{ padding: 0.18, minZoom: 0.4, maxZoom: 1.2 }}
        nodesDraggable
        nodesConnectable={false}
        edgesFocusable={false}
        proOptions={{ hideAttribution: true }}
      >
        {/* Dim slate dots so the grid reads as ambient texture against
            the dark surface, not a competing pattern. */}
        <Background gap={22} size={1.2} color="#1f2937" variant={BackgroundVariant.Dots} />
        <Controls
          position="bottom-right"
          showInteractive={false}
          className="!bg-surface !border !border-surface-border !rounded-md !shadow-sm [&_button]:!bg-surface [&_button]:!border-surface-border [&_button]:!text-gray-300 [&_button:hover]:!bg-white/[0.06]"
        />
      </ReactFlow>
    </div>
  );
}
