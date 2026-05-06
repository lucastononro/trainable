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
} from 'lucide-react';
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
  onClick,
  onRename,
  onDelete,
  onDragStart,
  onDragEnd,
}: {
  exp: Experiment;
  isActive: boolean;
  onClick: () => void;
  onRename: (newName: string) => void;
  onDelete: (e: React.MouseEvent) => void;
  onDragStart: (e: React.DragEvent) => void;
  onDragEnd: () => void;
}) {
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
      <StatusIcon state={exp.latest_state} />
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
  } = useApp();
  const [creating, setCreating] = useState(false);
  const [pendingRenameProjectId, setPendingRenameProjectId] = useState<string | null>(null);
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(() => new Set());
  const [dropTargetProjectId, setDropTargetProjectId] = useState<string | null>(null);
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

  const experimentsByProject = useMemo(() => {
    const map = new Map<string, Experiment[]>();
    for (const exp of experiments) {
      if (!map.has(exp.project_id)) map.set(exp.project_id, []);
      map.get(exp.project_id)!.push(exp);
    }
    return map;
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
            {projects.map((project) => {
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
                  {projectExperiments.map((exp) => (
                    <ExperimentRow
                      key={exp.id}
                      exp={exp}
                      isActive={exp.id === activeExperimentId}
                      onClick={() => setActiveExperiment(exp.id, exp.latest_session_id)}
                      onRename={(name) => handleRenameExperiment(exp.id, name)}
                      onDelete={(e) => handleDeleteExperiment(exp.id, e)}
                      onDragStart={(e) => handleDragStart(e, exp.id)}
                      onDragEnd={handleDragLeave}
                    />
                  ))}
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
