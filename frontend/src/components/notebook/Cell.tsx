'use client';

import type { NotebookCell } from '@/lib/notebook/types';
import CodeCell from './CodeCell';
import MarkdownCell from './MarkdownCell';

interface Props {
  cell: NotebookCell;
  running: boolean;
  kernelBusy: boolean;
  sessionId: string;
  onChange: (source: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onConvertTo: (type: 'code' | 'markdown') => void;
  onInsertBelow: (type: 'code' | 'markdown') => void;
}

export default function Cell({
  cell,
  running,
  kernelBusy,
  sessionId,
  onChange,
  onRun,
  onDelete,
  onConvertTo,
  onInsertBelow,
}: Props) {
  return (
    <div className="group relative">
      {cell.cell_type === 'code' ? (
        <CodeCell
          cell={cell}
          running={running}
          kernelBusy={kernelBusy}
          onChange={onChange}
          onRun={onRun}
        />
      ) : (
        <MarkdownCell cell={cell} sessionId={sessionId} onChange={onChange} />
      )}
      <div className="absolute -top-3 right-2 z-10 hidden items-center gap-1 rounded border border-neutral-800 bg-neutral-950 px-1 py-0.5 text-[10px] text-neutral-400 shadow group-hover:flex">
        <button
          onClick={() =>
            onConvertTo(cell.cell_type === 'code' ? 'markdown' : 'code')
          }
          className="rounded px-1.5 py-0.5 hover:bg-neutral-800"
          title="Toggle cell type"
        >
          {cell.cell_type === 'code' ? 'md' : 'py'}
        </button>
        <button
          onClick={() => onInsertBelow('code')}
          className="rounded px-1.5 py-0.5 hover:bg-neutral-800"
          title="Insert code cell below"
        >
          + code
        </button>
        <button
          onClick={() => onInsertBelow('markdown')}
          className="rounded px-1.5 py-0.5 hover:bg-neutral-800"
          title="Insert markdown cell below"
        >
          + md
        </button>
        <button
          onClick={onDelete}
          className="rounded px-1.5 py-0.5 text-rose-400 hover:bg-neutral-800"
          title="Delete cell"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
