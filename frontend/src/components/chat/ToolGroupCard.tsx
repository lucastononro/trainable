'use client';

import { useState } from 'react';
import { CheckCircle2, ChevronRight, Code2, Loader2 } from 'lucide-react';
import type { ChatItem } from '@/lib/chatItems';
import CollapsibleToolCard from '@/components/chat/CollapsibleToolCard';

// ---------------------------------------------------------------------------
// ToolGroupCard -- groups consecutive tool executions into a single card
// ---------------------------------------------------------------------------

export default function ToolGroupCard({ items }: { items: ChatItem[] }) {
  const [expanded, setExpanded] = useState(false);

  // Check if any tool in the group is still running
  const hasRunning = items.some((i) => i.type === 'tool_start');
  const toolItems = items.filter((i) => i.type === 'tool_start' || i.type === 'tool_end');
  const count = toolItems.length;
  const totalDuration = toolItems.reduce(
    (sum, i) => sum + (i.type === 'tool_end' ? i.meta?.duration || 0 : 0),
    0,
  );

  // If only 1 tool, render it directly without group wrapper
  if (count === 1) {
    return <CollapsibleToolCard item={toolItems[0]} />;
  }

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-amber-500/20">
        <Code2 className="w-3.5 h-3.5 text-amber-400" />
      </div>
      <div className="flex-1 min-w-0 rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border overflow-hidden">
        <button
          type="button"
          aria-expanded={expanded}
          className="w-full flex items-center gap-2 px-4 py-2.5 cursor-pointer select-none text-left"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {hasRunning ? (
            <Loader2 className="w-3.5 h-3.5 text-amber-400 animate-spin" />
          ) : (
            <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
          )}
          <span className="text-sm text-gray-300 flex-1">
            {hasRunning
              ? `Running ${count} steps...`
              : `Ran ${count} steps${totalDuration > 0 ? ` in ${totalDuration}s` : ''}`}
          </span>
          <ChevronRight
            className={`w-3.5 h-3.5 text-gray-500 transition-transform duration-150 ${
              expanded ? 'rotate-90' : ''
            }`}
          />
        </button>
        {expanded && (
          <div className="border-t border-surface-border px-2 py-2 space-y-1">
            {toolItems.map((item) => (
              <CollapsibleToolCard key={item.id} item={item} inline />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
