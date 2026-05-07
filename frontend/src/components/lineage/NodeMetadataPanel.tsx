'use client';

/**
 * Slide-in detail panel for a clicked lineage node. Dark-themed to
 * match the rest of the app shell — was previously a white panel that
 * jarred against bg-black on the in-session canvas tab.
 */

import { X } from 'lucide-react';

import type { LineageNode } from '@/lib/types';

interface Props {
  node: LineageNode | null;
  onClose: () => void;
}

function fmtBytes(n: number): string {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(1)} ${u[i]}`;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-3 py-1.5 text-sm">
      <div className="text-gray-500">{label}</div>
      <div className="text-gray-200 break-words">{value}</div>
    </div>
  );
}

export default function NodeMetadataPanel({ node, onClose }: Props) {
  if (!node) return null;

  const headerColor =
    node.type === 'dataset'
      ? 'text-sky-300'
      : node.type === 'experiment'
        ? 'text-amber-300'
        : 'text-violet-300';

  return (
    <aside className="fixed right-0 top-0 z-40 h-full w-[380px] bg-surface shadow-2xl border-l border-surface-border flex flex-col">
      <header className="flex items-start justify-between p-4 border-b border-surface-border">
        <div>
          <div className={`text-xs uppercase tracking-wide font-medium ${headerColor}`}>
            {node.type}
          </div>
          <h2 className="mt-1 text-base font-semibold text-gray-100">{node.name}</h2>
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 text-gray-500 hover:bg-white/[0.06] hover:text-gray-200"
          aria-label="Close"
        >
          <X className="w-4 h-4" />
        </button>
      </header>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {node.description ? (
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
              Description
            </h3>
            <p className="text-sm text-gray-300 leading-relaxed">{node.description}</p>
          </section>
        ) : (
          <section className="text-sm text-gray-500 italic">
            No AI-generated description yet — the agent didn&apos;t supply one at registration time.
          </section>
        )}

        {node.type === 'dataset' ? (
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
              Dataset
            </h3>
            <Row label="Kind" value={node.kind} />
            <Row
              label="Path"
              value={<span className="font-mono text-xs text-gray-400">{node.path}</span>}
            />
            <Row label="Size" value={fmtBytes(node.size_bytes)} />
            <Row
              label="Hash"
              value={
                <span className="font-mono text-xs text-gray-400">
                  {node.hash ? `${node.hash.slice(0, 12)}…` : '—'}
                </span>
              }
            />
            {node.metadata && Object.keys(node.metadata).length ? (
              <Row
                label="Metadata"
                value={
                  <pre className="font-mono text-xs whitespace-pre-wrap bg-black/40 border border-surface-border rounded p-2 text-gray-300">
                    {JSON.stringify(node.metadata, null, 2)}
                  </pre>
                }
              />
            ) : null}
          </section>
        ) : null}

        {node.type === 'experiment' ? (
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
              Experiment
            </h3>
            <Row label="State" value={node.state} />
            {node.hypothesis ? <Row label="Hypothesis" value={node.hypothesis} /> : null}
            <Row label="Started" value={node.started_at || '—'} />
            <Row label="Completed" value={node.completed_at || '—'} />
            <Row
              label="Session"
              value={
                node.session_id ? (
                  <a
                    className="text-sky-400 hover:text-sky-300 hover:underline font-mono text-xs"
                    href={`/?session=${node.session_id}`}
                  >
                    {node.session_id.slice(0, 8)}…
                  </a>
                ) : (
                  '—'
                )
              }
            />
          </section>
        ) : null}

        {node.type === 'model' ? (
          <section>
            <h3 className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-1">
              Model
            </h3>
            <Row label="Framework" value={node.framework || '—'} />
            <Row label="Version" value={`v${node.version}`} />
            {Object.keys(node.metrics_summary || {}).length ? (
              <Row
                label="Metrics"
                value={
                  <pre className="font-mono text-xs whitespace-pre-wrap bg-black/40 border border-surface-border rounded p-2 text-gray-300">
                    {JSON.stringify(node.metrics_summary, null, 2)}
                  </pre>
                }
              />
            ) : null}
            {Object.keys(node.hyperparams || {}).length ? (
              <Row
                label="Hyperparams"
                value={
                  <pre className="font-mono text-xs whitespace-pre-wrap bg-black/40 border border-surface-border rounded p-2 text-gray-300">
                    {JSON.stringify(node.hyperparams, null, 2)}
                  </pre>
                }
              />
            ) : null}
          </section>
        ) : null}

        <Row label="Created" value={node.created_at || '—'} />
      </div>
    </aside>
  );
}
