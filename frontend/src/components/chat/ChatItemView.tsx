'use client';

import { memo, type ReactNode } from 'react';
import { AlertCircle, Bot, CheckCircle2, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ErrorBoundary from '@/components/ErrorBoundary';
import MentionPill from '@/components/MentionPill';
import { wireToDraft } from '@/lib/mentions';
import type { Mention } from '@/lib/types';
import type { ChatItem } from '@/lib/chatItems';
import { AGENT_COLORS, AGENT_META } from '@/components/chat/agentMeta';
import ToolGroupCard from '@/components/chat/ToolGroupCard';
import CollapsibleToolCard from '@/components/chat/CollapsibleToolCard';
import SubAgentCard from '@/components/chat/SubAgentCard';
import ClarificationCard from '@/components/chat/ClarificationCard';
import AgentToolCard from '@/components/chat/AgentToolCard';
import UserMessageFilePills from '@/components/chat/UserMessageFilePills';

// ---------------------------------------------------------------------------
// ChatItemView — memoized per-item renderer (formerly the plain
// `renderChatItem` function). renderGroupedChatItems re-runs on every
// `agent_token`/`agent_message` SSE event (each one calls `setChatItems`),
// which previously re-invoked this as a plain function for every prior
// message and re-parsed markdown for all of them — O(messages) ReactMarkdown
// parses per streamed chunk. Wrapping it in `memo`, keyed by `item.id` at
// the call site, means React bails out and skips re-render (and re-parse)
// for every bubble except the one whose `item` object reference actually
// changed (the currently-streaming assistant bubble).
// ---------------------------------------------------------------------------

// Stable remark-plugins array for chat bubbles — a fresh `[remarkGfm]`
// literal on every render would give ReactMarkdown a "new" plugin list each
// time, undermining the memoization above even when `item` didn't change.
const CHAT_MARKDOWN_PLUGINS = [remarkGfm];

const ChatItemView = memo(function ChatItemView({
  item,
  streamingItemId,
  sessionId,
}: {
  item: ChatItem;
  streamingItemId?: string | null;
  sessionId?: string | null;
}) {
  switch (item.type) {
    case 'user': {
      const files: string[] = item.meta?.files || [];
      const mentions: Mention[] | undefined = item.meta?.mentions;
      const hasText = item.content && item.content.trim().length > 0;
      const hasFiles = files.length > 0;
      const tokens = mentions && mentions.length > 0 ? wireToDraft(item.content, mentions) : null;
      return (
        <div className="flex justify-end animate-fade-in">
          <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary-600 text-white text-sm overflow-hidden">
            {hasFiles && <UserMessageFilePills files={files} hasText={Boolean(hasText)} />}
            {hasText && (
              <div className="px-4 py-2.5 whitespace-pre-wrap break-words">
                {tokens
                  ? tokens.map((t, i) =>
                      t.kind === 'text' ? (
                        <span key={i}>{t.value}</span>
                      ) : (
                        <MentionPill key={i} mention={t.mention} />
                      ),
                    )
                  : item.content}
              </div>
            )}
            {!hasText && !hasFiles && <div className="px-4 py-2.5">{item.content}</div>}
          </div>
        </div>
      );
    }
    case 'assistant': {
      // Color the avatar based on which agent produced this message
      const agentType = item.meta?.agent_type;
      const agentMeta = agentType ? AGENT_META[agentType] : null;
      const agentColor = agentMeta ? AGENT_COLORS[agentMeta.color] : null;
      const avatarBg = agentColor ? agentColor.bg : 'bg-emerald-500/20';
      const avatarText = agentColor ? agentColor.text : 'text-emerald-400';
      const isStreaming = item.id === streamingItemId;

      return (
        <div className="flex gap-3 animate-fade-in">
          <div
            className={`w-7 h-7 rounded-full ${avatarBg} flex items-center justify-center shrink-0 mt-1`}
          >
            <Bot className={`w-3.5 h-3.5 ${avatarText}`} />
          </div>
          <div className="flex-1 min-w-0 text-sm text-gray-200 markdown-content">
            {agentMeta && (
              <div className={`text-[10px] ${avatarText} font-medium mb-1`}>{agentMeta.label}</div>
            )}
            <ErrorBoundary
              fallback={() => <div className="whitespace-pre-wrap break-words">{item.content}</div>}
            >
              <ReactMarkdown remarkPlugins={CHAT_MARKDOWN_PLUGINS}>{item.content}</ReactMarkdown>
            </ErrorBoundary>
            {isStreaming && (
              <span className="inline-block w-2 h-5 bg-primary-400 rounded-sm ml-0.5 animate-blink align-text-bottom" />
            )}
          </div>
        </div>
      );
    }
    case 'tool_start':
    case 'tool_end':
      return <CollapsibleToolCard item={item} />;
    case 'code_output':
      return null; // folded into the tool card above
    case 'subagent_start':
    case 'subagent_end':
      return <SubAgentCard item={item} />;
    case 'clarification':
      return <ClarificationCard item={item} sessionId={sessionId ?? null} />;
    case 'agent_tool':
      return <AgentToolCard item={item} />;
    case 'error':
      return (
        <div className="animate-fade-in flex items-center gap-2 px-3 py-2 bg-red-900/30 border border-red-800/50 rounded-lg text-sm text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {item.content}
        </div>
      );
    case 'status':
      return (
        <div className="text-center">
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
              item.content.includes('running')
                ? 'bg-amber-500/20 text-amber-400'
                : item.content.includes('done')
                  ? 'bg-green-500/20 text-green-400'
                  : item.content === 'failed'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-neutral-800 text-gray-400'
            }`}
          >
            {item.content.includes('running') && <Loader2 className="w-3 h-3 animate-spin" />}
            {item.content.includes('done') && <CheckCircle2 className="w-3 h-3" />}
            {item.content.replace(/_/g, ' ')}
          </span>
        </div>
      );
    case 'stage_complete':
      return (
        <div className="flex items-center justify-center py-2 animate-fade-in">
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-green-500/10 border border-green-500/20">
            <CheckCircle2 className="w-4 h-4 text-green-400" />
            <span className="text-sm font-medium text-green-300">{item.content} complete</span>
          </div>
        </div>
      );
    default:
      return null;
  }
});

export default ChatItemView;

// ---------------------------------------------------------------------------
// renderGroupedChatItems — groups consecutive tool items together
// ---------------------------------------------------------------------------

function isToolItem(item: ChatItem) {
  return item.type === 'tool_start' || item.type === 'tool_end' || item.type === 'code_output';
}

export function renderGroupedChatItems(
  items: ChatItem[],
  streamingItemId?: string | null,
  sessionId?: string | null,
) {
  const result: ReactNode[] = [];
  let i = 0;

  while (i < items.length) {
    const cur = items[i];

    if (isToolItem(cur)) {
      const group: ChatItem[] = [];
      while (i < items.length && isToolItem(items[i])) {
        group.push(items[i]);
        i++;
      }
      result.push(<ToolGroupCard key={`tg-${group[0].id}`} items={group} />);
    } else {
      result.push(
        <ChatItemView
          key={cur.id}
          item={cur}
          streamingItemId={streamingItemId}
          sessionId={sessionId}
        />,
      );
      i++;
    }
  }

  return result;
}
