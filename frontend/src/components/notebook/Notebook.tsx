'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { notebookApi } from '@/lib/notebook/api';
import { useNotebookSSE } from '@/lib/notebook/useNotebookSSE';
import type {
  CodeCell as CodeCellT,
  KernelState,
  Notebook as NotebookT,
  NotebookCell,
} from '@/lib/notebook/types';
import { sourceToString } from '@/lib/notebook/types';
import Cell from './Cell';
import KernelStatusBadge from './KernelStatusBadge';

interface Props {
  sessionId: string;
  notebookName: string;
  onClose?: () => void;
  variant?: 'inline' | 'fullscreen';
}

function emptyNotebook(): NotebookT {
  return { cells: [], metadata: {}, nbformat: 4, nbformat_minor: 5 };
}

function newId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
}

function newCodeCell(source = ''): CodeCellT {
  return {
    id: newId(),
    cell_type: 'code',
    source,
    outputs: [],
    execution_count: null,
    metadata: {},
  };
}

function newMarkdownCell(source = ''): NotebookCell {
  return { id: newId(), cell_type: 'markdown', source, metadata: {} };
}

export default function Notebook({ sessionId, notebookName, onClose, variant = 'inline' }: Props) {
  const [nb, setNb] = useState<NotebookT>(emptyNotebook);
  const [kernelState, setKernelState] = useState<KernelState>('dead');
  const [running, setRunning] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nbRef = useRef<NotebookT>(nb);
  nbRef.current = nb;
  const completionResolversRef = useRef<Map<string, () => void>>(new Map());
  const kernelStateRef = useRef<KernelState>('dead');
  kernelStateRef.current = kernelState;

  // Initial load — open() creates the notebook if missing + pre-warms kernel.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    notebookApi
      .open(sessionId, notebookName)
      .then((loaded) => {
        if (!cancelled) {
          setNb(loaded);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, notebookName]);

  useEffect(() => {
    notebookApi
      .status(sessionId)
      .then((s) => setKernelState(s.state))
      .catch(() => {});
  }, [sessionId]);

  const scheduleSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      notebookApi.put(sessionId, notebookName, nbRef.current).catch((e) => {
        console.warn('notebook save failed', e);
      });
    }, 800);
  }, [sessionId, notebookName]);

  const mutateCell = useCallback(
    (cellId: string, updater: (c: NotebookCell) => NotebookCell) => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) => (c.id === cellId ? updater(c) : c)),
      }));
      scheduleSave();
    },
    [scheduleSave],
  );

  const setCellSource = useCallback(
    (cellId: string, source: string) => {
      mutateCell(cellId, (c) => ({ ...c, source }));
    },
    [mutateCell],
  );

  const runCell = useCallback(
    async (cellId: string): Promise<void> => {
      const cell = nbRef.current.cells.find((c) => c.id === cellId);
      if (!cell || cell.cell_type !== 'code') return;
      const code = sourceToString(cell.source);
      setRunning((r) => ({ ...r, [cellId]: true }));
      mutateCell(cellId, (c) =>
        c.cell_type === 'code' ? { ...c, outputs: [], execution_count: null } : c,
      );
      const completion = new Promise<void>((resolve) => {
        completionResolversRef.current.set(cellId, resolve);
      });
      try {
        await notebookApi.executeCell(sessionId, notebookName, cellId, code);
      } catch (e) {
        console.warn('execute failed', e);
        setRunning((r) => {
          const next = { ...r };
          delete next[cellId];
          return next;
        });
        completionResolversRef.current.delete(cellId);
        return;
      }
      await completion;
    },
    [mutateCell, sessionId, notebookName],
  );

  const runCellIds = useCallback(
    async (ids: string[]) => {
      for (const id of ids) {
        const cell = nbRef.current.cells.find((c) => c.id === id);
        if (!cell || cell.cell_type !== 'code') continue;
        await runCell(id);
        const c = nbRef.current.cells.find((x) => x.id === id);
        if (c && c.cell_type === 'code') {
          if (c.outputs.some((o) => o.output_type === 'error')) break;
        }
      }
    },
    [runCell],
  );

  const runAll = useCallback(() => {
    const ids = nbRef.current.cells.filter((c) => c.cell_type === 'code').map((c) => c.id);
    return runCellIds(ids);
  }, [runCellIds]);

  const restartAndRunAll = useCallback(async () => {
    await notebookApi.shutdown(sessionId).catch(() => {});
    let tries = 0;
    while (kernelStateRef.current !== 'dead' && tries < 30) {
      await new Promise((r) => setTimeout(r, 200));
      tries++;
    }
    await runAll();
  }, [runAll, sessionId]);

  const deleteCell = useCallback(
    (cellId: string) => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.filter((c) => c.id !== cellId),
      }));
      scheduleSave();
    },
    [scheduleSave],
  );

  const convertCell = useCallback(
    (cellId: string, toType: 'code' | 'markdown') => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) => {
          if (c.id !== cellId) return c;
          const src = sourceToString(c.source);
          return toType === 'code'
            ? { ...newCodeCell(src), id: c.id }
            : { ...newMarkdownCell(src), id: c.id };
        }),
      }));
      scheduleSave();
    },
    [scheduleSave],
  );

  const insertBelow = useCallback(
    (cellId: string, type: 'code' | 'markdown') => {
      setNb((prev) => {
        const idx = prev.cells.findIndex((c) => c.id === cellId);
        if (idx < 0) return prev;
        const fresh = type === 'code' ? newCodeCell() : newMarkdownCell();
        const next = [...prev.cells];
        next.splice(idx + 1, 0, fresh);
        return { ...prev, cells: next };
      });
      scheduleSave();
    },
    [scheduleSave],
  );

  const appendCell = useCallback(
    (type: 'code' | 'markdown') => {
      setNb((prev) => ({
        ...prev,
        cells: [...prev.cells, type === 'code' ? newCodeCell() : newMarkdownCell()],
      }));
      scheduleSave();
    },
    [scheduleSave],
  );

  useNotebookSSE(sessionId, !loading, notebookName, {
    onKernelState: (e) => setKernelState(e.state),
    onStructureChanged: (e) => {
      if (e.notebook_name !== notebookName) return;
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      const localSources = new Map<string, string>();
      for (const c of nbRef.current.cells) {
        localSources.set(c.id, sourceToString(c.source));
      }
      notebookApi
        .get(sessionId, notebookName)
        .then((fresh) => {
          setNb({
            ...fresh,
            cells: fresh.cells.map((c) => {
              const localSrc = localSources.get(c.id);
              const serverSrc = sourceToString(c.source);
              if (localSrc != null && localSrc !== serverSrc) {
                return { ...c, source: localSrc };
              }
              return c;
            }),
          });
        })
        .catch(() => {});
    },
    onCellStarted: (e) => {
      setRunning((r) => ({ ...r, [e.cell_id]: true }));
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) =>
          c.id === e.cell_id && c.cell_type === 'code'
            ? { ...c, outputs: [], execution_count: null }
            : c,
        ),
      }));
    },
    onCellStream: (e) => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) => {
          if (c.id !== e.cell_id || c.cell_type !== 'code') return c;
          return {
            ...c,
            outputs: [...c.outputs, { output_type: 'stream', name: e.name, text: e.text }],
          };
        }),
      }));
    },
    onCellDisplay: (e) => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) => {
          if (c.id !== e.cell_id || c.cell_type !== 'code') return c;
          return {
            ...c,
            outputs: [
              ...c.outputs,
              {
                output_type: 'display_data',
                data: e.data,
                metadata: e.metadata || {},
              },
            ],
          };
        }),
      }));
    },
    onCellError: (e) => {
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) => {
          if (c.id !== e.cell_id || c.cell_type !== 'code') return c;
          return {
            ...c,
            outputs: [
              ...c.outputs,
              {
                output_type: 'error',
                ename: e.ename,
                evalue: e.evalue,
                traceback: e.traceback,
              },
            ],
          };
        }),
      }));
    },
    onCellCompleted: (e) => {
      setRunning((r) => {
        const next = { ...r };
        delete next[e.cell_id];
        return next;
      });
      setNb((prev) => ({
        ...prev,
        cells: prev.cells.map((c) =>
          c.id === e.cell_id && c.cell_type === 'code'
            ? {
                ...c,
                execution_count: e.exec_count ?? c.execution_count,
                _last_duration_ms: e.duration_ms ?? null,
              }
            : c,
        ),
      }));
      const resolver = completionResolversRef.current.get(e.cell_id);
      if (resolver) {
        completionResolversRef.current.delete(e.cell_id);
        resolver();
      }
    },
  });

  const kernelBusy = kernelState === 'busy' || kernelState === 'starting';
  const cellCount = nb.cells.length;

  const header = useMemo(
    () => (
      <div className="flex items-center justify-between border-b border-neutral-800 bg-neutral-950 px-3 py-1.5">
        <div className="flex items-center gap-3 min-w-0">
          <span className="truncate font-mono text-xs text-neutral-400">
            {notebookName}.ipynb · {cellCount} cells
          </span>
          <KernelStatusBadge
            state={kernelState}
            onStart={() => {
              setKernelState('starting');
              notebookApi.startKernel(sessionId).catch((e) => {
                console.warn('start kernel failed', e);
                setKernelState('dead');
              });
            }}
            onInterrupt={() => notebookApi.interrupt(sessionId)}
            onShutdown={() => notebookApi.shutdown(sessionId)}
          />
        </div>
        <div className="flex items-center gap-1 text-xs">
          <button
            onClick={() => runAll()}
            disabled={kernelBusy}
            className="rounded border border-neutral-700 px-2 py-0.5 text-neutral-200 hover:bg-neutral-800 disabled:opacity-50"
            title="Run all code cells"
          >
            ▶▶
          </button>
          <button
            onClick={() => restartAndRunAll()}
            className="rounded border border-neutral-700 px-2 py-0.5 text-neutral-200 hover:bg-neutral-800"
            title="Shutdown kernel then run every cell"
          >
            ↻
          </button>
          <div className="mx-1 h-4 w-px bg-neutral-800" />
          <button
            onClick={() => appendCell('code')}
            className="rounded border border-neutral-700 px-2 py-0.5 text-neutral-200 hover:bg-neutral-800"
          >
            + code
          </button>
          <button
            onClick={() => appendCell('markdown')}
            className="rounded border border-neutral-700 px-2 py-0.5 text-neutral-200 hover:bg-neutral-800"
          >
            + md
          </button>
          <a
            href={notebookApi.downloadUrl(sessionId, notebookName)}
            className="rounded border border-neutral-700 px-2 py-0.5 text-neutral-200 hover:bg-neutral-800"
            title="Download .ipynb"
          >
            ⬇
          </a>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100"
              title="Close"
            >
              ✕
            </button>
          )}
        </div>
      </div>
    ),
    [
      appendCell,
      cellCount,
      kernelBusy,
      kernelState,
      notebookName,
      onClose,
      restartAndRunAll,
      runAll,
      sessionId,
    ],
  );

  const containerCls =
    variant === 'fullscreen'
      ? 'flex h-full flex-col bg-neutral-900 text-neutral-100'
      : 'flex h-full min-h-0 flex-col bg-neutral-900 text-neutral-100';

  return (
    <div className={containerCls}>
      {header}
      {kernelState === 'starting' && (
        <div className="flex items-center gap-2 border-b border-amber-700/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-200">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-400" />
          Starting Modal sandbox kernel — first spin-up takes 20–60 s while the ML image boots.
          Cells you run now will queue and execute as soon as the kernel is ready.
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {loading && <div className="text-sm text-neutral-400">Loading notebook…</div>}
        {error && <div className="text-sm text-rose-400">Error: {error}</div>}
        {!loading && !error && (
          <div className="mx-auto flex max-w-3xl flex-col gap-2">
            {nb.cells.map((cell) => (
              <Cell
                key={cell.id}
                cell={cell}
                running={!!running[cell.id]}
                kernelBusy={kernelBusy}
                sessionId={sessionId}
                onChange={(src) => setCellSource(cell.id, src)}
                onRun={() => runCell(cell.id)}
                onDelete={() => deleteCell(cell.id)}
                onConvertTo={(t) => convertCell(cell.id, t)}
                onInsertBelow={(t) => insertBelow(cell.id, t)}
              />
            ))}
            {nb.cells.length === 0 && (
              <div className="rounded-lg border border-dashed border-neutral-800 p-6 text-center text-sm text-neutral-500">
                Empty notebook. Click{' '}
                <button className="underline" onClick={() => appendCell('code')}>
                  + code
                </button>{' '}
                to start — or let the agent fill it.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
