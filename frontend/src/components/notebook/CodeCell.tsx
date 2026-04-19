'use client';

import { useEffect, useLayoutEffect, useRef } from 'react';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import type { CodeCell as CodeCellT } from '@/lib/notebook/types';
import { sourceToString } from '@/lib/notebook/types';
import CellOutputs from './CellOutputs';

SyntaxHighlighter.registerLanguage('python', python);

interface Props {
  cell: CodeCellT;
  running: boolean;
  kernelBusy: boolean;
  onChange: (source: string) => void;
  onRun: () => void;
}

// Exact pixel metrics shared between the highlighter layer and the textarea.
// If these drift, the cursor will sit off from the characters.
const CODE_FONT_FAMILY =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Courier New", monospace';
const CODE_FONT_SIZE = 13;
const CODE_LINE_HEIGHT = 20; // px — fixed for reliable alignment
const CODE_PADDING = '10px 14px';

export default function CodeCell({
  cell,
  running,
  kernelBusy,
  onChange,
  onRun,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const src = sourceToString(cell.source);

  // Auto-resize the textarea AND the (absolutely-positioned) highlight layer.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = '0px';
    const h = Math.max(el.scrollHeight, CODE_LINE_HEIGHT * 2);
    el.style.height = h + 'px';
  }, [src]);

  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && e.shiftKey) {
      e.preventDefault();
      onRun();
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      const el = e.currentTarget;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      const next = src.slice(0, start) + '    ' + src.slice(end);
      onChange(next);
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = start + 4;
      });
    }
  }

  const countLabel = running
    ? '[*]'
    : cell.execution_count != null
      ? `[${cell.execution_count}]`
      : '[ ]';

  const durationLabel =
    !running && typeof cell._last_duration_ms === 'number'
      ? cell._last_duration_ms < 1000
        ? `${cell._last_duration_ms} ms`
        : `${(cell._last_duration_ms / 1000).toFixed(1)} s`
      : null;

  // Shared style block keeps highlighter and textarea pixel-aligned.
  const sharedCodeStyle: React.CSSProperties = {
    fontFamily: CODE_FONT_FAMILY,
    fontSize: CODE_FONT_SIZE,
    lineHeight: `${CODE_LINE_HEIGHT}px`,
    tabSize: 4,
    whiteSpace: 'pre',
    margin: 0,
    padding: CODE_PADDING,
    border: 0,
  };

  return (
    <div className={`overflow-hidden rounded-lg border bg-[#1e1e1e] ${
      running ? 'border-sky-500/40' : 'border-neutral-800'
    }`}>
      <div className="flex items-stretch">
        <div className="flex w-14 shrink-0 select-none flex-col items-end border-r border-neutral-800 bg-neutral-950 px-2 pt-2.5 font-mono text-xs text-sky-400">
          <div>{countLabel}</div>
          {durationLabel && (
            <div className="mt-1 text-[10px] text-neutral-500">{durationLabel}</div>
          )}
        </div>

        <div className="relative flex-1 min-w-0">
          {/* Highlight layer — non-interactive, sits behind the textarea. */}
          <SyntaxHighlighter
            language="python"
            style={oneDark}
            PreTag="pre"
            customStyle={{
              ...sharedCodeStyle,
              position: 'absolute',
              inset: 0,
              background: 'transparent',
              pointerEvents: 'none',
              overflow: 'hidden',
            }}
            codeTagProps={{
              style: {
                fontFamily: CODE_FONT_FAMILY,
                fontSize: CODE_FONT_SIZE,
                lineHeight: `${CODE_LINE_HEIGHT}px`,
                tabSize: 4,
              },
            }}
          >
            {src || ' '}
          </SyntaxHighlighter>

          {/* Edit layer — transparent text, white caret, selection still visible. */}
          <textarea
            ref={textareaRef}
            value={src}
            spellCheck={false}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKey}
            placeholder="# Python — Shift+Enter to run, Tab = 4 spaces"
            style={{
              ...sharedCodeStyle,
              position: 'relative',
              width: '100%',
              resize: 'none',
              background: 'transparent',
              color: 'transparent',
              caretColor: '#e5e5e5',
              outline: 'none',
              overflow: 'hidden',
            }}
            className="placeholder:text-neutral-500/70 selection:bg-sky-500/30 selection:text-transparent"
          />
        </div>

        <div className="flex shrink-0 items-start border-l border-neutral-800 bg-neutral-950 p-1">
          <button
            onClick={onRun}
            disabled={kernelBusy && !running}
            title={kernelBusy && !running ? 'Kernel busy' : 'Run cell (Shift+Enter)'}
            className="rounded px-2 py-1 text-xs text-neutral-200 hover:bg-neutral-800 disabled:cursor-not-allowed disabled:text-neutral-600"
          >
            {running ? '…' : '▶'}
          </button>
        </div>
      </div>
      <CellOutputs outputs={cell.outputs} />
    </div>
  );
}
