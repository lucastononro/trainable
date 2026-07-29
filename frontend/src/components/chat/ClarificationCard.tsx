'use client';

import { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, Send } from 'lucide-react';
import { api } from '@/lib/api';
import type { ChatItem } from '@/lib/chatItems';
import { AGENT_COLORS, AGENT_META } from '@/components/chat/agentMeta';

// ---------------------------------------------------------------------------
// ClarificationCard -- inline reply for sub-agent clarification questions
// ---------------------------------------------------------------------------

export default function ClarificationCard({
  item,
  sessionId,
}: {
  item: ChatItem;
  sessionId: string | null;
}) {
  const [reply, setReply] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [localStatus, setLocalStatus] = useState<'pending' | 'sent' | 'resolved'>(
    item.meta?.status === 'resolved' ? 'resolved' : 'pending',
  );

  useEffect(() => {
    if (item.meta?.status === 'resolved') setLocalStatus('resolved');
  }, [item.meta?.status]);

  const askerType = item.meta?.asker_agent_type || 'sub-agent';
  const askerId = item.meta?.asker_agent_id;
  const depth = item.meta?.depth ?? 1;
  const askerLabel = AGENT_META[askerType]?.label || askerType;
  const askerColor = AGENT_COLORS[AGENT_META[askerType]?.color || 'teal'];

  async function send() {
    if (!reply.trim() || !sessionId || !item.meta?.question_id) return;
    setSubmitting(true);
    try {
      await api.replyClarification(sessionId, item.meta.question_id, reply.trim());
      setLocalStatus('sent');
    } catch (e) {
      console.error('Failed to send clarification reply', e);
    } finally {
      setSubmitting(false);
    }
  }

  const isResolved = localStatus === 'resolved';
  const isSent = localStatus === 'sent';

  return (
    <div className="animate-fade-in my-1.5" style={{ marginLeft: `${Math.min(depth, 3) * 12}px` }}>
      <div className={`rounded-xl border ${askerColor.border} ${askerColor.bg} p-3.5`}>
        <div className="flex items-center gap-2 mb-2">
          <div
            className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${askerColor.bg}`}
          >
            <AlertCircle className={`w-3.5 h-3.5 ${askerColor.text}`} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className={`text-sm font-medium ${askerColor.text}`}>{askerLabel}</span>
              {askerId && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.06] text-gray-500 font-mono">
                  #{askerId}
                </span>
              )}
              <span className="text-[10px] uppercase tracking-wider text-gray-500">
                needs clarification
              </span>
            </div>
          </div>
          {isResolved && <CheckCircle2 className="w-4 h-4 text-green-400" />}
        </div>

        <div className="text-sm text-gray-200 mb-1.5 whitespace-pre-wrap">{item.content}</div>
        {item.meta?.why_needed && (
          <div className="text-xs text-gray-500 mb-2 italic">Why: {item.meta.why_needed}</div>
        )}

        {isResolved ? (
          <div className="mt-2 pt-2 border-t border-white/[0.05] text-xs text-gray-400">
            <span className="text-green-400 font-medium">Answered: </span>
            {item.meta?.answer || '(no answer)'}
          </div>
        ) : isSent ? (
          <div className="text-xs text-gray-500 italic flex items-center gap-1.5">
            <Loader2 className="w-3 h-3 animate-spin" /> Sent — waiting for sub-agent to resume…
          </div>
        ) : (
          <div className="flex gap-2 items-end mt-1">
            <textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              placeholder="Type your reply…"
              rows={2}
              className="flex-1 bg-neutral-900/60 border border-surface-border rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-none focus:outline-none focus:border-primary-500/60"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            <button
              onClick={send}
              disabled={submitting || !reply.trim()}
              className="px-3 py-2 rounded-lg bg-primary-600 hover:bg-primary-500 disabled:bg-neutral-800 disabled:text-gray-600 text-white text-sm flex items-center gap-1.5 transition-colors"
            >
              {submitting ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Send className="w-3.5 h-3.5" />
              )}
              Reply
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
