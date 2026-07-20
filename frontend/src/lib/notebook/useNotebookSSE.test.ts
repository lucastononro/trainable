import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNotebookSSE, type NotebookSSEHandlers } from './useNotebookSSE';

/**
 * jsdom has no EventSource implementation, and even if it did we want full
 * control over dispatch timing. This fake captures the URL it was opened
 * with and lets tests push messages directly via `emit`.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  close() {
    this.closed = true;
  }
}

function latestSource(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error('no EventSource was opened');
  return source;
}

describe('useNotebookSSE', () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mountHook(
    sessionId: string | null,
    enabled: boolean,
    notebookName: string | null,
    handlers: NotebookSSEHandlers,
  ) {
    return renderHook(
      ({ sessionId, enabled, notebookName, handlers }) =>
        useNotebookSSE(sessionId, enabled, notebookName, handlers),
      { initialProps: { sessionId, enabled, notebookName, handlers } },
    );
  }

  it('opens a stream scoped to the session id', () => {
    mountHook('sess-1', true, null, {});
    expect(latestSource().url).toBe('/api/sessions/sess-1/stream');
  });

  it('does not open a stream when disabled or sessionId is missing', () => {
    mountHook(null, true, null, {});
    mountHook('sess-1', false, null, {});
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('routes notebook.cell.started to onCellStarted when no filter is set', () => {
    const onCellStarted = vi.fn();
    mountHook('sess-1', true, null, { onCellStarted });

    latestSource().emit({
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb-a', cell_id: 'c1' },
    });

    expect(onCellStarted).toHaveBeenCalledTimes(1);
    expect(onCellStarted).toHaveBeenCalledWith({ notebook_name: 'nb-a', cell_id: 'c1' });
  });

  it('filters cell-lifecycle events to the given notebookName', () => {
    const onCellStarted = vi.fn();
    const onCellCompleted = vi.fn();
    mountHook('sess-1', true, 'nb-a', { onCellStarted, onCellCompleted });

    latestSource().emit({
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb-b', cell_id: 'other' },
    });
    latestSource().emit({
      type: 'notebook.cell.completed',
      data: { notebook_name: 'nb-a', cell_id: 'mine', exec_count: 1 },
    });

    expect(onCellStarted).not.toHaveBeenCalled();
    expect(onCellCompleted).toHaveBeenCalledTimes(1);
    expect(onCellCompleted).toHaveBeenCalledWith({
      notebook_name: 'nb-a',
      cell_id: 'mine',
      exec_count: 1,
    });
  });

  it('fires onKernelState and onStructureChanged regardless of the notebook filter', () => {
    const onKernelState = vi.fn();
    const onStructureChanged = vi.fn();
    mountHook('sess-1', true, 'nb-a', { onKernelState, onStructureChanged });

    latestSource().emit({ type: 'notebook.kernel.state', data: { state: 'busy' } });
    latestSource().emit({
      type: 'notebook.structure.changed',
      data: { reason: 'agent_append', notebook_name: 'nb-other' },
    });

    expect(onKernelState).toHaveBeenCalledWith({ state: 'busy' });
    expect(onStructureChanged).toHaveBeenCalledWith({
      reason: 'agent_append',
      notebook_name: 'nb-other',
    });
  });

  it('routes notebook.created to onNotebookCreated even when a filter is set', () => {
    const onNotebookCreated = vi.fn();
    mountHook('sess-1', true, 'nb-a', { onNotebookCreated });

    latestSource().emit({
      type: 'notebook.created',
      data: { notebook_name: 'nb-new', notebook_path: '/notebooks/nb-new.ipynb' },
    });

    expect(onNotebookCreated).toHaveBeenCalledTimes(1);
    expect(onNotebookCreated).toHaveBeenCalledWith({
      notebook_name: 'nb-new',
      notebook_path: '/notebooks/nb-new.ipynb',
    });
  });

  it('applies the latest notebookName filter without re-opening the stream', () => {
    const onCellStarted = vi.fn();
    const handlers = { onCellStarted };
    const { rerender } = mountHook('sess-1', true, 'nb-a', handlers);

    rerender({ sessionId: 'sess-1', enabled: true, notebookName: 'nb-b', handlers });

    // Still the same underlying EventSource — the effect didn't re-run.
    expect(FakeEventSource.instances).toHaveLength(1);

    latestSource().emit({
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb-a', cell_id: 'stale' },
    });
    latestSource().emit({
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb-b', cell_id: 'fresh' },
    });

    expect(onCellStarted).toHaveBeenCalledTimes(1);
    expect(onCellStarted).toHaveBeenCalledWith({ notebook_name: 'nb-b', cell_id: 'fresh' });
  });

  it('ignores events whose type does not start with "notebook."', () => {
    const onCellStarted = vi.fn();
    mountHook('sess-1', true, null, { onCellStarted });

    latestSource().emit({ type: 'chat.message', data: { notebook_name: 'nb-a' } });

    expect(onCellStarted).not.toHaveBeenCalled();
  });

  it('swallows malformed (non-JSON) event payloads instead of throwing', () => {
    const onCellStarted = vi.fn();
    mountHook('sess-1', true, null, { onCellStarted });
    const source = latestSource();

    expect(() => source.onmessage?.({ data: '{not json' } as MessageEvent)).not.toThrow();
    expect(onCellStarted).not.toHaveBeenCalled();
  });

  it('closes the EventSource on unmount', () => {
    const { unmount } = mountHook('sess-1', true, null, {});
    const source = latestSource();
    expect(source.closed).toBe(false);

    unmount();

    expect(source.closed).toBe(true);
  });

  it('dispatches to the latest handlers even though the effect only re-runs on sessionId/enabled changes', () => {
    const onCellStartedFirst = vi.fn();
    const onCellStartedSecond = vi.fn();
    const { rerender } = mountHook('sess-1', true, null, {
      onCellStarted: onCellStartedFirst,
    });

    rerender({
      sessionId: 'sess-1',
      enabled: true,
      notebookName: null,
      handlers: { onCellStarted: onCellStartedSecond },
    });

    // Still the same underlying EventSource — the effect didn't re-run.
    expect(FakeEventSource.instances).toHaveLength(1);

    latestSource().emit({
      type: 'notebook.cell.started',
      data: { notebook_name: 'nb-a', cell_id: 'c1' },
    });

    expect(onCellStartedFirst).not.toHaveBeenCalled();
    expect(onCellStartedSecond).toHaveBeenCalledTimes(1);
  });
});
