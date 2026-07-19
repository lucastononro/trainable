'use client';

import {
  useCallback,
  useEffect,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from 'react';
import {
  Bot,
  FolderUp,
  HardDrive,
  Loader2,
  Plus,
  RotateCcw,
  Send,
  ShieldCheck,
  Square,
  Upload,
} from 'lucide-react';
import { useApp } from '@/lib/AppContext';
import { isDraftEmpty } from '@/lib/mentions';
import type { Draft, Task, TaskCreatePayload, TaskUpdatePayload } from '@/lib/types';
import type { ChatItem } from '@/lib/chatItems';
import { renderGroupedChatItems } from '@/components/chat/ChatItemView';
import AttachedFilesPreview from '@/components/chat/AttachedFilesPreview';
import InlineTasks from '@/components/InlineTasks';
import MentionInput from '@/components/MentionInput';

// ---------------------------------------------------------------------------
// ChatPane — the left panel of the studio view: scrollable chat stream
// (grouped chat items + inline tasks card + typing indicator) and the
// in-session input bar (attach menu + mention input + stop/send).
// ---------------------------------------------------------------------------

export default function ChatPane({
  canvasOpen,
  chatItems,
  streamingItemIdRef,
  pinnedToBottomRef,
  tasks,
  onTaskCreate,
  onTaskUpdate,
  onTaskDelete,
  draft,
  onDraftChange,
  onSend,
  onStop,
  sessionState,
  onResume,
  approvalsEnabled,
  onToggleApprovals,
  attachedFiles,
  onRemoveAttachedFile,
  onClearAttachedFiles,
  attachingFiles,
  showAttachMenu,
  setShowAttachMenu,
  attachMenuRef,
  fileInputRef,
  folderInputRef,
  onFilesSelected,
  onOpenS3Browser,
  sessionAttachedFiles,
}: {
  canvasOpen: boolean;
  chatItems: ChatItem[];
  streamingItemIdRef: MutableRefObject<string | null>;
  pinnedToBottomRef: MutableRefObject<boolean>;
  tasks: Task[];
  onTaskCreate: (body: TaskCreatePayload) => Promise<void>;
  onTaskUpdate: (id: number, body: TaskUpdatePayload) => Promise<void>;
  onTaskDelete: (id: number) => Promise<void>;
  draft: Draft;
  onDraftChange: (draft: Draft) => void;
  onSend: () => void;
  onStop: () => void;
  /** Session lifecycle state from the stream (e.g. "failed", "cancelled"). */
  sessionState: string;
  /** Relaunch an interrupted session with recovered progress (issue #106). */
  onResume: (mode: 'resume' | 'retry') => void;
  /** HITL approval gates (issue #108) — opt-in toggle state + flipper. */
  approvalsEnabled: boolean;
  onToggleApprovals: () => void;
  attachedFiles: File[];
  onRemoveAttachedFile: (index: number) => void;
  onClearAttachedFiles: () => void;
  attachingFiles: boolean;
  showAttachMenu: boolean;
  setShowAttachMenu: Dispatch<SetStateAction<boolean>>;
  /** Shared with WelcomeScreen — only one of the two inputs is mounted at a
   *  time, and the outside-click close handler lives in the page. */
  attachMenuRef: RefObject<HTMLDivElement>;
  fileInputRef: RefObject<HTMLInputElement>;
  folderInputRef: RefObject<HTMLInputElement>;
  onFilesSelected: (files: FileList | File[]) => void;
  onOpenS3Browser: () => void;
  sessionAttachedFiles: { name: string; sandboxPath: string }[];
}) {
  const { activeSessionId, activeProjectId, experiments, isRunning } = useApp();

  const bottomRef = useRef<HTMLDivElement>(null);
  // The actual scrollable chat pane (the `overflow-y-auto` div `bottomRef`
  // sits at the bottom of). Used to measure scroll position for the
  // pinned-to-bottom tracking below.
  const chatScrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new chat items — but only while the user is pinned near
  // the bottom of the pane. `chatItems` changes many times per second while
  // an assistant reply streams token-by-token; without the pin gate,
  // `scrollIntoView` fired on every single one of those changes and
  // hijacked the scroll position, making it impossible to scroll up and
  // read earlier output. `behavior: 'auto'` (no animation) while a bubble is
  // actively streaming avoids stacking up smooth-scroll animations that
  // fight each other; once streaming settles we go back to a smooth nudge.
  useEffect(() => {
    if (!pinnedToBottomRef.current) return;
    bottomRef.current?.scrollIntoView({
      behavior: streamingItemIdRef.current ? 'auto' : 'smooth',
    });
  }, [chatItems, pinnedToBottomRef, streamingItemIdRef]);

  // Track whether the user is pinned near the bottom of the chat pane via a
  // scroll listener + threshold, rather than assuming every render should
  // snap back down.
  const AUTO_SCROLL_PIN_THRESHOLD_PX = 96;
  const handleChatScroll = useCallback(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedToBottomRef.current = distanceFromBottom <= AUTO_SCROLL_PIN_THRESHOLD_PX;
  }, [pinnedToBottomRef]);

  return (
    <div className="h-full flex flex-col min-w-0">
      <div
        ref={chatScrollRef}
        onScroll={handleChatScroll}
        className="flex-1 overflow-y-auto px-4 py-4"
      >
        <div className={`mx-auto w-full space-y-4 ${canvasOpen ? 'max-w-3xl' : 'max-w-5xl'}`}>
          {renderGroupedChatItems(chatItems, streamingItemIdRef.current, activeSessionId)}

          {tasks.length > 0 && (
            <InlineTasks
              tasks={tasks}
              onCreate={onTaskCreate}
              onUpdate={onTaskUpdate}
              onDelete={onTaskDelete}
            />
          )}

          {isRunning &&
            !streamingItemIdRef.current &&
            (() => {
              const last = chatItems[chatItems.length - 1];
              return !last || last.type !== 'tool_start';
            })() && (
              <div className="flex gap-3 animate-fade-in">
                <div className="w-7 h-7 rounded-full bg-emerald-500/20 flex items-center justify-center shrink-0">
                  <Bot className="w-3.5 h-3.5 text-emerald-400" />
                </div>
                <div className="flex items-center gap-1.5 px-4 py-2.5 rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border">
                  <span
                    className="w-2 h-2 rounded-full bg-gray-400 animate-typing"
                    style={{ animationDelay: '0ms' }}
                  />
                  <span
                    className="w-2 h-2 rounded-full bg-gray-400 animate-typing"
                    style={{ animationDelay: '150ms' }}
                  />
                  <span
                    className="w-2 h-2 rounded-full bg-gray-400 animate-typing"
                    style={{ animationDelay: '300ms' }}
                  />
                </div>
              </div>
            )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input bar */}
      <div className="bg-black px-4 py-3">
        <div className={`mx-auto ${canvasOpen ? 'max-w-3xl' : 'max-w-5xl'}`}>
          {/* Resume / retry banner — only for interrupted sessions. The
              backend relaunches the agent with prior tool history, task
              state, and the workspace file listing so completed steps are
              skipped instead of redone. */}
          {!isRunning && ['failed', 'cancelled', 'timed_out'].includes(sessionState) && (
            <div className="mb-2 flex items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 animate-fade-in">
              <span className="flex-1 text-xs text-amber-200">
                {sessionState === 'failed'
                  ? 'The last run failed before finishing.'
                  : sessionState === 'timed_out'
                    ? 'The last run timed out before finishing.'
                    : 'The last run was stopped before finishing.'}{' '}
                You can pick it up from where it left off — completed steps are skipped.
              </span>
              <button
                onClick={() => onResume(sessionState === 'failed' ? 'retry' : 'resume')}
                className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 px-2.5 py-1 text-xs font-medium text-amber-100 transition-colors shrink-0"
                title={
                  sessionState === 'failed'
                    ? 'Retry the failed run from recovered progress'
                    : 'Resume from recovered progress'
                }
              >
                <RotateCcw className="w-3.5 h-3.5" />
                {sessionState === 'failed' ? 'Retry' : 'Resume'}
              </button>
            </div>
          )}

          {/* Attached files preview */}
          <AttachedFilesPreview
            files={attachedFiles}
            onRemove={onRemoveAttachedFile}
            onClearAll={onClearAttachedFiles}
            variant="session"
          />

          <div className="flex items-center gap-1 bg-[#1e1f22] rounded-2xl px-2 py-1.5 transition-colors">
            {/* Attach menu */}
            <div className="relative" ref={attachMenuRef}>
              <button
                type="button"
                onClick={() => setShowAttachMenu(!showAttachMenu)}
                className={`p-2 rounded-xl transition-colors shrink-0 ${
                  showAttachMenu
                    ? 'bg-white/[0.1] text-white'
                    : 'hover:bg-neutral-700 text-gray-400 hover:text-gray-300'
                }`}
                title="Attach files or data"
              >
                <Plus className="w-4 h-4" />
              </button>
              {showAttachMenu && (
                <div className="absolute bottom-full left-0 mb-2 w-52 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => e.target.files && onFilesSelected(e.target.files)}
                  />
                  <input
                    ref={folderInputRef}
                    type="file"
                    // @ts-ignore
                    webkitdirectory=""
                    directory=""
                    multiple
                    className="hidden"
                    onChange={(e) => e.target.files && onFilesSelected(e.target.files)}
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <Upload className="w-4 h-4 text-gray-500" />
                    Upload files
                  </button>
                  <button
                    onClick={() => folderInputRef.current?.click()}
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <FolderUp className="w-4 h-4 text-gray-500" />
                    Upload folder
                  </button>
                  <div className="border-t border-white/[0.06]" />
                  <button
                    onClick={() => {
                      setShowAttachMenu(false);
                      onOpenS3Browser();
                    }}
                    className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                  >
                    <HardDrive className="w-4 h-4 text-gray-500" />
                    Browse S3 data
                  </button>
                </div>
              )}
            </div>

            {/* HITL approval-gate toggle (issue #108). OFF by default —
                when ON, agents post consequential decisions (target column,
                prep plan, model shortlist) as approval cards and block on
                the user's Approve/Edit. */}
            <button
              type="button"
              onClick={onToggleApprovals}
              className={`p-2 rounded-xl transition-colors shrink-0 ${
                approvalsEnabled
                  ? 'bg-emerald-500/20 text-emerald-400'
                  : 'hover:bg-neutral-700 text-gray-400 hover:text-gray-300'
              }`}
              title={
                approvalsEnabled
                  ? 'Approval gates ON — the agent will ask before consequential decisions'
                  : 'Approval gates OFF — click to require your approval for consequential decisions'
              }
            >
              <ShieldCheck className="w-4 h-4" />
            </button>

            <MentionInput
              draft={draft}
              onChange={onDraftChange}
              onSubmit={() =>
                isRunning && isDraftEmpty(draft) && attachedFiles.length === 0 ? onStop() : onSend()
              }
              placeholder="Ask anything"
              className="flex-1 py-1.5"
              projectId={activeProjectId}
              experiments={experiments}
              attachedFilesInSession={sessionAttachedFiles}
            />
            {isRunning && isDraftEmpty(draft) && attachedFiles.length === 0 ? (
              <button
                onClick={onStop}
                className="p-2 bg-red-600 hover:bg-red-700 rounded-xl transition-colors shrink-0"
                title="Stop agent"
              >
                <Square className="w-4 h-4 text-white" />
              </button>
            ) : (
              <button
                onClick={onSend}
                disabled={isDraftEmpty(draft) && attachedFiles.length === 0}
                title="Send message"
                className="p-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-30 rounded-xl transition-colors shrink-0"
              >
                {attachingFiles ? (
                  <Loader2 className="w-4 h-4 text-white animate-spin" />
                ) : (
                  <Send className="w-4 h-4 text-white" />
                )}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
