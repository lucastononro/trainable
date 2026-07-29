'use client';

import { useState } from 'react';
import { Paperclip, X } from 'lucide-react';

// ---------------------------------------------------------------------------
// AttachedFilesPreview — staged-attachment preview that sits above the
// chat input. Used in both the home (no-session) input and the in-session
// input. Same goal as UserMessageFilePills: when the user picks a folder
// of thousands of files, do NOT render every pill — that pushes the
// input bar off-screen and makes Send unreachable. Cap to a preview of
// ~6 with a "+N more" toggle and a "clear all" escape hatch. Also nudge
// the user toward the S3 upload script when the count gets silly.
// ---------------------------------------------------------------------------

const STAGED_PILL_PREVIEW = 6;
// Threshold for the "use the script" hint. Browser multipart uploads of
// many small files are slow on the backend (per-file S3 + Modal Volume
// round-trips), so steer the user to upload_to_s3.py for big sets.
const BULK_UPLOAD_HINT_THRESHOLD = 100;

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function AttachedFilesPreview({
  files,
  onRemove,
  onClearAll,
  variant,
}: {
  files: File[];
  onRemove: (i: number) => void;
  onClearAll: () => void;
  /** "home" = larger pills (above the centered welcome input).
   *  "session" = compact pills (above the in-session input). */
  variant: 'home' | 'session';
}) {
  const [expanded, setExpanded] = useState(false);
  if (files.length === 0) return null;

  const overflow = files.length - STAGED_PILL_PREVIEW;
  const visible = expanded || overflow <= 0 ? files : files.slice(0, STAGED_PILL_PREVIEW);
  const totalBytes = files.reduce((s, f) => s + f.size, 0);
  const showHint = files.length >= BULK_UPLOAD_HINT_THRESHOLD;

  const pillCls =
    variant === 'home'
      ? 'flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.06] border border-white/[0.08] text-xs text-gray-300'
      : 'flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/[0.06] border border-white/[0.08] text-xs text-gray-300';
  const wrapCls = variant === 'home' ? 'flex flex-wrap gap-2' : 'flex flex-wrap gap-2 mb-2';

  return (
    <div className={variant === 'home' ? '' : 'mb-2'}>
      {/* Summary row: total count + size + clear-all */}
      <div className="flex items-center justify-between mb-1.5 text-[11px] text-gray-500">
        <span>
          {files.length} file{files.length === 1 ? '' : 's'} · {humanBytes(totalBytes)}
        </span>
        <button
          type="button"
          onClick={onClearAll}
          className="px-1.5 py-0.5 rounded hover:bg-white/[0.06] text-gray-500 hover:text-gray-300 transition-colors"
          title="Remove all attached files"
        >
          clear all
        </button>
      </div>

      {/* Hint for huge folder uploads — browser POST is the slow path */}
      {showHint && (
        <div className="mb-1.5 px-2 py-1 rounded text-[11px] text-amber-300/90 bg-amber-500/10 border border-amber-500/20">
          That&apos;s a lot of files. The browser upload streams each one serially through the
          backend — for &gt;100 files, you&apos;ll get dramatically faster results from{' '}
          <code className="bg-black/40 px-1 rounded">upload_to_s3.py</code> + the &quot;Browse
          S3&quot; attach option.
        </div>
      )}

      <div className={wrapCls}>
        {visible.map((f, i) => (
          <div key={i} className={pillCls}>
            <Paperclip className="w-3 h-3 text-gray-500" />
            <span className="truncate max-w-[160px]">{f.name}</span>
            <button
              onClick={() => onRemove(i)}
              title="Remove file"
              className="p-0.5 hover:bg-white/[0.1] rounded transition-colors"
            >
              <X className="w-3 h-3 text-gray-500" />
            </button>
          </div>
        ))}
        {overflow > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex items-center px-2.5 py-1 rounded-lg bg-white/[0.1] hover:bg-white/[0.15] border border-white/[0.08] text-xs text-gray-300 transition-colors"
          >
            {expanded ? 'show less' : `+${overflow} more`}
          </button>
        )}
      </div>
    </div>
  );
}
