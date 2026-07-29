// Shared agent display metadata for the chat cards (SubAgentCard,
// ClarificationCard, AgentToolCard, ChatItemView avatars).

export const AGENT_META: Record<string, { label: string; color: string }> = {
  orchestrator: { label: 'Orchestrator', color: 'violet' },
  eda: { label: 'EDA Agent', color: 'blue' },
  data_prep: { label: 'Data Prep Agent', color: 'amber' },
  feature_eng: { label: 'Feature Eng. Agent', color: 'orange' },
  trainer: { label: 'Training Agent', color: 'green' },
  reviewer: { label: 'Review Agent', color: 'rose' },
  chat: { label: 'Chat Agent', color: 'gray' },
};

export const AGENT_COLORS: Record<
  string,
  { bg: string; text: string; border: string; dot: string }
> = {
  blue: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    border: 'border-blue-500/20',
    dot: 'bg-blue-400',
  },
  amber: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    border: 'border-amber-500/20',
    dot: 'bg-amber-400',
  },
  green: {
    bg: 'bg-green-500/15',
    text: 'text-green-400',
    border: 'border-green-500/20',
    dot: 'bg-green-400',
  },
  orange: {
    bg: 'bg-orange-500/15',
    text: 'text-orange-400',
    border: 'border-orange-500/20',
    dot: 'bg-orange-400',
  },
  rose: {
    bg: 'bg-rose-500/15',
    text: 'text-rose-400',
    border: 'border-rose-500/20',
    dot: 'bg-rose-400',
  },
  violet: {
    bg: 'bg-violet-500/15',
    text: 'text-violet-400',
    border: 'border-violet-500/20',
    dot: 'bg-violet-400',
  },
  gray: {
    bg: 'bg-gray-500/15',
    text: 'text-gray-400',
    border: 'border-gray-500/20',
    dot: 'bg-gray-400',
  },
  teal: {
    bg: 'bg-teal-500/15',
    text: 'text-teal-400',
    border: 'border-teal-500/20',
    dot: 'bg-teal-400',
  },
};
