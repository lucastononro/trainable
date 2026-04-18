'use client';

import { FileText, MessageSquare, X } from 'lucide-react';
import type { Mention } from '@/lib/types';

interface Props {
  mention: Mention;
  onRemove?: () => void;
  contentEditable?: boolean;
  title?: string;
}

export default function MentionPill({ mention, onRemove, contentEditable, title }: Props) {
  const isSession = mention.kind === 'session';
  const color = isSession
    ? 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
    : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';
  const Icon = isSession ? MessageSquare : FileText;
  const tooltip =
    title ??
    (isSession
      ? `session · ${mention.ref}`
      : mention.sandbox_path || mention.ref);

  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[12px] font-medium align-baseline select-none ${color}`}
      contentEditable={contentEditable ? false : undefined}
      data-mention="true"
      title={tooltip}
    >
      <Icon className="w-3 h-3 shrink-0" />
      <span className="truncate max-w-[180px]">{mention.label}</span>
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onRemove();
          }}
          className="p-0.5 -mr-0.5 rounded hover:bg-white/[0.12] transition-colors"
          aria-label="Remove mention"
        >
          <X className="w-2.5 h-2.5" />
        </button>
      )}
    </span>
  );
}
