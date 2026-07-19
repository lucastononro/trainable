'use client';

import { useEffect, useState } from 'react';
import { CheckCircle2, ChevronRight, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ErrorBoundary from '@/components/ErrorBoundary';
import type { ChatItem } from '@/lib/chatItems';
import { AGENT_COLORS, AGENT_META } from '@/components/chat/agentMeta';

// ---------------------------------------------------------------------------
// SubAgentCard -- teal/cyan themed card for multi-agent sub-agents
// ---------------------------------------------------------------------------

export default function SubAgentCard({ item }: { item: ChatItem }) {
  const isStart = item.type === 'subagent_start';
  const [collapsed, setCollapsed] = useState(true);
  const [elapsed, setElapsed] = useState(0);

  const agentType = item.content || 'sub-agent';
  const meta = AGENT_META[agentType] || {
    label: agentType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
    color: 'teal',
  };
  const colors = AGENT_COLORS[meta.color] || AGENT_COLORS.teal;
  const modelName = item.meta?.model
    ? item.meta.model.replace('claude-', '').replace(/-/g, ' ')
    : '';

  useEffect(() => {
    if (!isStart) return;
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - item.timestamp) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [isStart, item.timestamp]);

  return (
    <div className="animate-fade-in my-1">
      <button
        className={`flex items-center gap-2.5 w-full px-3.5 py-2 rounded-xl ${colors.bg} border ${colors.border} hover:brightness-110 transition-all text-left group`}
        onClick={() => setCollapsed((prev) => !prev)}
      >
        {/* Agent icon */}
        <div
          className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${colors.bg}`}
        >
          {isStart ? (
            <Loader2 className={`w-3.5 h-3.5 ${colors.text} animate-spin`} />
          ) : (
            <CheckCircle2 className={`w-3.5 h-3.5 ${colors.text}`} />
          )}
        </div>

        {/* Label + model */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-sm font-medium ${colors.text}`}>{meta.label}</span>
            {modelName && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.06] text-gray-500 font-mono">
                {modelName}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 truncate mt-0.5">
            {isStart
              ? `Running...${elapsed > 0 ? ` ${elapsed}s` : ''}`
              : `Completed${item.meta?.duration ? ` in ${item.meta.duration}s` : ''}`}
          </p>
        </div>

        <ChevronRight
          className={`w-3.5 h-3.5 text-gray-600 transition-transform duration-150 shrink-0 ${
            !collapsed ? 'rotate-90' : ''
          }`}
        />
      </button>

      {!collapsed && (
        <div className={`mt-1 ml-4 border-l-2 ${colors.border} pl-3 space-y-1.5`}>
          {item.meta?.task && (
            <div className="text-xs text-gray-400">
              <span className={`${colors.text} font-medium`}>Task: </span>
              {item.meta.task}
            </div>
          )}
          {item.meta?.summary && (
            <div className="text-xs text-gray-400 max-h-48 overflow-y-auto">
              <span className={`${colors.text} font-medium`}>Result: </span>
              <div className="mt-1 markdown-chat">
                <ErrorBoundary
                  fallback={() => (
                    <div className="whitespace-pre-wrap break-words">{item.meta?.summary}</div>
                  )}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {item.meta.summary.length > 800
                      ? item.meta.summary.slice(0, 800) + '\n\n...'
                      : item.meta.summary}
                  </ReactMarkdown>
                </ErrorBoundary>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
