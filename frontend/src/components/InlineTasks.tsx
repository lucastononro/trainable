'use client';

import { useState } from 'react';
import { ChevronDown, ChevronRight, ListChecks, Plus, Trash2, X } from 'lucide-react';
import { Task, TaskStatus, TaskCreatePayload, TaskUpdatePayload } from '@/lib/types';
import { StatusIcon } from './AgentStatusIndicator';

const ICON_STATUS: Record<TaskStatus, string> = {
  pending: 'pending',
  in_progress: 'running',
  completed: 'completed',
};

const NEXT_STATUS: Record<TaskStatus, TaskStatus> = {
  pending: 'in_progress',
  in_progress: 'completed',
  completed: 'pending',
};

function summary(tasks: Task[]) {
  let done = 0,
    doing = 0,
    todo = 0;
  for (const t of tasks) {
    if (t.status === 'completed') done++;
    else if (t.status === 'in_progress') doing++;
    else todo++;
  }
  return { done, doing, todo };
}

interface Props {
  tasks: Task[];
  onCreate: (body: TaskCreatePayload) => Promise<void> | void;
  onUpdate: (id: number, body: TaskUpdatePayload) => Promise<void> | void;
  onDelete: (id: number) => Promise<void> | void;
}

export default function InlineTasks({ tasks, onCreate, onUpdate, onDelete }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  if (tasks.length === 0 && !adding) {
    return null;
  }

  const { done, doing, todo } = summary(tasks);

  return (
    <div className="rounded-xl border border-white/[0.08] bg-surface-elevated overflow-hidden text-sm">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/[0.03] transition-colors"
      >
        <ListChecks className="w-3.5 h-3.5 text-violet-400 shrink-0" />
        <span className="font-medium text-gray-200">Tasks</span>
        <span className="text-[11px] text-gray-500">
          {done}/{tasks.length} done
          {doing > 0 && <span className="text-amber-400 ml-2">{doing} in progress</span>}
        </span>
        <span className="ml-auto flex items-center gap-1">
          <span
            role="button"
            tabIndex={0}
            aria-label="Add task"
            onClick={(e) => {
              e.stopPropagation();
              setAdding(true);
              setCollapsed(false);
            }}
            className="p-1 rounded hover:bg-white/[0.08] text-gray-400 hover:text-gray-200"
            title="Add task"
          >
            <Plus className="w-3.5 h-3.5" />
          </span>
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5 text-gray-600" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-gray-600" />
          )}
        </span>
      </button>

      {!collapsed && (
        <div className="border-t border-white/[0.04]">
          {tasks.map((t) => (
            <TaskRow
              key={t.id}
              task={t}
              expanded={expandedId === t.id}
              onToggleStatus={() => onUpdate(t.id, { status: NEXT_STATUS[t.status] })}
              onToggleExpand={() => setExpandedId(expandedId === t.id ? null : t.id)}
              onDelete={() => onDelete(t.id)}
            />
          ))}

          {adding && (
            <TaskEditor
              initial={{ subject: '', short_description: '', description: '' }}
              onSave={async (body) => {
                await onCreate(body);
                setAdding(false);
              }}
              onCancel={() => setAdding(false)}
            />
          )}
        </div>
      )}
    </div>
  );
}

interface RowProps {
  task: Task;
  expanded: boolean;
  onToggleStatus: () => void;
  onToggleExpand: () => void;
  onDelete: () => void;
}

function TaskRow({ task, expanded, onToggleStatus, onToggleExpand, onDelete }: RowProps) {
  const completed = task.status === 'completed';
  const inProgress = task.status === 'in_progress';
  const label = inProgress && task.active_form ? task.active_form : task.subject;
  const hasLong = !!task.description;

  return (
    <div className="group border-b border-white/[0.03] last:border-0">
      <div className="flex items-start gap-2 px-3 py-1.5 hover:bg-white/[0.02]">
        <button
          onClick={onToggleStatus}
          className="pt-0.5 cursor-pointer hover:opacity-80"
          title={`Click to mark ${NEXT_STATUS[task.status].replace('_', ' ')}`}
        >
          <StatusIcon status={ICON_STATUS[task.status]} color={inProgress ? 'amber' : 'gray'} />
        </button>

        <button
          onClick={hasLong ? onToggleExpand : undefined}
          className={`flex-1 text-left ${hasLong ? 'cursor-pointer' : 'cursor-default'} min-w-0`}
        >
          <div
            className={
              completed
                ? 'text-gray-500 line-through truncate'
                : inProgress
                  ? 'text-gray-100 truncate'
                  : 'text-gray-200 truncate'
            }
          >
            {label}
            {hasLong && (
              <span className="ml-1 text-gray-600">
                {expanded ? (
                  <ChevronDown className="w-3 h-3 inline" />
                ) : (
                  <ChevronRight className="w-3 h-3 inline" />
                )}
              </span>
            )}
          </div>
          {task.short_description && (
            <div className="text-[11px] text-gray-500 mt-0.5 line-clamp-1">
              {task.short_description}
            </div>
          )}
        </button>

        <button
          onClick={onDelete}
          className="p-1 rounded hover:bg-rose-500/20 text-gray-400 hover:text-rose-300 opacity-0 group-hover:opacity-100 transition-opacity"
          title="Delete"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>

      {expanded && hasLong && (
        <div className="px-9 pb-2 text-[12px] text-gray-400 whitespace-pre-wrap leading-relaxed">
          {task.description}
        </div>
      )}
    </div>
  );
}

interface EditorProps {
  initial: { subject: string; short_description: string; description: string };
  onSave: (body: TaskCreatePayload) => Promise<void> | void;
  onCancel: () => void;
}

function TaskEditor({ initial, onSave, onCancel }: EditorProps) {
  const [subject, setSubject] = useState(initial.subject);
  const [shortDesc, setShortDesc] = useState(initial.short_description);
  const [longDesc, setLongDesc] = useState(initial.description);

  async function save() {
    const trimmed = subject.trim();
    if (!trimmed) return;
    await onSave({
      subject: trimmed,
      short_description: shortDesc,
      description: longDesc,
    });
  }

  return (
    <div className="border-y border-primary-500/30 bg-primary-500/[0.04] px-3 py-2 space-y-1.5">
      <input
        type="text"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        autoFocus
        placeholder="Task name (required)"
        className="w-full bg-black/40 border border-white/[0.08] rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-primary-500"
      />
      <input
        type="text"
        value={shortDesc}
        onChange={(e) => setShortDesc(e.target.value)}
        placeholder="One-line summary (optional)"
        className="w-full bg-black/40 border border-white/[0.08] rounded px-2 py-1 text-[12px] text-gray-300 focus:outline-none focus:border-primary-500"
      />
      <textarea
        value={longDesc}
        onChange={(e) => setLongDesc(e.target.value)}
        rows={2}
        placeholder="Notes (optional)"
        className="w-full bg-black/40 border border-white/[0.08] rounded px-2 py-1 text-[12px] text-gray-300 focus:outline-none focus:border-primary-500 resize-y"
      />
      <div className="flex justify-end gap-2 pt-0.5">
        <button
          onClick={onCancel}
          className="px-2 py-0.5 text-[11px] text-gray-400 hover:text-gray-200 flex items-center gap-1"
        >
          <X className="w-3 h-3" /> Cancel
        </button>
        <button
          onClick={save}
          disabled={!subject.trim()}
          className="px-2 py-0.5 text-[11px] rounded bg-primary-500 text-white hover:bg-primary-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Save
        </button>
      </div>
    </div>
  );
}
