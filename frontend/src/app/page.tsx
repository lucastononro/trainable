'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '@/lib/AppContext';
import { SSEStreamProvider } from '@/lib/SSEStreamContext';
import { api } from '@/lib/api';
import type { Mention, Draft, TaskCreatePayload, TaskUpdatePayload } from '@/lib/types';
import { draftToWire, isDraftEmpty, draftToPlainText } from '@/lib/mentions';
import {
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from 'react-resizable-panels';
import { BarChart3, Database, GripVertical, Loader2, PanelRightOpen } from 'lucide-react';
import Sidebar from '@/components/Sidebar';
import ErrorBoundary from '@/components/ErrorBoundary';
import AgentStatusIndicator from '@/components/AgentStatusIndicator';
import CostBadge from '@/components/CostBadge';
import S3FileBrowserModal from '@/components/S3FileBrowserModal';
import ProjectDataModal from '@/components/ProjectDataModal';
import ChatPane from '@/components/chat/ChatPane';
import WelcomeScreen from '@/components/chat/WelcomeScreen';
import WorkspaceCanvas from '@/components/workspace/WorkspaceCanvas';
import { useSessionStream } from '@/lib/useSessionStream';

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

function HomePageContent() {
  const {
    projects,
    experiments,
    activeExperimentId,
    activeSessionId,
    activeProjectId,
    setActiveExperiment,
    setActiveProject,
    refreshExperiments,
    refreshProjects,
    agentModels,
    agentThinking,
    isRunning,
    setIsRunning,
  } = useApp();
  // Keep a ref for stable access inside async handlers/closures
  const agentModelsRef = useRef<Record<string, string>>({});
  useEffect(() => {
    agentModelsRef.current = agentModels;
  }, [agentModels]);
  const agentThinkingRef = useRef<Record<string, string>>({});
  useEffect(() => {
    agentThinkingRef.current = agentThinking;
  }, [agentThinking]);

  const [draft, setDraft] = useState<Draft>([]);
  // HITL approval gates (issue #108). Opt-in per session; OFF by default so
  // legacy behavior is unchanged. Sent with every run-triggering message —
  // the backend re-asserts the per-session flag from it on each launch.
  const [approvalsEnabled, setApprovalsEnabled] = useState(false);
  const approvalsEnabledRef = useRef(false);
  useEffect(() => {
    approvalsEnabledRef.current = approvalsEnabled;
  }, [approvalsEnabled]);
  // Derive the displayed experiment name from the experiments list (single
  // source of truth) so a rename in the sidebar updates the header without
  // needing a session reload.
  const experimentName = useMemo(
    () => experiments.find((e) => e.id === activeExperimentId)?.name ?? '',
    [experiments, activeExperimentId],
  );

  const [canvasOpen, setCanvasOpen] = useState(false);
  const workspacePanelRef = useRef<ImperativePanelHandle>(null);

  // Opens the canvas and forces the panel to its intended default width —
  // `.expand()` alone restores the last drag-size (which may be smaller than
  // we want), so we always `.resize(...)` to the same target the PanelGroup
  // uses on first mount.
  const CANVAS_DEFAULT_SIZE = 70;
  const openCanvas = useCallback(() => {
    const p = workspacePanelRef.current;
    if (!p) return;
    p.expand();
    // Run on the next frame so the expand takes effect before we resize.
    requestAnimationFrame(() => {
      p.resize(CANVAS_DEFAULT_SIZE);
      // Notify the workspace tab manager so it can pick a sensible default
      // tab if the user opens the canvas with nothing currently active.
      window.dispatchEvent(new CustomEvent('trainable:canvas-opened'));
    });
  }, []);
  const collapseCanvas = useCallback(() => {
    workspacePanelRef.current?.collapse();
  }, []);
  const clearDraft = useCallback(() => setDraft([]), []);

  // The SSE connection + onmessage reducer and every state slice it owns
  // (chat, tasks, canvas, file tree, metrics, usage, active agents) live in
  // this hook — see lib/useSessionStream.ts.
  const {
    chatItems,
    addItem,
    sessionState,
    loading,
    sseConnected,
    tasks,
    streamingItemIdRef,
    pinnedToBottomRef,
    canvasContent,
    canvasTitle,
    generatedFiles,
    fileTree,
    htmlArtifacts,
    metricPoints,
    chartConfig,
    logEvents,
    usageTotals,
    recentUsage,
    activeAgents,
  } = useSessionStream(activeExperimentId, activeSessionId, {
    openCanvas,
    collapseCanvas,
    onReset: clearDraft,
  });

  // File attachment state
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showS3Browser, setShowS3Browser] = useState(false);
  const [showProjectData, setShowProjectData] = useState(false);
  const [attachingFiles, setAttachingFiles] = useState(false);
  const fileInputRef2 = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const attachMenuRef = useRef<HTMLDivElement>(null);

  // Pending message ref: when we auto-create an experiment, we queue the message
  const pendingMessageRef = useRef<{ content: string; mentions: Mention[] } | null>(null);
  // Pending attachment ref: when we auto-create an experiment with files, queue the upload+send
  const pendingAttachmentRef = useRef<{
    files: File[];
    text: string;
    fileNames: string[];
  } | null>(null);

  // ---------------------------------------------------------------------------
  // Send pending message once SSE is connected (after auto-create)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (pendingMessageRef.current && activeSessionId && sseConnected) {
      const pending = pendingMessageRef.current;
      pendingMessageRef.current = null;
      addItem({
        type: 'user',
        content: pending.content,
        meta: pending.mentions.length > 0 ? { mentions: pending.mentions } : undefined,
      });
      setIsRunning(true);
      api
        .sendMessage(
          activeSessionId,
          pending.content,
          true,
          agentModelsRef.current,
          pending.mentions,
          agentThinkingRef.current,
          approvalsEnabledRef.current,
        )
        .catch((e: any) => {
          addItem({ type: 'error', content: e.message });
          setIsRunning(false);
        });
    }
  }, [activeSessionId, sseConnected, addItem, setIsRunning]);

  // Drain a pending file attachment once the auto-created session's SSE is live
  useEffect(() => {
    if (!pendingAttachmentRef.current || !activeExperimentId || !activeSessionId || !sseConnected) {
      return;
    }
    const pending = pendingAttachmentRef.current;
    pendingAttachmentRef.current = null;
    const expId = activeExperimentId;
    const sesId = activeSessionId;

    (async () => {
      try {
        addItem({
          type: 'user',
          content: pending.text,
          meta: { files: pending.fileNames },
        });
        if (pending.files.length > 0) {
          await api.attachData(expId, pending.files, undefined, sesId);
          await refreshExperiments();
        }
        const agentPrompt = pending.text
          ? pending.text
          : `I've attached ${pending.fileNames.length} file${pending.fileNames.length > 1 ? 's' : ''}: ${pending.fileNames.join(', ')}. What can you tell me about this data?`;
        setIsRunning(true);
        await api.sendMessage(
          sesId,
          agentPrompt,
          true,
          agentModelsRef.current,
          undefined,
          agentThinkingRef.current,
          approvalsEnabledRef.current,
        );
      } catch (e: any) {
        addItem({ type: 'error', content: e.message });
        setIsRunning(false);
      } finally {
        setAttachingFiles(false);
      }
    })();
  }, [
    activeExperimentId,
    activeSessionId,
    sseConnected,
    addItem,
    refreshExperiments,
    setIsRunning,
  ]);

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleStop = async () => {
    if (!activeSessionId) return;
    try {
      await api.abortSession(activeSessionId);
    } catch (e: any) {
      addItem({ type: 'error', content: `Failed to stop: ${e.message}` });
    }
  };

  // Resume / retry an interrupted session. The chat feedback (status bubble +
  // spinner) comes back over SSE via the `session_resumed` event, so this
  // handler only fires the request and surfaces failures.
  const handleResume = useCallback(
    async (mode: 'resume' | 'retry') => {
      if (!activeSessionId) return;
      try {
        await api.resumeSession(activeSessionId, mode);
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to ${mode}: ${e.message}` });
      }
    },
    [activeSessionId, addItem],
  );

  // Tasks card — user-side CRUD. Optimistic on the wire isn't needed:
  // the backend publishes task_created/task_updated/task_deleted SSE
  // for both REST and skill paths, and the SSE handler upserts by id.
  const handleTaskCreate = useCallback(
    async (body: TaskCreatePayload) => {
      if (!activeSessionId) return;
      try {
        await api.createTask(activeSessionId, body);
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to create task: ${e.message}` });
      }
    },
    [activeSessionId, addItem],
  );

  const handleTaskUpdate = useCallback(
    async (id: number, body: TaskUpdatePayload) => {
      if (!activeSessionId) return;
      try {
        await api.updateTask(activeSessionId, id, body);
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to update task: ${e.message}` });
      }
    },
    [activeSessionId, addItem],
  );

  const handleTaskDelete = useCallback(
    async (id: number) => {
      if (!activeSessionId) return;
      try {
        await api.deleteTask(activeSessionId, id);
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to delete task: ${e.message}` });
      }
    },
    [activeSessionId, addItem],
  );

  const handleSend = async () => {
    // If there are attached files, use the attach-and-send flow
    if (attachedFiles.length > 0) {
      await handleAttachAndSend();
      return;
    }

    if (isDraftEmpty(draft)) return;
    const { content, mentions } = draftToWire(draft);
    setDraft([]);

    // If no active experiment, we need to create one (and a project if needed).
    if (!activeExperimentId || !activeSessionId) {
      try {
        let projectId = activeProjectId;
        let expId: string | null = null;
        let sesId: string | null = null;

        if (!projectId) {
          // Bootstrap: create a project (which auto-creates an initial experiment + session)
          const created = await api.createProject();
          projectId = created.project.id;
          expId = created.experiment.id;
          sesId = created.session_id;
        } else {
          // Have a project but no experiment — quick-create one inside it.
          const created = await api.quickCreate(projectId, undefined, draftToPlainText(draft));
          expId = created.id;
          sesId = created.session_id;
        }

        await refreshProjects();
        await refreshExperiments();
        setActiveProject(projectId);
        setActiveExperiment(expId, sesId);
        // Queue the message to be sent once SSE connects
        pendingMessageRef.current = { content, mentions };
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to create: ${e.message}` });
      }
      return;
    }

    addItem({
      type: 'user',
      content,
      meta: mentions.length > 0 ? { mentions } : undefined,
    });
    setIsRunning(true);

    try {
      await api.sendMessage(
        activeSessionId,
        content,
        true,
        agentModelsRef.current,
        mentions,
        agentThinkingRef.current,
        approvalsEnabledRef.current,
      );
    } catch (e: any) {
      addItem({ type: 'error', content: e.message });
      setIsRunning(false);
    }
  };

  // ── File attachment handlers ──

  const handleFilesSelected = useCallback((files: FileList | File[]) => {
    setAttachedFiles((prev) => [...prev, ...Array.from(files)]);
    setShowAttachMenu(false);
  }, []);

  const removeAttachedFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleAttachAndSend = useCallback(async () => {
    if (attachedFiles.length === 0 && isDraftEmpty(draft)) return;

    const filesToSend = [...attachedFiles];
    const { content: textToSend, mentions: draftMentions } = draftToWire(draft);
    const fileNames = filesToSend.map((f) => f.name);
    setAttachedFiles([]);
    setDraft([]);

    const expId = activeExperimentId;
    const sesId = activeSessionId;

    // Auto-create project/experiment if needed; queue the attachment so it runs
    // after the load effect has finished resetting state and SSE has connected.
    if (!expId || !sesId) {
      setAttachingFiles(true);
      try {
        let projectId = activeProjectId;
        let createdExpId: string | null = null;
        let createdSesId: string | null = null;
        if (!projectId) {
          const created = await api.createProject();
          projectId = created.project.id;
          createdExpId = created.experiment.id;
          createdSesId = created.session_id;
        } else {
          const created = await api.quickCreate(projectId);
          createdExpId = created.id;
          createdSesId = created.session_id;
        }
        await refreshProjects();
        await refreshExperiments();
        pendingAttachmentRef.current = {
          files: filesToSend,
          text: textToSend,
          fileNames,
        };
        setActiveProject(projectId);
        setActiveExperiment(createdExpId, createdSesId);
      } catch (e: any) {
        addItem({ type: 'error', content: e.message });
        setAttachingFiles(false);
      }
      return;
    }

    setAttachingFiles(true);
    try {
      addItem({
        type: 'user',
        content: textToSend,
        meta: {
          files: fileNames,
          ...(draftMentions.length > 0 ? { mentions: draftMentions } : {}),
        },
      });

      if (filesToSend.length > 0) {
        await api.attachData(expId, filesToSend, undefined, sesId);
        await refreshExperiments();
      }

      const agentPrompt = textToSend
        ? textToSend
        : `I've attached ${fileNames.length} file${fileNames.length > 1 ? 's' : ''}: ${fileNames.join(', ')}. What can you tell me about this data?`;
      setIsRunning(true);
      await api.sendMessage(
        sesId,
        agentPrompt,
        true,
        agentModelsRef.current,
        draftMentions,
        agentThinkingRef.current,
        approvalsEnabledRef.current,
      );
    } catch (e: any) {
      addItem({ type: 'error', content: e.message });
    } finally {
      setAttachingFiles(false);
    }
  }, [
    attachedFiles,
    draft,
    activeExperimentId,
    activeSessionId,
    activeProjectId,
    refreshExperiments,
    refreshProjects,
    setActiveExperiment,
    setActiveProject,
    addItem,
    setIsRunning,
  ]);

  const handleS3Select = useCallback(
    async (s3Path: string) => {
      setShowS3Browser(false);
      setAttachingFiles(true);
      try {
        let expId = activeExperimentId;
        let sesId = activeSessionId;
        if (!expId || !sesId) {
          let projectId = activeProjectId;
          if (!projectId) {
            const created = await api.createProject();
            projectId = created.project.id;
            expId = created.experiment.id;
            sesId = created.session_id;
          } else {
            const created = await api.quickCreate(projectId);
            expId = created.id;
            sesId = created.session_id;
          }
          await refreshProjects();
          await refreshExperiments();
          setActiveProject(projectId);
          setActiveExperiment(expId, sesId);
        }
        if (expId) {
          const s3Name = s3Path.split('/').pop() || s3Path;
          addItem({
            type: 'user',
            content: '',
            meta: { files: [s3Name], s3: true },
          });
          await api.attachData(expId, undefined, s3Path, sesId || undefined);
          await refreshExperiments();
          // Trigger agent to look at the data
          if (sesId) {
            setIsRunning(true);
            await api.sendMessage(
              sesId,
              `I've attached data from S3: ${s3Path}. What can you tell me about this data?`,
              true,
              agentModelsRef.current,
              undefined,
              agentThinkingRef.current,
              approvalsEnabledRef.current,
            );
          }
        }
      } catch (e: any) {
        addItem({ type: 'error', content: e.message });
      } finally {
        setAttachingFiles(false);
      }
    },
    [
      activeExperimentId,
      activeSessionId,
      activeProjectId,
      refreshExperiments,
      refreshProjects,
      setActiveExperiment,
      setActiveProject,
      addItem,
      setIsRunning,
    ],
  );

  // Close attach menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target as Node)) {
        setShowAttachMenu(false);
      }
    };
    if (showAttachMenu) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showAttachMenu]);

  // Files attached earlier in this session — surfaced at the top of the `@` picker.
  const sessionAttachedFiles = useMemo(() => {
    const seen = new Set<string>();
    const out: { name: string; sandboxPath: string }[] = [];
    for (const it of chatItems) {
      const files = (it.meta?.files as string[] | undefined) ?? [];
      for (const name of files) {
        if (seen.has(name)) continue;
        seen.add(name);
        out.push({
          name,
          sandboxPath: activeSessionId ? `/sessions/${activeSessionId}/${name}` : name,
        });
      }
    }
    return out;
  }, [chatItems, activeSessionId]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const hasActiveSession = !!activeExperimentId && !!activeSessionId;
  const hasUserMessage = chatItems.some((i) => i.type === 'user');
  // Welcome screen is the default whenever the chat has no user turn yet —
  // including inside a freshly-created session that only has a seeded intro.
  // The chat view is gated on hasActiveSession so nested components can rely on
  // non-null experiment/session ids.
  const showWelcome = !loading && (!hasActiveSession || (!hasUserMessage && !isRunning));

  return (
    <div className="h-screen flex bg-black" id="main-content">
      {/* Sidebar */}
      <Sidebar />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-border shrink-0 bg-surface">
          {hasActiveSession && (
            <>
              <div className="w-px h-5 bg-surface-border" />
              <h1 className="text-sm font-semibold text-white truncate">{experimentName}</h1>
            </>
          )}
          <div className="flex-1" />

          {hasActiveSession && <AgentStatusIndicator agents={activeAgents} isRunning={isRunning} />}

          {hasActiveSession && <CostBadge totals={usageTotals} recent={recentUsage} />}

          {hasActiveSession && (
            <>
              {activeProjectId && (
                <button
                  onClick={() => setShowProjectData(true)}
                  className="p-1.5 rounded-lg transition-colors hover:bg-surface-hover text-gray-400 hover:text-emerald-400"
                  title="Project data"
                >
                  <Database className="w-4 h-4" />
                </button>
              )}
              <button
                onClick={() => {
                  openCanvas();
                  window.dispatchEvent(new CustomEvent('trainable:open-metrics-tab'));
                }}
                className={`p-1.5 rounded-lg transition-colors relative ${
                  metricPoints.length > 0
                    ? 'hover:bg-emerald-600/20 text-emerald-400'
                    : 'hover:bg-surface-hover text-gray-400'
                }`}
                title="Metrics"
              >
                <BarChart3 className="w-4 h-4" />
                {metricPoints.length > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-emerald-400" />
                )}
              </button>
              <button
                onClick={() => (canvasOpen ? workspacePanelRef.current?.collapse() : openCanvas())}
                className={`p-1.5 rounded-lg transition-colors ${
                  canvasOpen
                    ? 'bg-primary-600/20 text-primary-400'
                    : 'hover:bg-surface-hover text-gray-400'
                }`}
                title="Toggle workspace"
              >
                <PanelRightOpen className="w-4 h-4" />
              </button>
              <div
                className={`w-2 h-2 rounded-full ${sseConnected ? 'bg-green-500' : 'bg-red-500'}`}
              />
            </>
          )}
        </header>

        {/* Content */}
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-6 h-6 text-gray-500 animate-spin" />
          </div>
        ) : showWelcome ? (
          // -------------------------------------------------------------------
          // Welcome screen — shown whenever the chat has no user turn yet
          // -------------------------------------------------------------------
          <WelcomeScreen
            draft={draft}
            onDraftChange={setDraft}
            onSend={handleSend}
            attachedFiles={attachedFiles}
            onRemoveAttachedFile={removeAttachedFile}
            onClearAttachedFiles={() => setAttachedFiles([])}
            attachingFiles={attachingFiles}
            showAttachMenu={showAttachMenu}
            setShowAttachMenu={setShowAttachMenu}
            attachMenuRef={attachMenuRef}
            fileInputRef={fileInputRef2}
            folderInputRef={folderInputRef}
            onFilesSelected={handleFilesSelected}
            onOpenS3Browser={() => setShowS3Browser(true)}
            sessionAttachedFiles={sessionAttachedFiles}
          />
        ) : (
          // -------------------------------------------------------------------
          // Studio view: chat + workspace
          // -------------------------------------------------------------------
          <PanelGroup
            direction="horizontal"
            className="flex-1 animate-slide-up"
            autoSaveId="trainable-layout-v2"
          >
            {/* Chat panel */}
            <Panel defaultSize={canvasOpen ? 30 : 100} minSize={20}>
              <ChatPane
                canvasOpen={canvasOpen}
                chatItems={chatItems}
                streamingItemIdRef={streamingItemIdRef}
                pinnedToBottomRef={pinnedToBottomRef}
                tasks={tasks}
                onTaskCreate={handleTaskCreate}
                onTaskUpdate={handleTaskUpdate}
                onTaskDelete={handleTaskDelete}
                draft={draft}
                onDraftChange={setDraft}
                onSend={handleSend}
                onStop={handleStop}
                sessionState={sessionState}
                onResume={handleResume}
                approvalsEnabled={approvalsEnabled}
                onToggleApprovals={() => setApprovalsEnabled((v) => !v)}
                attachedFiles={attachedFiles}
                onRemoveAttachedFile={removeAttachedFile}
                onClearAttachedFiles={() => setAttachedFiles([])}
                attachingFiles={attachingFiles}
                showAttachMenu={showAttachMenu}
                setShowAttachMenu={setShowAttachMenu}
                attachMenuRef={attachMenuRef}
                fileInputRef={fileInputRef2}
                folderInputRef={folderInputRef}
                onFilesSelected={handleFilesSelected}
                onOpenS3Browser={() => setShowS3Browser(true)}
                sessionAttachedFiles={sessionAttachedFiles}
              />
            </Panel>

            {/* Resize handle + Workspace sidebar */}
            <PanelResizeHandle
              className={`w-1.5 transition-colors relative group flex items-center justify-center ${canvasOpen ? 'bg-surface-border hover:bg-primary-500/50 active:bg-primary-500/70' : 'bg-transparent pointer-events-none'}`}
            >
              {canvasOpen && (
                <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                  <GripVertical className="w-3 h-3 text-gray-400" />
                </div>
              )}
            </PanelResizeHandle>
            <Panel
              ref={workspacePanelRef}
              defaultSize={canvasOpen ? 70 : 0}
              minSize={30}
              collapsible
              collapsedSize={0}
              onCollapse={() => setCanvasOpen(false)}
              onExpand={() => setCanvasOpen(true)}
            >
              {canvasOpen && (
                // Keyed by sessionId so switching sessions also clears any
                // prior crash state, in addition to the panel's own "Try
                // again" button. Agent-authored file content, markdown, and
                // self-contained HTML artifacts all render inside here —
                // without this boundary, a bad one blanks the whole SPA.
                <ErrorBoundary key={activeSessionId} label="the workspace">
                  <WorkspaceCanvas
                    experimentId={activeExperimentId || ''}
                    sessionId={activeSessionId || ''}
                    canvasContent={canvasContent}
                    canvasTitle={canvasTitle}
                    generatedFiles={generatedFiles}
                    fileTree={fileTree}
                    metricPoints={metricPoints}
                    chartConfig={chartConfig}
                    logEvents={logEvents}
                    htmlArtifacts={htmlArtifacts}
                    sessionState={sessionState}
                    onClose={collapseCanvas}
                  />
                </ErrorBoundary>
              )}
            </Panel>
          </PanelGroup>
        )}
      </div>

      {showS3Browser && (
        <S3FileBrowserModal
          isOpen={showS3Browser}
          onClose={() => setShowS3Browser(false)}
          onSelect={handleS3Select}
        />
      )}

      {activeProjectId && (
        <ProjectDataModal
          projectId={activeProjectId}
          projectName={projects.find((p) => p.id === activeProjectId)?.name ?? ''}
          isOpen={showProjectData}
          onClose={() => setShowProjectData(false)}
        />
      )}
    </div>
  );
}

// SSEStreamProvider must sit above HomePageContent so `useSSEStream()` (and
// anything nested under it, like the notebook) can reach the same
// publish/subscribe bus that the useSessionStream hook's SSE connection feeds.
export default function HomePage() {
  return (
    <SSEStreamProvider>
      <HomePageContent />
    </SSEStreamProvider>
  );
}
