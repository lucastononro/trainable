'use client';

import { useEffect, useState } from 'react';
import { Check, CheckCircle2, Clock, Loader2, Pencil, ShieldCheck, XCircle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { api } from '@/lib/api';
import type { ChatItem } from '@/lib/chatItems';
import { AGENT_COLORS, AGENT_META } from '@/components/chat/agentMeta';

// ---------------------------------------------------------------------------
// ApprovalCard — HITL approval gate (issue #108). The agent proposed a
// consequential decision (target column, prep plan, model shortlist, …) and
// is BLOCKED until the user approves it as-is or submits a revision.
// ---------------------------------------------------------------------------

const MARKDOWN_PLUGINS = [remarkGfm];

const KIND_LABEL: Record<string, string> = {
  target_column: 'Target column',
  prep_plan: 'Prep plan',
  model_shortlist: 'Model shortlist',
  other: 'Decision',
};

export default function ApprovalCard({
  item,
  sessionId,
}: {
  item: ChatItem;
  sessionId: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [localStatus, setLocalStatus] = useState<'pending' | 'sent' | 'resolved'>(
    item.meta?.status === 'resolved' ? 'resolved' : 'pending',
  );

  useEffect(() => {
    if (item.meta?.status === 'resolved') setLocalStatus('resolved');
  }, [item.meta?.status]);

  const askerType = item.meta?.asker_agent_type || 'agent';
  const askerLabel = AGENT_META[askerType]?.label || askerType;
  const askerColor = AGENT_COLORS[AGENT_META[askerType]?.color || 'teal'];
  const kind = item.meta?.kind || 'other';
  const decision = item.meta?.decision;

  async function send(verdict: 'approve' | 'edit') {
    if (!sessionId || !item.meta?.approval_id) return;
    if (verdict === 'edit' && !edits.trim()) return;
    setSubmitting(true);
    setSendError(null);
    try {
      await api.replyApproval(
        sessionId,
        item.meta.approval_id,
        verdict,
        verdict === 'edit' ? edits.trim() : undefined,
      );
      setLocalStatus('sent');
    } catch (e) {
      // The buttons re-enable via `finally`, but without a visible message
      // the user can't tell their verdict never reached the agent (e.g. the
      // approval expired or the backend restarted) — surface it inline.
      console.error('Failed to send approval verdict', e);
      setSendError(
        `Couldn't send your ${verdict === 'edit' ? 'revision' : 'approval'} — ` +
          `${e instanceof Error ? e.message : 'request failed'}. Try again.`,
      );
    } finally {
      setSubmitting(false);
    }
  }

  const isResolved = localStatus === 'resolved';
  const isSent = localStatus === 'sent';

  return (
    <div className="animate-fade-in my-1.5">
      <div className={`rounded-xl border ${askerColor.border} ${askerColor.bg} p-3.5`}>
        {/* Header */}
        <div className="flex items-center gap-2 mb-2">
          <div
            className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${askerColor.bg}`}
          >
            <ShieldCheck className={`w-3.5 h-3.5 ${askerColor.text}`} />
          </div>
          <div className="flex-1 min-w-0 flex items-center gap-2">
            <span className={`text-sm font-medium ${askerColor.text}`}>{askerLabel}</span>
            <span className="text-[10px] uppercase tracking-wider text-gray-500">
              needs your approval
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.06] text-gray-400">
              {KIND_LABEL[kind] || KIND_LABEL.other}
            </span>
          </div>
          {isResolved &&
            (decision === 'approve' ? (
              <CheckCircle2 className="w-4 h-4 text-green-400" />
            ) : decision === 'edit' ? (
              <Pencil className="w-4 h-4 text-sky-400" />
            ) : decision === 'timeout' ? (
              <Clock className="w-4 h-4 text-amber-400" />
            ) : (
              <XCircle className="w-4 h-4 text-gray-500" />
            ))}
        </div>

        {/* Proposed decision */}
        {item.meta?.title && (
          <div className="text-sm font-medium text-gray-100 mb-1">{item.meta.title}</div>
        )}
        <div className="text-sm text-gray-200 markdown-content mb-1.5">
          <ErrorBoundary
            fallback={() => <div className="whitespace-pre-wrap break-words">{item.content}</div>}
          >
            <ReactMarkdown remarkPlugins={MARKDOWN_PLUGINS}>{item.content}</ReactMarkdown>
          </ErrorBoundary>
        </div>
        {item.meta?.context && (
          <div className="text-xs text-gray-500 mb-2 italic">Why: {item.meta.context}</div>
        )}

        {/* Verdict / actions */}
        {isResolved ? (
          <div className="mt-2 pt-2 border-t border-white/[0.05] text-xs text-gray-400">
            {decision === 'approve' ? (
              <span className="text-green-400 font-medium">Approved — proceeding as proposed.</span>
            ) : decision === 'edit' ? (
              <>
                <span className="text-sky-400 font-medium">Edited: </span>
                {item.meta?.answer || '(no revision text)'}
              </>
            ) : decision === 'timeout' ? (
              <span className="text-amber-400 font-medium">
                No response — the agent proceeded with its proposal (unapproved).
              </span>
            ) : (
              <span className="text-gray-500 font-medium">Cancelled with the session.</span>
            )}
          </div>
        ) : isSent ? (
          <div className="text-xs text-gray-500 italic flex items-center gap-1.5">
            <Loader2 className="w-3 h-3 animate-spin" /> Sent — the agent is resuming…
          </div>
        ) : (
          <div className="mt-1 space-y-2">
            {sendError && (
              <div className="text-xs text-red-400 flex items-center gap-1.5">
                <XCircle className="w-3.5 h-3.5 shrink-0" /> {sendError}
              </div>
            )}
            {editing && (
              <textarea
                value={edits}
                onChange={(e) => setEdits(e.target.value)}
                placeholder="Revise the decision — your text replaces the agent's proposal…"
                rows={3}
                autoFocus
                className="w-full bg-neutral-900/60 border border-surface-border rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-none focus:outline-none focus:border-primary-500/60"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    send('edit');
                  }
                }}
              />
            )}
            <div className="flex gap-2">
              {editing ? (
                <>
                  <button
                    onClick={() => send('edit')}
                    disabled={submitting || !edits.trim()}
                    className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 disabled:bg-neutral-800 disabled:text-gray-600 text-white text-sm flex items-center gap-1.5 transition-colors"
                  >
                    {submitting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Pencil className="w-3.5 h-3.5" />
                    )}
                    Send revision
                  </button>
                  <button
                    onClick={() => setEditing(false)}
                    disabled={submitting}
                    className="px-3 py-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] text-gray-300 text-sm transition-colors"
                  >
                    Back
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={() => send('approve')}
                    disabled={submitting}
                    className="px-3 py-1.5 rounded-lg bg-green-600 hover:bg-green-500 disabled:bg-neutral-800 disabled:text-gray-600 text-white text-sm flex items-center gap-1.5 transition-colors"
                  >
                    {submitting ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Check className="w-3.5 h-3.5" />
                    )}
                    Approve
                  </button>
                  <button
                    onClick={() => setEditing(true)}
                    disabled={submitting}
                    className="px-3 py-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] border border-surface-border text-gray-300 text-sm flex items-center gap-1.5 transition-colors"
                  >
                    <Pencil className="w-3.5 h-3.5" />
                    Edit
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
