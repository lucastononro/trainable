'use client';

import { AlertCircle, FileSearch, ListChecks, Search, Wrench } from 'lucide-react';
import type { ChatItem } from '@/lib/chatItems';
import { AGENT_COLORS, AGENT_META } from '@/components/chat/agentMeta';

// ---------------------------------------------------------------------------
// AgentToolCard -- one-line surface for auxiliary tools (inspect, list, read,
// inter-agent clarification exchanges). Strict UX rules:
//   - ALWAYS use friendly agent names (never agent_id strings) in the headline.
//   - NEVER show tool input arguments or tool result content. The user only
//     needs to know that the agent ran the tool. Content is queryable via
//     inspect_agent_context for agents themselves.
// ---------------------------------------------------------------------------

const AGENT_TOOL_META: Record<
  string,
  { icon: typeof Search; verb: string; targetVerb?: string; color: keyof typeof AGENT_COLORS }
> = {
  inspect_agent_context: {
    icon: Search,
    verb: 'inspected',
    targetVerb: "'s context",
    color: 'teal',
  },
  list_session_agents: { icon: ListChecks, verb: 'listed agents in this session', color: 'teal' },
  read_project_session: {
    icon: FileSearch,
    verb: 'read another session in this project',
    color: 'teal',
  },
  request_clarification: {
    icon: AlertCircle,
    verb: 'asked',
    targetVerb: 'for clarification (answered internally)',
    color: 'violet',
  },
};

function _agentLabel(agentType: string | undefined | null): string {
  if (!agentType) return 'an agent';
  return AGENT_META[agentType]?.label || agentType;
}

export default function AgentToolCard({ item }: { item: ChatItem }) {
  const meta = item.meta || {};
  const toolName: string = meta.tool_name || item.content || 'tool';
  const config = AGENT_TOOL_META[toolName] || {
    icon: Wrench as typeof Search,
    verb: 'used a tool',
    color: 'gray' as const,
  };
  const Icon = config.icon;
  const colors = AGENT_COLORS[config.color] || AGENT_COLORS.gray;
  const isError = !!meta.is_error;

  const askerLabel = _agentLabel(meta.asker_agent_type);
  const targetType =
    meta.target_agent_type ||
    (meta.variant === 'clarification_exchange' ? meta.answerer_agent_type : null);
  const targetLabel = targetType ? _agentLabel(targetType) : null;

  // Build the headline. NO ids, NO input args, NO result content.
  const headline = targetLabel
    ? `${askerLabel} ${config.verb} ${targetLabel}${config.targetVerb || ''}`
    : `${askerLabel} ${config.verb}`;

  const depth = Math.min(meta.depth || 0, 3);
  const duration = meta.duration_s;

  return (
    <div className="animate-fade-in my-1" style={{ marginLeft: `${depth * 12}px` }}>
      <div
        className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border ${
          isError ? 'bg-red-500/10 border-red-500/20' : `${colors.bg} ${colors.border}`
        }`}
      >
        <Icon className={`w-3.5 h-3.5 shrink-0 ${isError ? 'text-red-400' : colors.text}`} />
        <span className={`text-xs ${isError ? 'text-red-300' : 'text-gray-300'}`}>
          {headline}
          {isError && ' — failed'}
        </span>
        {typeof duration === 'number' && duration > 0 && (
          <span className="text-[10px] text-gray-600 font-mono">{duration.toFixed(1)}s</span>
        )}
      </div>
    </div>
  );
}
