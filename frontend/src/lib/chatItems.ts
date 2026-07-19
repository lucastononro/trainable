import type { Mention } from '@/lib/types';

// ---------------------------------------------------------------------------
// ChatItem interface
// ---------------------------------------------------------------------------

// Flat, all-optional meta shape covering every `ChatItem.type`'s fields.
// A precise per-type discriminated union would be more rigorous, but `meta`
// flows untyped through ~8 render components (CollapsibleToolCard,
// SubAgentCard, ClarificationCard, AgentToolCard, ToolGroupCard, …) that
// each only read their own subset without first narrowing on `item.type` —
// threading a strict per-variant type through all of them is a much larger,
// riskier change than this issue calls for. This still replaces `any` with
// real field names/types, which is what actually protects against a typo'd
// or missing field from the (unvalidated) backend payload.
export interface ChatItemMeta {
  // tool_start / tool_end / code_output
  code?: string;
  output?: string;
  outputs?: Array<{ text: string; stream?: string }>;
  // standalone code_output item (no matching tool_start found) — folded
  // into the tool card, never rendered on its own, but still carried
  stream?: string;
  // tool_end + subagent_end
  duration?: number | null;
  // subagent_start / subagent_end
  task?: string;
  model?: string;
  agent_id?: string;
  summary?: string;
  // clarification
  question_id?: string;
  asker_agent_id?: string;
  why_needed?: string;
  urgency?: string;
  status?: 'pending' | 'resolved';
  original_question?: string;
  answer?: string;
  answered_by?: string;
  // agent_tool (+ shared with clarification/subagent above: asker_agent_type,
  // answerer_agent_type, depth)
  call_id?: string;
  tool_name?: string;
  asker_agent_type?: string;
  target_agent_type?: string;
  answerer_agent_type?: string;
  answerer_agent_id?: string;
  depth?: number;
  duration_s?: number;
  is_error?: boolean;
  variant?: 'tool' | 'clarification_exchange';
  // assistant
  agent_type?: string;
  // user
  files?: string[];
  mentions?: Mention[];
  hidden?: boolean;
  /** File(s) attached via the "Browse S3" picker rather than local upload. */
  s3?: boolean;
}

export interface ChatItem {
  id: string;
  type:
    | 'user'
    | 'assistant'
    | 'tool_start'
    | 'tool_end'
    | 'code_output'
    | 'error'
    | 'status'
    | 'stage_complete'
    | 'subagent_start'
    | 'subagent_end'
    | 'clarification'
    | 'agent_tool';
  content: string;
  meta?: ChatItemMeta;
  timestamp: number;
}

// Small runtime-checked readers for `Message.metadata` (Record<string,
// unknown> — persisted session history, not schema-validated on the way
// back out of Postgres). Used when reconstructing `ChatItem.meta` on
// session reload, so a malformed/missing field degrades to `undefined`
// instead of a blind `as` cast lying about the shape.
export function metaStr(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}
export function metaNum(v: unknown): number | undefined {
  return typeof v === 'number' ? v : undefined;
}
