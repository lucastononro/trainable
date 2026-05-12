'use client';

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import {
  Plus,
  MessageSquare,
  FolderPlus,
  Loader2,
  Trash2,
  PanelLeftOpen,
  PanelLeftClose,
  ChevronRight,
  Pencil,
  Check,
  AlertCircle,
  Settings,
  Search,
  X,
  Box,
  FlaskConical,
} from 'lucide-react';
import Link from 'next/link';
import { useRouter, usePathname } from 'next/navigation';
import { useApp } from '@/lib/AppContext';
import { api } from '@/lib/api';
import type { Experiment, Project, SandboxConfig } from '@/lib/types';
import ConfirmModal from './ConfirmModal';
import ProjectSettingsModal from './ProjectSettingsModal';

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'now';
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return `${Math.floor(days / 7)}w`;
}

function statusDot(state: string | null): string {
  if (!state) return 'bg-gray-600';
  if (state.includes('running')) return 'bg-amber-400 animate-pulse';
  if (state.includes('done') || state === 'train_done') return 'bg-green-400';
  if (state === 'failed') return 'bg-red-400';
  return 'bg-gray-600';
}

function StatusIcon({ state }: { state: string | null }) {
  if (state && state.includes('running')) {
    return (
      <Loader2 className="w-3 h-3 shrink-0 text-amber-400 animate-spin" aria-label="running" />
    );
  }
  if (state === 'failed') {
    return <AlertCircle className="w-3 h-3 shrink-0 text-red-400" aria-label="failed" />;
  }
  if (state && (state.includes('done') || state === 'train_done')) {
    return <Check className="w-3 h-3 shrink-0 text-green-400" aria-label="done" />;
  }
  // Idle / unknown — fall back to the original dot so brand-new chats don't
  // shout "completed" before they've run.
  return <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot(state)}`} />;
}

// -----------------------------------------------------------------------------
// EditableName — Finder-style: click an already-selected item's name to rename.
// Double-click always works as a shortcut. Parents can also trigger rename
// externally via `startEditing`, or programmatically via a pencil button.
// -----------------------------------------------------------------------------
export interface EditableNameHandle {
  startEdit: () => void;
}

const EditableName = ({
  value,
  onSave,
  className,
  inputClassName,
  startEditing,
  onDoneEditing,
  clickToEdit,
  editHandleRef,
}: {
  value: string;
  onSave: (v: string) => void;
  className?: string;
  inputClassName?: string;
  startEditing?: boolean;
  onDoneEditing?: () => void;
  /** When true, a single click on the name enters edit mode (and stops propagation). */
  clickToEdit?: boolean;
  /** Lets the parent imperatively open edit mode, e.g. from a pencil button. */
  editHandleRef?: React.MutableRefObject<EditableNameHandle | null>;
}) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (startEditing) setEditing(true);
  }, [startEditing]);

  useEffect(() => {
    if (editHandleRef) {
      editHandleRef.current = { startEdit: () => setEditing(true) };
    }
  }, [editHandleRef]);

  useEffect(() => {
    if (editing) {
      // Focus and select after render
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [editing]);

  const finish = useCallback(
    (commit: boolean) => {
      if (commit && draft.trim() && draft.trim() !== value) {
        onSave(draft.trim());
      } else {
        setDraft(value);
      }
      setEditing(false);
      onDoneEditing?.();
    },
    [draft, value, onSave, onDoneEditing],
  );

  if (!editing) {
    return (
      <span
        className={className}
        onClick={(e) => {
          if (clickToEdit) {
            e.stopPropagation();
            setEditing(true);
          }
          // Otherwise let the click propagate so the parent row can select.
        }}
        onDoubleClick={(e) => {
          e.stopPropagation();
          setEditing(true);
        }}
        title={clickToEdit ? 'Click to rename' : 'Double-click to rename'}
      >
        {value}
      </span>
    );
  }

  return (
    <input
      ref={inputRef}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          finish(true);
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          finish(false);
        }
      }}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={`bg-white/[0.08] outline-none rounded px-1 min-w-0 ${className ?? ''} ${inputClassName ?? ''}`}
    />
  );
};

// -----------------------------------------------------------------------------
// ExperimentRow — draggable chat entry with click-to-rename + hover pencil
// -----------------------------------------------------------------------------
function ExperimentRow({
  exp,
  isActive,
  liveRunning,
  onClick,
  onRename,
  onDelete,
  onDragStart,
  onDragEnd,
}: {
  exp: Experiment;
  isActive: boolean;
  liveRunning: boolean;
  onClick: () => void;
  onRename: (newName: string) => void;
  onDelete: (e: React.MouseEvent) => void;
  onDragStart: (e: React.DragEvent) => void;
  onDragEnd: () => void;
}) {
  // Drive the spinner from local `isRunning` for the active row so it lights
  // up the moment the user submits, instead of waiting for an experiments
  // refresh round-trip to surface the backend's `*_running` state.
  const displayState = liveRunning ? 'chat_running' : exp.latest_state;
  const editHandle = useRef<EditableNameHandle | null>(null);
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onClick={onClick}
      title={exp.name}
      className={`w-full flex items-center gap-2.5 pl-6 pr-2 py-1.5 rounded-lg text-left transition-colors group cursor-pointer ${
        isActive
          ? 'bg-white/[0.08] text-white'
          : 'text-gray-400 hover:bg-white/[0.04] hover:text-gray-300'
      }`}
    >
      <StatusIcon state={displayState} />
      <div className="flex-1 min-w-0">
        <EditableName
          value={exp.name}
          onSave={onRename}
          className="text-sm truncate block"
          clickToEdit={isActive}
          editHandleRef={editHandle}
        />
      </div>
      <span className="text-[10px] text-gray-600 shrink-0">{timeAgo(exp.created_at)}</span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          editHandle.current?.startEdit();
        }}
        className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
        title="Rename chat"
      >
        <Pencil className="w-3 h-3 text-gray-500" />
      </button>
      <button
        onClick={onDelete}
        className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
        title="Delete chat"
      >
        <Trash2 className="w-3 h-3 text-gray-500" />
      </button>
    </div>
  );
}

// -----------------------------------------------------------------------------
// ProjectSection — collapsible header + drop target
// -----------------------------------------------------------------------------
function ProjectSection({
  project,
  expanded,
  onToggleExpanded,
  isActiveProject,
  startRenaming,
  onRenameDone,
  onRename,
  onSettings,
  onDelete,
  onDrop,
  isDropTarget,
  onDragOver,
  onDragLeave,
  children,
}: {
  project: Project;
  expanded: boolean;
  onToggleExpanded: () => void;
  isActiveProject: boolean;
  startRenaming: boolean;
  onRenameDone: () => void;
  onRename: (newName: string) => void;
  onSettings: (e: React.MouseEvent) => void;
  onDelete: (e: React.MouseEvent) => void;
  onDrop: (e: React.DragEvent) => void;
  isDropTarget: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  children: React.ReactNode;
}) {
  const editHandle = useRef<EditableNameHandle | null>(null);
  return (
    <div
      className={`rounded-lg transition-colors ${
        isDropTarget ? 'bg-primary-500/10 ring-1 ring-primary-500/30' : ''
      }`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div
        onClick={onToggleExpanded}
        title={project.name}
        className={`flex items-center gap-1.5 px-1.5 py-1.5 rounded-lg cursor-pointer group ${
          isActiveProject ? 'text-white' : 'text-gray-300 hover:bg-white/[0.04]'
        }`}
      >
        <ChevronRight
          className={`w-3 h-3 text-gray-500 shrink-0 transition-transform duration-150 ${
            expanded ? 'rotate-90' : ''
          }`}
        />
        <div className="flex-1 min-w-0">
          <EditableName
            value={project.name}
            onSave={onRename}
            className="text-xs font-semibold truncate block"
            startEditing={startRenaming}
            onDoneEditing={onRenameDone}
            clickToEdit={isActiveProject}
            editHandleRef={editHandle}
          />
        </div>
        <span className="text-[10px] text-gray-600 shrink-0">{project.experiment_count}</span>
        <button
          onClick={(e) => {
            e.stopPropagation();
            editHandle.current?.startEdit();
          }}
          className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
          title="Rename project"
        >
          <Pencil className="w-3 h-3 text-gray-500" />
        </button>
        <button
          onClick={onSettings}
          className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
          title="Project settings"
        >
          <Settings className="w-3 h-3 text-gray-500" />
        </button>
        <button
          onClick={onDelete}
          className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
          title="Delete project"
        >
          <Trash2 className="w-3 h-3 text-gray-500" />
        </button>
      </div>
      {expanded && <div className="space-y-0.5 pb-1">{children}</div>}
    </div>
  );
}

// -----------------------------------------------------------------------------
// Sidebar
// -----------------------------------------------------------------------------
export default function Sidebar() {
  const {
    projects,
    activeProjectId,
    experiments,
    activeExperimentId,
    setActiveExperiment,
    setActiveProject,
    refreshExperiments,
    refreshProjects,
    sidebarOpen,
    setSidebarOpen,
    isRunning,
  } = useApp();
  const router = useRouter();
  const pathname = usePathname();
  const [creating, setCreating] = useState(false);
  const [pendingRenameProjectId, setPendingRenameProjectId] = useState<string | null>(null);
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(() => new Set());
  const [dropTargetProjectId, setDropTargetProjectId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<
    { kind: 'project'; id: string } | { kind: 'experiment'; id: string; name: string } | null
  >(null);
  const [settingsProjectId, setSettingsProjectId] = useState<string | null>(null);

  // Expand the active project whenever it changes.
  useEffect(() => {
    if (activeProjectId) {
      setExpandedProjectIds((prev) => {
        if (prev.has(activeProjectId)) return prev;
        const next = new Set(prev);
        next.add(activeProjectId);
        return next;
      });
    }
  }, [activeProjectId]);

  const toggleExpanded = useCallback((projectId: string) => {
    setExpandedProjectIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }, []);

  const filteredExperiments = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return experiments.filter((exp) => {
      if (exp.archived) return false;
      if (q) {
        const hay = `${exp.name} ${exp.description ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (activeTag) {
        const tags = (exp.tags as string[] | undefined) ?? [];
        if (!tags.includes(activeTag)) return false;
      }
      return true;
    });
  }, [experiments, searchQuery, activeTag]);

  const experimentsByProject = useMemo(() => {
    const map = new Map<string, Experiment[]>();
    for (const exp of filteredExperiments) {
      if (!map.has(exp.project_id)) map.set(exp.project_id, []);
      map.get(exp.project_id)!.push(exp);
    }
    // Pinned first, then most-recent-first
    map.forEach((arr: Experiment[]) => {
      arr.sort((a: Experiment, b: Experiment) => {
        const ap = a.pinned ? 1 : 0;
        const bp = b.pinned ? 1 : 0;
        if (ap !== bp) return bp - ap;
        return (b.created_at ?? '').localeCompare(a.created_at ?? '');
      });
    });
    return map;
  }, [filteredExperiments]);

  // Project filter: when search is active, hide projects that don't
  // match the query AND don't contain a matching experiment. Without
  // this the sidebar showed every project even when searching for
  // something only present in one — defeating the search.
  const filteredProjects = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q && !activeTag) return projects;
    return projects.filter((p) => {
      if (q && p.name.toLowerCase().includes(q)) return true;
      // Project has a hit experiment in the post-filter map.
      const hits = experimentsByProject.get(p.id);
      return Boolean(hits && hits.length > 0);
    });
  }, [projects, searchQuery, activeTag, experimentsByProject]);

  const allTags = useMemo(() => {
    const seen = new Map<string, number>();
    for (const exp of experiments) {
      for (const t of (exp.tags as string[] | undefined) ?? []) {
        seen.set(t, (seen.get(t) ?? 0) + 1);
      }
    }
    return Array.from(seen.entries()).sort((a, b) => b[1] - a[1]);
  }, [experiments]);

  const handleNewProject = useCallback(async () => {
    setCreating(true);
    try {
      const result = await api.createProject();
      await refreshProjects();
      await refreshExperiments();
      setActiveProject(result.project.id);
      setActiveExperiment(result.experiment.id, result.session_id);
      setExpandedProjectIds((prev) => {
        const next = new Set(prev);
        next.add(result.project.id);
        return next;
      });
      // Flag the new project for inline rename immediately.
      setPendingRenameProjectId(result.project.id);
    } catch {
      // silent
    } finally {
      setCreating(false);
    }
  }, [refreshProjects, refreshExperiments, setActiveProject, setActiveExperiment]);

  const handleNewChatInProject = useCallback(
    async (projectId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      setCreating(true);
      try {
        const result = await api.quickCreate(projectId);
        await refreshExperiments();
        await refreshProjects();
        setActiveExperiment(result.id, result.session_id);
      } catch {
        // silent
      } finally {
        setCreating(false);
      }
    },
    [refreshExperiments, refreshProjects, setActiveExperiment],
  );

  const handleRenameProject = useCallback(
    async (projectId: string, newName: string) => {
      try {
        await api.updateProject(projectId, { name: newName });
        await refreshProjects();
      } catch {
        // silent
      }
    },
    [refreshProjects],
  );

  const handleDeleteProject = useCallback((projectId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setConfirmTarget({ kind: 'project', id: projectId });
  }, []);

  const handleSaveSandboxConfig = useCallback(
    async (projectId: string, config: SandboxConfig) => {
      try {
        await api.updateProject(projectId, { sandbox_config: config });
        await refreshProjects();
      } catch {
        // silent
      }
    },
    [refreshProjects],
  );

  const confirmDeleteProject = useCallback(
    async (projectId: string) => {
      try {
        await api.deleteProject(projectId);
        if (activeProjectId === projectId) setActiveProject(null);
        await refreshProjects();
        await refreshExperiments();
      } catch {
        // silent
      }
    },
    [activeProjectId, setActiveProject, refreshProjects, refreshExperiments],
  );

  const handleRenameExperiment = useCallback(
    async (expId: string, newName: string) => {
      try {
        await api.updateExperiment(expId, { name: newName });
        await refreshExperiments();
      } catch {
        // silent
      }
    },
    [refreshExperiments],
  );

  const handleDeleteExperiment = useCallback(
    (expId: string, e: React.MouseEvent) => {
      e.stopPropagation();
      const exp = experiments.find((x) => x.id === expId);
      const name = exp?.name || 'this chat';
      setConfirmTarget({ kind: 'experiment', id: expId, name });
    },
    [experiments],
  );

  const confirmDeleteExperiment = useCallback(
    async (expId: string) => {
      try {
        await api.deleteExperiment(expId);
        if (activeExperimentId === expId) {
          setActiveExperiment(null);
        }
        await refreshExperiments();
        await refreshProjects();
      } catch {
        // silent
      }
    },
    [activeExperimentId, setActiveExperiment, refreshExperiments, refreshProjects],
  );

  const handleDragStart = useCallback((e: React.DragEvent, expId: string) => {
    e.dataTransfer.setData('text/plain', expId);
    e.dataTransfer.effectAllowed = 'move';
  }, []);

  const handleDragOver = useCallback(
    (e: React.DragEvent, projectId: string) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (dropTargetProjectId !== projectId) setDropTargetProjectId(projectId);
    },
    [dropTargetProjectId],
  );

  const handleDragLeave = useCallback(() => {
    setDropTargetProjectId(null);
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent, targetProjectId: string) => {
      e.preventDefault();
      setDropTargetProjectId(null);
      const expId = e.dataTransfer.getData('text/plain');
      if (!expId) return;
      const exp = experiments.find((x) => x.id === expId);
      if (!exp || exp.project_id === targetProjectId) return;
      try {
        await api.updateExperiment(expId, { project_id: targetProjectId });
        await refreshExperiments();
        await refreshProjects();
      } catch {
        // silent
      }
    },
    [experiments, refreshExperiments, refreshProjects],
  );

  return (
    <div
      className={`shrink-0 h-full flex flex-col bg-black border-r border-white/[0.06] transition-all duration-300 ease-in-out overflow-hidden ${
        sidebarOpen ? 'w-[260px]' : 'w-[52px]'
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-2.5 py-3 shrink-0">
        {sidebarOpen ? (
          <>
            <a href="/" className="shrink-0 ml-0.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/logo-brain-transparent.png" alt="Trainable" className="h-5 w-auto" />
            </a>
            <span className="text-sm font-semibold text-gray-300 truncate">Trainable</span>
            <div className="flex-1" />
            <button
              onClick={handleNewProject}
              disabled={creating}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-400 hover:text-white"
              title="New project"
            >
              {creating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <FolderPlus className="w-4 h-4" />
              )}
            </button>
            <button
              onClick={() => setSidebarOpen(false)}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-500 hover:text-gray-300"
              title="Collapse sidebar"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </>
        ) : (
          <div className="flex flex-col items-center gap-1 w-full">
            <a href="/" className="p-1.5 flex items-center justify-center" title="Trainable">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/logo-brain-transparent.png" alt="Trainable" className="h-5 w-auto" />
            </a>
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-500 hover:text-gray-300"
              title="Expand sidebar"
            >
              <PanelLeftOpen className="w-4 h-4" />
            </button>
            <button
              onClick={handleNewProject}
              disabled={creating}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-400 hover:text-white"
              title="New project"
            >
              {creating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <FolderPlus className="w-4 h-4" />
              )}
            </button>
          </div>
        )}
      </div>

      {sidebarOpen && (
        <div className="px-2 pb-1.5 space-y-1.5">
          {/* Top-of-tree quick nav: Experiments / Models. Lineage was
              removed — clicking an experiment row in /experiments now
              opens its lineage view, so the dedicated nav was redundant. */}
          <div className="space-y-0.5 pb-1.5 border-b border-white/[0.05]">
            <Link
              href="/experiments"
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.04] transition-colors"
              title="Experiments list"
            >
              <FlaskConical className="w-3.5 h-3.5 text-amber-400" />
              Experiments
            </Link>
            <Link
              href="/models"
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-gray-400 hover:text-gray-100 hover:bg-white/[0.04] transition-colors"
              title="Registered models"
            >
              <Box className="w-3.5 h-3.5 text-blue-400" />
              Models
            </Link>
          </div>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-600" />
            <input
              type="text"
              placeholder="Search projects + chats…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full text-xs bg-white/[0.04] border border-white/[0.06] rounded-md pl-7 pr-7 py-1.5 text-gray-300 placeholder-gray-600 focus:outline-none focus:border-white/[0.15]"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-white/[0.08] text-gray-500"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
          {allTags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {allTags.slice(0, 8).map(([tag, count]) => (
                <button
                  key={tag}
                  onClick={() => setActiveTag(activeTag === tag ? null : tag)}
                  className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                    activeTag === tag
                      ? 'bg-violet-500/20 border-violet-500/30 text-violet-200'
                      : 'bg-white/[0.03] border-white/[0.06] text-gray-500 hover:text-gray-300'
                  }`}
                >
                  #{tag}
                  <span className="ml-1 text-[9px] text-gray-600">{count}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Project + experiment tree */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-1.5 space-y-0.5">
        {sidebarOpen && (
          <>
            {projects.length === 0 && !creating && (
              <div className="px-3 py-8 text-center">
                <MessageSquare className="w-8 h-8 text-gray-700 mx-auto mb-2" />
                <p className="text-xs text-gray-600">No projects yet</p>
                <p className="text-[10px] text-gray-700 mt-1">Click the folder button to start</p>
              </div>
            )}
            {projects.length > 0 && filteredProjects.length === 0 && (
              <div className="px-3 py-6 text-center">
                <p className="text-[11px] text-gray-600">
                  No matches for &ldquo;{searchQuery}&rdquo;
                </p>
              </div>
            )}
            {filteredProjects.map((project) => {
              const projectExperiments = experimentsByProject.get(project.id) ?? [];
              const isExpanded = expandedProjectIds.has(project.id);
              const isActiveProject = project.id === activeProjectId;
              return (
                <ProjectSection
                  key={project.id}
                  project={project}
                  expanded={isExpanded}
                  isActiveProject={isActiveProject}
                  startRenaming={pendingRenameProjectId === project.id}
                  onRenameDone={() => setPendingRenameProjectId(null)}
                  onToggleExpanded={() => {
                    toggleExpanded(project.id);
                    setActiveProject(project.id);
                  }}
                  onRename={(name) => handleRenameProject(project.id, name)}
                  onSettings={(e) => {
                    e.stopPropagation();
                    setSettingsProjectId(project.id);
                  }}
                  onDelete={(e) => handleDeleteProject(project.id, e)}
                  isDropTarget={dropTargetProjectId === project.id}
                  onDragOver={(e) => handleDragOver(e, project.id)}
                  onDragLeave={handleDragLeave}
                  onDrop={(e) => handleDrop(e, project.id)}
                >
                  {(() => {
                    // Bucket experiments by session so multi-experiment
                    // chats render as ONE sidebar row, not N. The user
                    // perceived N rows as "N new sessions with the same
                    // conversation" — they all do point to the same
                    // session, so a single entry is the honest UI. The
                    // `+N more` suffix tells the user there are siblings;
                    // the canvas / /experiments page is where you drill
                    // into the individual experiments.
                    // Each bucket is anchored on the FIRST experiment
                    // (the one that "owns" the chat's name). Adding new
                    // sibling experiments doesn't rename the row — the
                    // user's mental model is "this chat" not "this
                    // experiment", so the label has to be stable. The
                    // count badge surfaces the sibling count.
                    type Bucket = {
                      key: string;
                      anchor: Experiment;
                      siblings: Experiment[];
                      latest: Experiment;
                    };
                    const order: string[] = [];
                    const buckets = new Map<string, Bucket>();
                    for (const exp of projectExperiments) {
                      const key = exp.session_id ?? exp.latest_session_id ?? exp.id;
                      const existing = buckets.get(key);
                      if (!existing) {
                        order.push(key);
                        buckets.set(key, {
                          key,
                          anchor: exp,
                          siblings: [],
                          latest: exp,
                        });
                      } else {
                        existing.siblings.push(exp);
                        // Anchor stays as whichever experiment was
                        // created earliest — the chat's "original" name.
                        if ((exp.created_at ?? '') < (existing.anchor.created_at ?? '')) {
                          existing.siblings.push(existing.anchor);
                          existing.anchor = exp;
                        }
                        // Track latest separately so click → most recent.
                        if ((exp.created_at ?? '') > (existing.latest.created_at ?? '')) {
                          existing.latest = exp;
                        }
                      }
                    }
                    return order.map((key) => {
                      const b = buckets.get(key)!;
                      const total = 1 + b.siblings.length;
                      const displayExp =
                        total > 1
                          ? {
                              ...b.anchor,
                              name: `${b.anchor.name} · ${total}`,
                            }
                          : b.anchor;
                      const isActive =
                        b.anchor.id === activeExperimentId ||
                        b.latest.id === activeExperimentId ||
                        b.siblings.some((s) => s.id === activeExperimentId);
                      return (
                        <ExperimentRow
                          key={key}
                          exp={displayExp}
                          isActive={isActive}
                          // Live spinner mirrors the same isActive logic so
                          // a sibling experiment running in the same chat
                          // still flips the bucketed row to spinning state.
                          liveRunning={isActive && isRunning}
                          // Click → drop into the chat at the latest
                          // experiment so a fresh sibling is selected on
                          // entry. The row's NAME stays anchored.
                          onClick={() => {
                            setActiveExperiment(
                              b.latest.id,
                              b.latest.session_id ?? b.latest.latest_session_id,
                            );
                            if (pathname !== '/') router.push('/');
                          }}
                          onRename={(name) => handleRenameExperiment(b.anchor.id, name)}
                          onDelete={(e) => handleDeleteExperiment(b.anchor.id, e)}
                          onDragStart={(e) => handleDragStart(e, b.anchor.id)}
                          onDragEnd={handleDragLeave}
                        />
                      );
                    });
                  })()}
                  <button
                    onClick={(e) => handleNewChatInProject(project.id, e)}
                    title={`New chat in ${project.name}`}
                    className="w-full flex items-center gap-2 pl-6 pr-2 py-1.5 rounded-lg text-left text-gray-500 hover:bg-white/[0.04] hover:text-gray-300 transition-colors text-xs"
                  >
                    <Plus className="w-3 h-3" />
                    New chat
                  </button>
                </ProjectSection>
              );
            })}
          </>
        )}
      </div>

      <ConfirmModal
        isOpen={confirmTarget !== null}
        title={confirmTarget?.kind === 'project' ? 'Delete project?' : 'Delete chat?'}
        message={
          confirmTarget?.kind === 'project'
            ? 'Delete this project and all its chats? This cannot be undone.'
            : `Delete "${confirmTarget?.kind === 'experiment' ? confirmTarget.name : ''}"? This cannot be undone.`
        }
        onCancel={() => setConfirmTarget(null)}
        onConfirm={() => {
          if (!confirmTarget) return;
          const target = confirmTarget;
          setConfirmTarget(null);
          if (target.kind === 'project') confirmDeleteProject(target.id);
          else confirmDeleteExperiment(target.id);
        }}
      />

      {(() => {
        const settingsProject = settingsProjectId
          ? projects.find((p) => p.id === settingsProjectId)
          : null;
        return (
          <ProjectSettingsModal
            isOpen={settingsProjectId !== null}
            projectName={settingsProject?.name ?? ''}
            sandboxConfig={settingsProject?.sandbox_config ?? {}}
            onSave={(config) => {
              if (settingsProjectId) handleSaveSandboxConfig(settingsProjectId, config);
            }}
            onClose={() => setSettingsProjectId(null)}
          />
        );
      })()}
    </div>
  );
}
