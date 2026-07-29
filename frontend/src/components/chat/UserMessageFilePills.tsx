'use client';

import { useState } from 'react';
import { Paperclip } from 'lucide-react';

// ---------------------------------------------------------------------------
// UserMessageFilePills — collapsed list of file pills inside a user bubble.
//
// Bulk uploads (e.g. an image dataset of thousands of files) used to render
// every filename as a flex-wrapped pill, growing the bubble vertically until
// it pushed the chat input bar off-screen and the user couldn't type. Cap
// the visible count, and stash the rest behind a "+N more" toggle so the
// bubble stays a sensible size.
// ---------------------------------------------------------------------------

const FILE_PILL_PREVIEW = 6;

export default function UserMessageFilePills({
  files,
  hasText,
}: {
  files: string[];
  hasText: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const overflow = files.length - FILE_PILL_PREVIEW;
  const visible = expanded || overflow <= 0 ? files : files.slice(0, FILE_PILL_PREVIEW);
  return (
    <div className={`flex flex-wrap gap-1.5 px-4 ${hasText ? 'pt-3 pb-1' : 'py-3'}`}>
      {visible.map((f, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/15 text-xs"
        >
          <Paperclip className="w-3 h-3 opacity-70" />
          <span className="truncate max-w-[150px]">{f}</span>
        </span>
      ))}
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center px-2 py-0.5 rounded-md bg-white/20 hover:bg-white/30 text-xs transition-colors"
        >
          {expanded ? 'show less' : `+${overflow} more`}
        </button>
      )}
    </div>
  );
}
