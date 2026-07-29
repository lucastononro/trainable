import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { SSEStreamProvider, useSSEStream } from '../SSEStreamContext';
import type { SSEEvent } from '../types';
import { useNotebookSSE, type NotebookSSEHandlers } from './useNotebookSSE';

/** Captures the pub/sub bus so tests can publish as `connectSSE` would. */
let bus: ReturnType<typeof useSSEStream>;

function BusCapture({ children }: { children: ReactNode }) {
  bus = useSSEStream();
  return <>{children}</>;
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <SSEStreamProvider>
      <BusCapture>{children}</BusCapture>
    </SSEStreamProvider>
  );
}

function publish(sessionId: string, event: SSEEvent) {
  act(() => bus.publish(sessionId, event));
}

describe('SSEStreamContext', () => {
  it('fans out published events to subscribers and stops after unsubscribe', () => {
    renderHook(() => null, { wrapper });
    const seen: Array<[string, SSEEvent]> = [];
    const unsubscribe = bus.subscribe((sid, e) => seen.push([sid, e]));

    const event: SSEEvent = { type: 'state_change', data: { state: 'running' } };
    publish('sess-1', event);
    expect(seen).toEqual([['sess-1', event]]);

    unsubscribe();
    publish('sess-1', event);
    expect(seen).toHaveLength(1);
  });
});

describe('useNotebookSSE', () => {
  function setup(sessionId: string | null, enabled = true, notebookName: string | null = 'nb') {
    const handlers = {
      onCellStarted: vi.fn(),
      onCellCompleted: vi.fn(),
      onKernelState: vi.fn(),
      onStructureChanged: vi.fn(),
    } satisfies NotebookSSEHandlers;
    renderHook(() => useNotebookSSE(sessionId, enabled, notebookName, handlers), { wrapper });
    return handlers;
  }

  it('dispatches notebook.* events for the subscribed session', () => {
    const handlers = setup('sess-1');
    publish('sess-1', {
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb', cell_id: 'c1' },
    });
    expect(handlers.onCellStarted).toHaveBeenCalledWith({ notebook_name: 'nb', cell_id: 'c1' });
  });

  it('ignores events published for a different session (no cross-session bleed)', () => {
    const handlers = setup('sess-1');
    publish('sess-2', {
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb', cell_id: 'c1' },
    });
    publish('sess-2', { type: 'notebook.kernel.state', data: { state: 'idle' } });
    expect(handlers.onCellStarted).not.toHaveBeenCalled();
    expect(handlers.onKernelState).not.toHaveBeenCalled();
  });

  it('filters cell events by notebook_name but always fires structure.changed', () => {
    const handlers = setup('sess-1', true, 'nb');
    publish('sess-1', {
      type: 'notebook.cell.completed',
      data: { notebook_name: 'other-nb', cell_id: 'c9' },
    });
    publish('sess-1', {
      type: 'notebook.structure.changed',
      data: { reason: 'agent_append', notebook_name: 'other-nb' },
    });
    expect(handlers.onCellCompleted).not.toHaveBeenCalled();
    expect(handlers.onStructureChanged).toHaveBeenCalledWith({
      reason: 'agent_append',
      notebook_name: 'other-nb',
    });
  });

  it('ignores non-notebook events and does nothing when disabled', () => {
    const active = setup('sess-1');
    publish('sess-1', { type: 'state_change', data: { state: 'running' } });
    expect(active.onKernelState).not.toHaveBeenCalled();

    const disabled = setup('sess-1', false);
    publish('sess-1', { type: 'notebook.kernel.state', data: { state: 'idle' } });
    expect(disabled.onKernelState).not.toHaveBeenCalled();
  });
});
