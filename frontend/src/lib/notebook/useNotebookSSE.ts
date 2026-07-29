import { useEffect, useRef } from 'react';
import { useSSEStream } from '../SSEStreamContext';
import type { SSEEvent } from '../types';
import type {
  CellCompletedEvent,
  CellDisplayEvent,
  CellErrorEvent,
  CellStartedEvent,
  CellStreamEvent,
  KernelStateEvent,
  NotebookCreatedEvent,
} from './types';

export interface StructureChangedEvent {
  reason: 'agent_append' | string;
  notebook_name: string;
  notebook_path?: string;
  cell_id?: string;
  total_cells?: number;
}

export interface NotebookSSEHandlers {
  onKernelState?: (e: KernelStateEvent) => void;
  onNotebookCreated?: (e: NotebookCreatedEvent) => void;
  onCellStarted?: (e: CellStartedEvent) => void;
  onCellStream?: (e: CellStreamEvent) => void;
  onCellDisplay?: (e: CellDisplayEvent) => void;
  onCellError?: (e: CellErrorEvent) => void;
  onCellCompleted?: (e: CellCompletedEvent) => void;
  onStructureChanged?: (e: StructureChangedEvent) => void;
}

/**
 * Subscribe to the session's SSE stream and dispatch `notebook.*` events.
 * When `notebookName` is provided, cell-lifecycle events are filtered to
 * that notebook only — kernel/notebook-created/structure events always fire
 * (they inform listeners about cross-notebook state).
 *
 * This consumes the single page-level EventSource via `SSEStreamContext`
 * rather than opening a second EventSource to the identical
 * `/api/sessions/{id}/stream` endpoint — `HomePage.connectSSE` already owns
 * that connection and republishes every parsed message here.
 */
export function useNotebookSSE(
  sessionId: string | null,
  enabled: boolean,
  notebookName: string | null,
  handlers: NotebookSSEHandlers,
) {
  const { subscribe } = useSSEStream();
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;
  const filterRef = useRef(notebookName);
  filterRef.current = notebookName;

  useEffect(() => {
    if (!sessionId || !enabled) return;

    const handler = (eventSessionId: string, msg: SSEEvent) => {
      // The bus is page-global while the old EventSource-per-hook design was
      // implicitly session-scoped: during a session switch, events from the
      // new connection can be published before React flushes this effect's
      // cleanup. Drop anything not belonging to our session.
      if (eventSessionId !== sessionId) return;
      const type = msg?.type as string | undefined;
      if (!type || !type.startsWith('notebook.')) return;
      const data = (msg.data ?? {}) as any;
      const h = handlersRef.current;
      const belongsToThisNotebook = !filterRef.current || data.notebook_name === filterRef.current;

      switch (type) {
        case 'notebook.kernel.state':
          h.onKernelState?.(data);
          break;
        case 'notebook.created':
          h.onNotebookCreated?.(data);
          break;
        case 'notebook.structure.changed':
          // Always fire — callers route on notebook_name themselves.
          h.onStructureChanged?.(data);
          break;
        case 'notebook.cell.started':
          if (belongsToThisNotebook) h.onCellStarted?.(data);
          break;
        case 'notebook.cell.stream':
          if (belongsToThisNotebook) h.onCellStream?.(data);
          break;
        case 'notebook.cell.display':
          if (belongsToThisNotebook) h.onCellDisplay?.(data);
          break;
        case 'notebook.cell.error':
          if (belongsToThisNotebook) h.onCellError?.(data);
          break;
        case 'notebook.cell.completed':
          if (belongsToThisNotebook) h.onCellCompleted?.(data);
          break;
      }
    };

    return subscribe(handler);
  }, [sessionId, enabled, subscribe]);
}
