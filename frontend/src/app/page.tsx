'use client';

import { memo, useEffect, useMemo, useState, useRef, useCallback } from 'react';
import { useApp } from '@/lib/AppContext';
import { api } from '@/lib/api';
import {
  SSEEvent,
  FileTreeNode,
  MetricPoint,
  ChartConfig,
  Mention,
  Draft,
  LineageGraph as LineageGraphPayload,
  LineageNode,
  Task,
  TaskCreatePayload,
  TaskUpdatePayload,
  TaskEventData,
} from '@/lib/types';
import { draftToWire, wireToDraft, isDraftEmpty, draftToPlainText } from '@/lib/mentions';
import {
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from 'react-resizable-panels';
import {
  Bot,
  Send,
  Square,
  Loader2,
  Code2,
  CheckCircle2,
  Terminal,
  AlertCircle,
  FileText,
  X,
  PanelRightOpen,
  FolderOpen,
  Folder,
  Image,
  BarChart3,
  Database,
  Cpu,
  ChevronRight,
  ChevronDown,
  GripVertical,
  Braces,
  Table,
  File as FileIcon,
  ArrowRight,
  Sparkles,
  Plus,
  ArrowUp,
  Users,
  Upload,
  FolderUp,
  HardDrive,
  Paperclip,
  Search,
  ListChecks,
  FileSearch,
  Wrench,
  GitBranch,
} from 'lucide-react';
import Sidebar from '@/components/Sidebar';
import Notebook from '@/components/notebook/Notebook';
import AgentStatusIndicator, { ActiveAgent } from '@/components/AgentStatusIndicator';
import CostBadge, { UsageTotals } from '@/components/CostBadge';
import InlineTasks from '@/components/InlineTasks';
import type { UsageEvent } from '@/lib/types';

const ZERO_USAGE: UsageTotals = {
  cost_usd: 0,
  llm_cost_usd: 0,
  compute_cost_usd: 0,
  input_tokens: 0,
  output_tokens: 0,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
  llm_calls: 0,
  sandbox_seconds: 0,
  compute_runs: 0,
};
import MetricsTab from '@/components/MetricsTab';
import LineageGraph from '@/components/lineage/LineageGraph';
import NodeMetadataPanel from '@/components/lineage/NodeMetadataPanel';
import S3FileBrowserModal from '@/components/S3FileBrowserModal';
import ProjectDataModal from '@/components/ProjectDataModal';
import MentionInput, { MentionInputHandle } from '@/components/MentionInput';
import MentionPill from '@/components/MentionPill';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  buildTreeFromFlatList,
  insertNodeIntoTree,
  unwrapTree,
  countFiles,
  fileBreadcrumb,
  stripSessionPrefix,
} from '@/lib/useFileTree';

SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('json', json);

// ---------------------------------------------------------------------------
// SSE / Backend helpers
// ---------------------------------------------------------------------------

function getSSEBase() {
  if (typeof window === 'undefined') return 'http://localhost:8000';
  return `http://${window.location.hostname}:8000`;
}

function getBackendUrl() {
  if (typeof window === 'undefined') return 'http://localhost:8000';
  return `http://${window.location.hostname}:8000`;
}

// ---------------------------------------------------------------------------
// ChatItem interface
// ---------------------------------------------------------------------------

interface ChatItem {
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
    | 'agent_tool'
    | 'tasks_anchor';
  content: string;
  meta?: any;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// Welcome screen suggestions
// ---------------------------------------------------------------------------

const SUGGESTIONS = [
  {
    icon: BarChart3,
    label: 'Explore a dataset',
    prompt: 'Analyze this dataset \u2014 perform a full EDA.',
  },
  { icon: Cpu, label: 'Train a model', prompt: 'Train a model on this dataset.' },
  {
    icon: Database,
    label: 'Clean & prep data',
    prompt: 'Clean and prepare this dataset for modeling.',
  },
  {
    icon: Terminal,
    label: 'Write a script',
    prompt: 'Write a Python script to process this data.',
  },
];

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function HomePage() {
  const {
    projects,
    experiments,
    activeExperimentId,
    activeSessionId,
    activeProjectId,
    sidebarOpen,
    setSidebarOpen,
    setActiveExperiment,
    setActiveProject,
    refreshExperiments,
    refreshProjects,
    agentModels,
    agentThinking,
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

  // Chat / session state
  const [chatItems, setChatItems] = useState<ChatItem[]>([]);
  const [draft, setDraft] = useState<Draft>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [sessionState, setSessionState] = useState('created');
  const [loading, setLoading] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [experimentName, setExperimentName] = useState('');
  const [tasks, setTasks] = useState<Task[]>([]);

  // Workspace state
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasContent, setCanvasContent] = useState('');
  const [canvasTitle, setCanvasTitle] = useState('Report');
  const [generatedFiles, setGeneratedFiles] = useState<any[]>([]);
  const [fileTree, setFileTree] = useState<FileTreeNode>({
    name: 'workspace',
    path: '/',
    type: 'directory',
    children: [],
  });

  // Metrics state
  const [metricPoints, setMetricPoints] = useState<MetricPoint[]>([]);
  const [chartConfig, setChartConfig] = useState<ChartConfig | null>(null);
  const metricKeysRef = useRef(new Set<string>());

  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const inputRef = useRef<MentionInputHandle | null>(null);
  const prevExperimentIdRef = useRef<string | null>(null);
  const streamingItemIdRef = useRef<string | null>(null);
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

  // Live usage totals for the active session (cost badge in header)
  const [usageTotals, setUsageTotals] = useState<UsageTotals>(ZERO_USAGE);
  const [recentUsage, setRecentUsage] = useState<UsageEvent[]>([]);

  // Active agents tracking (for header indicator)
  const [activeAgents, setActiveAgents] = useState<ActiveAgent[]>([]);
  const activeAgentsRef = useRef<ActiveAgent[]>([]);
  // Keep ref in sync for use inside SSE handler closure
  useEffect(() => {
    activeAgentsRef.current = activeAgents;
  }, [activeAgents]);

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

  // Auto-scroll on new chat items
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatItems]);

  // ---------------------------------------------------------------------------
  // addItem helper
  // ---------------------------------------------------------------------------

  const addItem = useCallback((item: Omit<ChatItem, 'id' | 'timestamp'>) => {
    setChatItems((prev) => [
      ...prev,
      { ...item, id: `${Date.now()}-${Math.random()}`, timestamp: Date.now() },
    ]);
  }, []);

  // ---------------------------------------------------------------------------
  // SSE connection
  // ---------------------------------------------------------------------------

  const connectSSE = useCallback(
    (sid: string) => {
      if (sseRef.current) sseRef.current.close();
      const url = `${getSSEBase()}/api/sessions/${sid}/stream`;
      const source = new EventSource(url);

      source.onopen = () => setSseConnected(true);
      source.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data) as SSEEvent;
          const data = event.data as any;

          switch (event.type) {
            case 'state_change':
              setSessionState(data.state);
              if (data.state.includes('running')) {
                setIsRunning(true);
                // A fresh ROOT-level run is starting. Wipe any leftover
                // sub-agent indicators from previous runs in this session
                // (or from the previous session if the user just switched
                // before the reset propagated). Sub-agents (depth > 0) must
                // NOT trigger this reset, otherwise they'd wipe their own
                // siblings mid-flight.
                const depth = (data.depth as number | undefined) ?? 0;
                if (depth === 0) {
                  setActiveAgents([]);
                  activeAgentsRef.current = [];
                }
              }
              if (
                data.state.includes('done') ||
                data.state === 'failed' ||
                data.state === 'cancelled'
              ) {
                streamingItemIdRef.current = null;
                setIsRunning(false);
                // Clear all active agents when session finishes
                setActiveAgents([]);
              }
              if (data.state.endsWith('_done')) {
                const stageName = data.state.replace('_done', '');
                if (stageName !== 'chat') {
                  addItem({ type: 'stage_complete', content: stageName.toUpperCase() });
                }
              }
              break;
            case 'agent_token':
            case 'agent_message': {
              // Prefer the agent_type carried on the event itself — the
              // backend stamps it via agent_meta in save_and_publish, so it's
              // always the authoritative source for which agent produced the
              // text. Fall back to the activeAgents heuristic only when the
              // event is missing the field (legacy events).
              const eventAgentType = (data.agent_type as string | undefined) || undefined;
              const running = activeAgentsRef.current.filter((a) => a.status === 'running');
              const fallbackType =
                running.length > 0 ? running[running.length - 1].type : undefined;
              const currentAgentType = eventAgentType || fallbackType;
              setChatItems((prev) => {
                const streamingId = streamingItemIdRef.current;
                if (streamingId) {
                  const idx = prev.findIndex((i) => i.id === streamingId && i.type === 'assistant');
                  if (idx >= 0) {
                    const updated = [...prev];
                    updated[idx] = {
                      ...updated[idx],
                      content: updated[idx].content + data.text,
                    };
                    return updated;
                  }
                }
                const newId = `${Date.now()}-${Math.random()}`;
                streamingItemIdRef.current = newId;
                return [
                  ...prev,
                  {
                    id: newId,
                    type: 'assistant',
                    content: data.text,
                    timestamp: Date.now(),
                    meta: currentAgentType ? { agent_type: currentAgentType } : undefined,
                  },
                ];
              });
              break;
            }
            case 'tool_start':
              streamingItemIdRef.current = null;
              addItem({ type: 'tool_start', content: data.tool, meta: data.input });
              break;
            case 'tool_end':
              setChatItems((prev) => {
                const idx = prev.findLastIndex(
                  (i) => i.type === 'tool_start' && i.content === data.tool,
                );
                if (idx >= 0) {
                  const updated = [...prev];
                  const duration = Math.max(
                    1,
                    Math.round((Date.now() - updated[idx].timestamp) / 1000),
                  );
                  updated[idx] = {
                    ...updated[idx],
                    type: 'tool_end',
                    meta: {
                      ...updated[idx].meta,
                      output: data.output,
                      outputs: updated[idx].meta?.outputs || [],
                      duration,
                    },
                  };
                  return updated;
                }
                return [
                  ...prev,
                  {
                    id: `${Date.now()}-${Math.random()}`,
                    type: 'tool_end',
                    content: data.tool,
                    meta: { output: data.output },
                    timestamp: Date.now(),
                  },
                ];
              });
              break;
            case 'code_output':
              setChatItems((prev) => {
                const idx = prev.findLastIndex((i) => i.type === 'tool_start');
                if (idx >= 0) {
                  const updated = [...prev];
                  const outputs = updated[idx].meta?.outputs || [];
                  updated[idx] = {
                    ...updated[idx],
                    meta: {
                      ...updated[idx].meta,
                      outputs: [...outputs, { text: data.text, stream: data.stream }],
                    },
                  };
                  return updated;
                }
                // Fallback: no tool_start found — append a standalone code_output item.
                // Build inline instead of calling addItem() inside the updater (which
                // would nest setChatItems and double-fire under React Strict Mode).
                return [
                  ...prev,
                  {
                    id: `${Date.now()}-${Math.random()}`,
                    type: 'code_output',
                    content: data.text,
                    meta: { stream: data.stream },
                    timestamp: Date.now(),
                  },
                ];
              });
              break;
            case 'agent_error':
              streamingItemIdRef.current = null;
              addItem({ type: 'error', content: data.error });
              setIsRunning(false);
              break;
            case 'usage_event': {
              const ev = data as UsageEvent;
              setRecentUsage((prev) => [...prev.slice(-49), ev]);
              setUsageTotals((prev) => {
                const c = ev.cost_usd || 0;
                const isLlm = ev.kind === 'llm';
                return {
                  cost_usd: prev.cost_usd + c,
                  llm_cost_usd: prev.llm_cost_usd + (isLlm ? c : 0),
                  compute_cost_usd: prev.compute_cost_usd + (isLlm ? 0 : c),
                  input_tokens: prev.input_tokens + (ev.input_tokens || 0),
                  output_tokens: prev.output_tokens + (ev.output_tokens || 0),
                  cache_read_input_tokens:
                    prev.cache_read_input_tokens + (ev.cache_read_input_tokens || 0),
                  cache_creation_input_tokens:
                    prev.cache_creation_input_tokens + (ev.cache_creation_input_tokens || 0),
                  llm_calls: prev.llm_calls + (isLlm ? 1 : 0),
                  sandbox_seconds: prev.sandbox_seconds + (ev.sandbox_seconds || 0),
                  compute_runs: prev.compute_runs + (isLlm ? 0 : 1),
                };
              });
              break;
            }
            case 'report_ready':
              setCanvasContent(data.content);
              setCanvasTitle(`${(data.stage || 'EDA').toUpperCase()} Report`);
              openCanvas();
              break;
            case 'files_ready': {
              const stage = (data.stage as string) || '';
              const newFiles = (data.files || []) as { path: string; type: string }[];
              setGeneratedFiles((prev) => {
                const existingPaths = new Set(prev.map((f: any) => f.path));
                const merged = [...prev];
                for (const f of newFiles) {
                  if (!existingPaths.has(f.path)) merged.push(f);
                }
                return merged;
              });
              setFileTree((prev) => {
                let merged = JSON.parse(JSON.stringify(prev)) as FileTreeNode;
                for (const f of newFiles) {
                  merged = insertNodeIntoTree(
                    merged,
                    { name: f.path.split('/').pop() || '', path: f.path, type: 'file' },
                    `/sessions/${sid}`,
                    stage,
                  );
                }
                return merged;
              });
              break;
            }
            case 'file_created': {
              const stage = (data.stage as string) || '';
              setFileTree((prev) =>
                insertNodeIntoTree(
                  prev,
                  {
                    name: data.name as string,
                    path: data.path as string,
                    type: 'file',
                  },
                  `/sessions/${sid}`,
                  stage,
                ),
              );
              break;
            }
            case 'agent_aborted':
              streamingItemIdRef.current = null;
              addItem({ type: 'status', content: 'Agent stopped' });
              setIsRunning(false);
              break;
            case 'metrics_batch': {
              const items = (data.items || []) as any[];
              const newPoints: MetricPoint[] = [];
              const now = new Date().toISOString();
              for (const m of items) {
                const key = `${m.step}:${m.name}:${m.run_tag || ''}`;
                if (!metricKeysRef.current.has(key)) {
                  metricKeysRef.current.add(key);
                  newPoints.push({
                    step: m.step,
                    name: m.name,
                    value: m.value,
                    stage: m.stage,
                    run_tag: m.run_tag || null,
                    created_at: now,
                  });
                }
              }
              if (newPoints.length > 0) {
                setMetricPoints((prev) => {
                  if (prev.length === 0) openCanvas();
                  return [...prev, ...newPoints];
                });
              }
              break;
            }
            case 'metric': {
              const key = `${data.step}:${data.name}:${data.run_tag || ''}`;
              if (!metricKeysRef.current.has(key)) {
                metricKeysRef.current.add(key);
                setMetricPoints((prev) => {
                  if (prev.length === 0) openCanvas();
                  return [
                    ...prev,
                    {
                      step: data.step as number,
                      name: data.name as string,
                      value: data.value as number,
                      stage: data.stage as string,
                      run_tag: (data.run_tag as string) || null,
                      created_at: new Date().toISOString(),
                    },
                  ];
                });
              }
              break;
            }
            case 'chart_config': {
              const cfg = data as any;
              if (cfg.charts && Array.isArray(cfg.charts)) {
                setChartConfig({ charts: cfg.charts });
              }
              break;
            }
            // Multi-agent events
            case 'subagent_start': {
              const agentId = data.agent_id || `${Date.now()}`;
              addItem({
                type: 'subagent_start',
                content: data.agent_type || 'sub-agent',
                meta: {
                  task: data.task || data.description || '',
                  model: data.model || '',
                  depth: data.depth || 1,
                  agent_id: agentId,
                },
              });
              // Track in active agents for header indicator
              setActiveAgents((prev) => [
                ...prev,
                {
                  id: agentId,
                  type: data.agent_type || 'sub-agent',
                  status: 'running',
                  task: data.task || '',
                  depth: data.depth || 1,
                  startedAt: Date.now(),
                },
              ]);
              break;
            }
            case 'subagent_end': {
              const endAgentId = data.agent_id || '';
              const endAgentType = data.agent_type || 'sub-agent';
              setChatItems((prev) => {
                const idx = prev.findLastIndex(
                  (i) =>
                    i.type === 'subagent_start' &&
                    (i.meta?.agent_id === endAgentId || i.content === endAgentType),
                );
                if (idx >= 0) {
                  const updated = [...prev];
                  const duration = Math.max(
                    1,
                    Math.round((Date.now() - updated[idx].timestamp) / 1000),
                  );
                  updated[idx] = {
                    ...updated[idx],
                    type: 'subagent_end',
                    meta: {
                      ...updated[idx].meta,
                      summary: data.summary || data.result || '',
                      duration,
                    },
                  };
                  return updated;
                }
                return [
                  ...prev,
                  {
                    id: `${Date.now()}-${Math.random()}`,
                    type: 'subagent_end',
                    content: endAgentType,
                    meta: { summary: data.summary || data.result || '', duration: null },
                    timestamp: Date.now(),
                  },
                ];
              });
              // Update agent status in header indicator
              setActiveAgents((prev) =>
                prev.map((a) =>
                  a.id === endAgentId || (a.type === endAgentType && a.status === 'running')
                    ? { ...a, status: data.summary?.startsWith('FAILED') ? 'failed' : 'completed' }
                    : a,
                ),
              );
              break;
            }
            // Inter-agent clarification: parent escalated to user
            case 'clarification_request': {
              addItem({
                type: 'clarification',
                content: data.question || '',
                meta: {
                  question_id: data.question_id,
                  asker_agent_id: data.asker_agent_id,
                  asker_agent_type: data.asker_agent_type,
                  answerer_agent_id: data.answerer_agent_id,
                  why_needed: data.why_needed,
                  urgency: data.urgency,
                  depth: data.depth,
                  status: 'pending',
                  original_question: data.original_question,
                },
              });
              break;
            }
            case 'clarification_resolved': {
              const qid = data.question_id;
              setChatItems((prev) =>
                prev.map((it) =>
                  it.type === 'clarification' && it.meta?.question_id === qid
                    ? {
                        ...it,
                        meta: {
                          ...it.meta,
                          status: 'resolved',
                          answer: data.answer,
                          answered_by: data.answered_by,
                        },
                      }
                    : it,
                ),
              );
              break;
            }
            // Generic auxiliary-tool event (inspect_agent_context, list_session_agents,
            // read_project_session). Single event per call. NO content preview is
            // surfaced — the user only sees that the agent did something.
            case 'agent_tool_call': {
              addItem({
                type: 'agent_tool',
                content: data.tool_name || 'tool',
                meta: {
                  call_id: data.call_id,
                  tool_name: data.tool_name,
                  asker_agent_type: data.asker_agent_type,
                  target_agent_type: data.target_agent_type,
                  answerer_agent_type: data.answerer_agent_type,
                  depth: data.depth || 0,
                  duration_s: data.duration_s,
                  is_error: !!data.is_error,
                  variant: 'tool',
                },
              });
              break;
            }
            // Inter-agent clarification that was answered directly by the
            // parent (no escalation). User sees only the fact that an
            // exchange happened — neither question nor answer text.
            case 'clarification_exchange': {
              addItem({
                type: 'agent_tool',
                content: 'request_clarification',
                meta: {
                  call_id: data.call_id,
                  tool_name: 'request_clarification',
                  asker_agent_type: data.asker_agent_type,
                  answerer_agent_type: data.answerer_agent_type,
                  depth: data.depth || 0,
                  duration_s: data.duration_s,
                  variant: 'clarification_exchange',
                },
              });
              break;
            }
            // Agent created a new notebook — auto-expand workspace + open it
            // so the user watches cells appear live.
            case 'notebook.created': {
              const path = data.notebook_path as string | undefined;
              if (path) {
                openCanvas();
                window.dispatchEvent(new CustomEvent('trainable:open-file', { detail: { path } }));
              }
              break;
            }
            // Lineage events from agent-declared experiment lifecycle.
            // Notify the WorkspaceSidebar so its lineage tab refetches; on
            // experiment_created we also auto-open the canvas + lineage tab
            // so the user sees the new experiment land in real time.
            case 'experiment_created':
            case 'dataset_registered':
            case 'model_registered':
            case 'experiment_state_changed':
            case 'experiments_abandoned': {
              window.dispatchEvent(
                new CustomEvent('trainable:lineage-changed', {
                  detail: { kind: event.type },
                }),
              );
              if (event.type === 'experiment_created') {
                openCanvas();
                window.dispatchEvent(new CustomEvent('trainable:open-lineage-tab'));
              }
              break;
            }
            // Tasks live to-do list — agent calls (add/update) and user
            // REST CRUD both publish these events. Upsert by id; preserve
            // ordering by creation time.
            case 'task_created':
            case 'task_updated': {
              const t = data as TaskEventData;
              setTasks((prev) => {
                const idx = prev.findIndex((x) => x.id === t.id);
                if (idx >= 0) {
                  const next = [...prev];
                  next[idx] = t;
                  return next;
                }
                return [...prev, t];
              });
              // Drop a tasks_anchor into the chat at this point so the
              // user sees the live list inline at the moment the agent
              // touches it. Dedupe consecutive anchors so a burst of
              // back-to-back add/update calls produces ONE card, not N.
              setChatItems((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.type === 'tasks_anchor') return prev;
                return [
                  ...prev,
                  {
                    id: `tasks-${Date.now()}-${Math.random()}`,
                    type: 'tasks_anchor',
                    content: 'tasks',
                    timestamp: Date.now(),
                  },
                ];
              });
              break;
            }
            case 'task_deleted': {
              const id = data.id as number;
              setTasks((prev) => prev.filter((x) => x.id !== id));
              break;
            }
          }
        } catch {
          /* ignore parse errors */
        }
      };
      source.onerror = () => setSseConnected(false);
      sseRef.current = source;
    },
    [addItem, openCanvas],
  );

  // ---------------------------------------------------------------------------
  // Reset state when active experiment changes
  // ---------------------------------------------------------------------------

  const resetSessionState = useCallback(() => {
    setChatItems([]);
    setDraft([]);
    setIsRunning(false);
    streamingItemIdRef.current = null;
    setSessionState('created');
    workspacePanelRef.current?.collapse();
    setCanvasContent('');
    setCanvasTitle('Report');
    setGeneratedFiles([]);
    setFileTree({
      name: 'workspace',
      path: '/',
      type: 'directory',
      children: [],
    });
    setMetricPoints([]);
    setChartConfig(null);
    metricKeysRef.current = new Set();
    setExperimentName('');
    // Critical: clear per-session agent indicators. If we don't, the previous
    // session's running sub-agents leak into the new one and `agent_message`
    // events get mis-tagged with the wrong agent_type (the stale entry from
    // the previous session). Reset both the state AND the ref synchronously
    // so the SSE handler closure sees an empty list immediately.
    setActiveAgents([]);
    activeAgentsRef.current = [];
    setUsageTotals(ZERO_USAGE);
    setRecentUsage([]);
    setTasks([]);
  }, []);

  // ---------------------------------------------------------------------------
  // Load experiment + session when activeExperimentId/activeSessionId change
  // ---------------------------------------------------------------------------

  useEffect(() => {
    // Disconnect previous SSE
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
      setSseConnected(false);
    }

    // If no active experiment, reset and show welcome
    if (!activeExperimentId || !activeSessionId) {
      if (prevExperimentIdRef.current !== null) {
        resetSessionState();
      }
      prevExperimentIdRef.current = activeExperimentId;
      setLoading(false);
      return;
    }

    prevExperimentIdRef.current = activeExperimentId;
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      resetSessionState();

      // Hydrate the CostBadge from the session's historical usage rows.
      // Without this, reopening a session shows 0/0 until the next live
      // usage_event SSE arrives. Fire-and-forget — non-fatal on failure.
      api
        .sessionUsage(activeSessionId!)
        .then((s) => {
          if (cancelled) return;
          const t = s.totals;
          setUsageTotals({
            cost_usd: t.cost_usd || 0,
            llm_cost_usd: t.llm_cost_usd || 0,
            compute_cost_usd: t.compute_cost_usd || 0,
            input_tokens: t.input_tokens || 0,
            output_tokens: t.output_tokens || 0,
            cache_read_input_tokens: t.cache_read_input_tokens || 0,
            cache_creation_input_tokens: t.cache_creation_input_tokens || 0,
            llm_calls: t.llm_calls || 0,
            sandbox_seconds: t.sandbox_seconds || 0,
            compute_runs: t.compute_runs || 0,
          });
          setRecentUsage(s.events ?? []);
        })
        .catch(() => {
          /* historical usage is best-effort; live SSE will fill in */
        });

      try {
        const exp = await api.getExperiment(activeExperimentId);
        if (cancelled) return;
        setExperimentName(exp.name);

        const sid = activeSessionId;
        const sessionData = await api.getSession(sid);
        if (cancelled) return;

        // Reconstruct chat from saved messages
        const restored: ChatItem[] = [];
        let restoredCanvasContent = '';
        let restoredCanvasTitle = 'Report';
        let restoredCanvasOpen = false;
        let restoredFiles: any[] = [];

        if (sessionData.messages?.length > 0) {
          // Events persisted for introspection/telemetry only — never rendered as bubbles.
          const NON_VISIBLE_EVENTS = new Set([
            'agent_thought',
            'file_created',
            'files_ready',
            'metric',
            'metrics_batch',
            'chart_config',
            'validation_result',
            's3_sync_complete',
            'metadata_ready',
          ]);

          for (const msg of sessionData.messages) {
            const eventType = msg.metadata?.event_type as string | undefined;
            if (eventType && NON_VISIBLE_EVENTS.has(eventType)) continue;
            // Legacy seeded intro messages (pre-dated system-prompt injection) — hide.
            if (msg.metadata?.session_intro) continue;
            const mkItem = (item: Omit<ChatItem, 'id' | 'timestamp'>): ChatItem => ({
              ...item,
              id: `${msg.id || Date.now()}-${Math.random()}`,
              timestamp: Date.now(),
            });

            if (eventType === 'tool_start') {
              restored.push(
                mkItem({
                  type: 'tool_start',
                  content: (msg.metadata?.tool as string) || 'execute_code',
                  meta: msg.metadata?.input as Record<string, unknown>,
                }),
              );
            } else if (eventType === 'tool_end') {
              const idx = restored.findLastIndex((i) => i.type === 'tool_start');
              if (idx >= 0) {
                restored[idx] = {
                  ...restored[idx],
                  type: 'tool_end',
                  meta: {
                    ...restored[idx].meta,
                    output: msg.metadata?.output,
                    duration: msg.metadata?.duration || null,
                  },
                };
              } else {
                restored.push(
                  mkItem({
                    type: 'tool_end',
                    content: (msg.metadata?.tool as string) || 'execute_code',
                    meta: { output: msg.metadata?.output as string },
                  }),
                );
              }
            } else if (eventType === 'code_output') {
              const idx = restored.findLastIndex(
                (i) => i.type === 'tool_start' || i.type === 'tool_end',
              );
              if (idx >= 0) {
                const outputs = restored[idx].meta?.outputs || [];
                restored[idx] = {
                  ...restored[idx],
                  meta: {
                    ...restored[idx].meta,
                    outputs: [
                      ...outputs,
                      { text: msg.content || msg.metadata?.text, stream: msg.metadata?.stream },
                    ],
                  },
                };
              }
            } else if (eventType === 'agent_message') {
              restored.push(mkItem({ type: 'assistant', content: msg.content }));
            } else if (eventType === 'report_ready') {
              restoredCanvasContent += msg.content + '\n';
              restoredCanvasTitle = `${((msg.metadata?.stage as string) || 'EDA').toUpperCase()} Report`;
              restoredCanvasOpen = true;
            } else if (eventType === 'files_ready') {
              const stageHint = (msg.metadata?.stage as string) || '';
              const newFiles = (msg.metadata?.files || []) as Array<{
                path: string;
                _stage?: string;
              }>;
              const existingPaths = new Set(restoredFiles.map((f: { path: string }) => f.path));
              for (const f of newFiles) {
                if (!existingPaths.has(f.path)) {
                  restoredFiles.push({ ...f, _stage: stageHint });
                }
              }
            } else if (eventType === 'state_change') {
              const st = msg.metadata?.state as string;
              if (st?.endsWith('_done')) {
                const stageName = st.replace('_done', '');
                if (stageName !== 'chat') {
                  restored.push(
                    mkItem({ type: 'stage_complete', content: stageName.toUpperCase() }),
                  );
                }
              }
            } else if (eventType === 'agent_error') {
              restored.push(
                mkItem({
                  type: 'error',
                  content: (msg.metadata?.error as string) || msg.content,
                }),
              );
            } else if (eventType === 'subagent_start') {
              restored.push(
                mkItem({
                  type: 'subagent_start',
                  content: (msg.metadata?.agent_type as string) || 'sub-agent',
                  meta: {
                    task: msg.metadata?.task || msg.metadata?.description || '',
                    model: msg.metadata?.model || '',
                    depth: msg.metadata?.depth || 1,
                    agent_id: msg.metadata?.agent_id || '',
                  },
                }),
              );
            } else if (eventType === 'subagent_end') {
              const idx = restored.findLastIndex((i) => i.type === 'subagent_start');
              if (idx >= 0) {
                restored[idx] = {
                  ...restored[idx],
                  type: 'subagent_end',
                  meta: {
                    ...restored[idx].meta,
                    summary: msg.metadata?.summary || msg.metadata?.result || '',
                    duration: msg.metadata?.duration || null,
                  },
                };
              } else {
                restored.push(
                  mkItem({
                    type: 'subagent_end',
                    content: (msg.metadata?.agent_type as string) || 'sub-agent',
                    meta: {
                      summary: msg.metadata?.summary || msg.metadata?.result || '',
                      duration: msg.metadata?.duration || null,
                    },
                  }),
                );
              }
            } else if (eventType === 'agent_tool_call') {
              restored.push(
                mkItem({
                  type: 'agent_tool',
                  content: (msg.metadata?.tool_name as string) || 'tool',
                  meta: {
                    call_id: msg.metadata?.call_id,
                    tool_name: msg.metadata?.tool_name,
                    asker_agent_type: msg.metadata?.asker_agent_type,
                    target_agent_type: msg.metadata?.target_agent_type,
                    answerer_agent_type: msg.metadata?.answerer_agent_type,
                    depth: msg.metadata?.depth || 0,
                    duration_s: msg.metadata?.duration_s,
                    is_error: !!msg.metadata?.is_error,
                    variant: 'tool',
                  },
                }),
              );
            } else if (eventType === 'clarification_exchange') {
              restored.push(
                mkItem({
                  type: 'agent_tool',
                  content: 'request_clarification',
                  meta: {
                    call_id: msg.metadata?.call_id,
                    tool_name: 'request_clarification',
                    asker_agent_type: msg.metadata?.asker_agent_type,
                    answerer_agent_type: msg.metadata?.answerer_agent_type,
                    depth: msg.metadata?.depth || 0,
                    duration_s: msg.metadata?.duration_s,
                    variant: 'clarification_exchange',
                  },
                }),
              );
            } else if (eventType === 'clarification_q' || eventType === 'clarification_a') {
              // Persisted under their respective agent_ids and recoverable via
              // inspect_agent_context. Don't render as chat bubbles — the
              // agent_tool_call / clarification_exchange events are the UI surface.
              continue;
            } else if (msg.role === 'user') {
              if (msg.metadata?.event_type === 'file_attached') {
                // Show file attachment as a user bubble with file chips
                const attachedFileNames = (msg.metadata?.files as string[]) || [];
                restored.push(
                  mkItem({
                    type: 'user',
                    content: '',
                    meta: { files: attachedFileNames, hidden: true },
                  }),
                );
                continue;
              }
              const mentions = (msg.metadata?.mentions as Mention[] | undefined) ?? undefined;
              restored.push(
                mkItem({
                  type: 'user',
                  content: msg.content,
                  meta: mentions && mentions.length > 0 ? { mentions } : undefined,
                }),
              );
            } else if (msg.role === 'assistant') {
              restored.push(mkItem({ type: 'assistant', content: msg.content }));
            }
          }
        }

        if (cancelled) return;

        // Convert orphaned tool_start to tool_end
        for (let i = 0; i < restored.length; i++) {
          if (restored[i].type === 'tool_start') {
            restored[i] = { ...restored[i], type: 'tool_end' };
          }
        }

        // Orphaned subagent_start events (no matching subagent_end in the DB)
        // mean that sub-agent was still running when the user navigated away.
        // We keep them as in-flight entries in `activeAgents` so the header
        // pulse comes back, but still flip the chat bubble to a completed
        // state — we don't have the mid-run text yet and the SSE reconnection
        // below will pick up subsequent events.
        const inFlightSubAgents: ActiveAgent[] = [];
        for (let i = 0; i < restored.length; i++) {
          if (restored[i].type === 'subagent_start') {
            const it = restored[i];
            inFlightSubAgents.push({
              id: (it.meta?.agent_id as string) || `${it.id}`,
              type: (it.content as string) || 'sub-agent',
              status: 'running',
              task: (it.meta?.task as string) || '',
              depth: (it.meta?.depth as number) || 1,
              startedAt: it.timestamp,
            });
            restored[i] = { ...it, type: 'subagent_end' };
          }
        }

        setChatItems(restored);
        setCanvasContent(restoredCanvasContent);
        setCanvasTitle(restoredCanvasTitle);
        if (restoredCanvasOpen) {
          // Delay expand to next tick so panel ref is mounted
          setTimeout(() => openCanvas(), 0);
        }
        setGeneratedFiles(restoredFiles);

        // Build file tree from restored files
        if (restoredFiles.length > 0) {
          setFileTree(buildTreeFromFlatList(restoredFiles, `/sessions/${sid}`));
        }
        // Fetch live tree from volume
        api
          .getFileTree(sid)
          .then((tree) => {
            if (!cancelled) setFileTree(unwrapTree(tree));
          })
          .catch((e) => console.error('Failed to load file tree', e));

        // Load historical metrics
        api
          .getMetrics(sid)
          .then((metrics) => {
            if (!cancelled && metrics.length > 0) {
              setMetricPoints(metrics);
              openCanvas();
              for (const m of metrics) {
                metricKeysRef.current.add(`${m.step}:${m.name}:${m.run_tag || ''}`);
              }
            }
          })
          .catch((e) => console.error('Failed to load historical metrics', e));

        // Load existing tasks for this session. If any are present,
        // append a tasks_anchor at the bottom of the restored chat so
        // the user sees the live card on reload — anchors aren't
        // persisted in the message log, so without this the card
        // would only appear on the next live add/update.
        api
          .getTasks(sid)
          .then((rows) => {
            if (cancelled) return;
            setTasks(rows);
            if (rows.length > 0) {
              setChatItems((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.type === 'tasks_anchor') return prev;
                return [
                  ...prev,
                  {
                    id: `tasks-restore-${Date.now()}`,
                    type: 'tasks_anchor',
                    content: 'tasks',
                    timestamp: Date.now(),
                  },
                ];
              });
            }
          })
          .catch((e) => console.error('Failed to load tasks', e));

        // Set running state from session. `state` in the DB is only a stage
        // marker (e.g. "eda_done"), so the definitive "is this session still
        // working right now?" answer comes from the backend's in-memory task
        // registry, exposed as `is_running` on the session payload.
        if (sessionData.state) setSessionState(sessionData.state);
        const stillRunning =
          sessionData.is_running === true || (sessionData.state?.includes('running') ?? false);
        if (stillRunning) {
          setIsRunning(true);
          if (inFlightSubAgents.length > 0) setActiveAgents(inFlightSubAgents);
        }

        connectSSE(sid);
      } catch {
        if (!cancelled) addItem({ type: 'error', content: 'Failed to load experiment' });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
      sseRef.current?.close();
    };
  }, [activeExperimentId, activeSessionId, connectSSE, addItem, resetSessionState, openCanvas]);

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
        )
        .catch((e: any) => {
          addItem({ type: 'error', content: e.message });
          setIsRunning(false);
        });
    }
  }, [activeSessionId, sseConnected, addItem]);

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
        );
      } catch (e: any) {
        addItem({ type: 'error', content: e.message });
        setIsRunning(false);
      } finally {
        setAttachingFiles(false);
      }
    })();
  }, [activeExperimentId, activeSessionId, sseConnected, addItem, refreshExperiments]);

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

  // Handle welcome-screen suggestion click
  const handleSuggestion = (prompt: string) => {
    setDraft([{ kind: 'text', value: prompt }]);
    inputRef.current?.focus();
  };

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
          <div className="flex-1 flex flex-col items-center justify-center px-4 animate-fade-in">
            <div className="w-full max-w-2xl space-y-8">
              {/* Logo + title */}
              <div className="text-center space-y-3">
                <div className="flex items-center justify-center gap-3">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src="/logo-brain-transparent.png" alt="Trainable" className="h-10 w-auto" />
                </div>
                <h1 className="text-2xl font-semibold text-white">
                  What would you like to explore?
                </h1>
                <p className="text-sm text-gray-500">
                  Upload a dataset or describe what you want to build. Trainable will handle the
                  rest.
                </p>
              </div>

              {/* File previews */}
              {attachedFiles.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {attachedFiles.map((f, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/[0.06] border border-white/[0.08] text-xs text-gray-300"
                    >
                      <Paperclip className="w-3 h-3 text-gray-500" />
                      <span className="truncate max-w-[160px]">{f.name}</span>
                      <button
                        onClick={() => removeAttachedFile(i)}
                        title="Remove file"
                        className="p-0.5 hover:bg-white/[0.1] rounded transition-colors"
                      >
                        <X className="w-3 h-3 text-gray-500" />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Input bar */}
              <div className="relative">
                <div className="flex items-center gap-2 bg-[#1e1f22] rounded-2xl px-3 py-3 transition-colors">
                  {/* Attach button */}
                  <div className="relative" ref={attachMenuRef}>
                    <button
                      onClick={() => setShowAttachMenu(!showAttachMenu)}
                      className={`p-1.5 rounded-xl transition-colors shrink-0 ${
                        showAttachMenu
                          ? 'bg-white/[0.1] text-white'
                          : 'hover:bg-white/[0.08] text-gray-400 hover:text-gray-300'
                      }`}
                      title="Attach files or data"
                    >
                      <Plus className="w-5 h-5" />
                    </button>
                    {showAttachMenu && (
                      <div className="absolute bottom-full left-0 mb-2 w-52 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
                        <input
                          ref={fileInputRef2}
                          type="file"
                          multiple
                          className="hidden"
                          onChange={(e) => e.target.files && handleFilesSelected(e.target.files)}
                        />
                        <input
                          ref={folderInputRef}
                          type="file"
                          // @ts-ignore
                          webkitdirectory=""
                          directory=""
                          multiple
                          className="hidden"
                          onChange={(e) => e.target.files && handleFilesSelected(e.target.files)}
                        />
                        <button
                          onClick={() => fileInputRef2.current?.click()}
                          title="Upload files from your computer"
                          className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                        >
                          <Upload className="w-4 h-4 text-gray-500" />
                          Upload files
                        </button>
                        <button
                          onClick={() => folderInputRef.current?.click()}
                          title="Upload an entire folder"
                          className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                        >
                          <FolderUp className="w-4 h-4 text-gray-500" />
                          Upload folder
                        </button>
                        <div className="border-t border-white/[0.06]" />
                        <button
                          onClick={() => {
                            setShowAttachMenu(false);
                            setShowS3Browser(true);
                          }}
                          title="Browse existing S3 datasets"
                          className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                        >
                          <HardDrive className="w-4 h-4 text-gray-500" />
                          Browse S3 data
                        </button>
                      </div>
                    )}
                  </div>

                  <MentionInput
                    ref={inputRef}
                    draft={draft}
                    onChange={setDraft}
                    onSubmit={handleSend}
                    placeholder="Describe your task, ask a question, or upload data..."
                    className="flex-1 py-1"
                    projectId={activeProjectId}
                    experiments={experiments}
                    attachedFilesInSession={sessionAttachedFiles}
                  />
                  <button
                    onClick={handleSend}
                    disabled={isDraftEmpty(draft) && attachedFiles.length === 0}
                    title="Send message"
                    className="p-2 bg-primary-600 hover:bg-primary-700 disabled:opacity-30 rounded-xl transition-colors shrink-0"
                  >
                    {attachingFiles ? (
                      <Loader2 className="w-4 h-4 text-white animate-spin" />
                    ) : (
                      <ArrowUp className="w-4 h-4 text-white" />
                    )}
                  </button>
                </div>
              </div>

              {/* Suggestion chips */}
              <div className="grid grid-cols-2 gap-3">
                {SUGGESTIONS.map((s, i) => {
                  const SIcon = s.icon;
                  return (
                    <button
                      key={i}
                      onClick={() => handleSuggestion(s.prompt)}
                      title={s.prompt}
                      className="flex items-center gap-3 px-4 py-3 rounded-xl bg-surface-elevated border border-surface-border hover:border-gray-600 hover:bg-surface-hover transition-all text-left group"
                    >
                      <SIcon className="w-5 h-5 text-gray-500 group-hover:text-primary-400 transition-colors shrink-0" />
                      <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
                        {s.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
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
              <div className="h-full flex flex-col min-w-0">
                <div className="flex-1 overflow-y-auto px-4 py-4">
                  <div
                    className={`mx-auto w-full space-y-4 ${canvasOpen ? 'max-w-3xl' : 'max-w-5xl'}`}
                  >
                    {renderGroupedChatItems(
                      chatItems,
                      streamingItemIdRef.current,
                      activeSessionId,
                      {
                        tasks,
                        onCreate: handleTaskCreate,
                        onUpdate: handleTaskUpdate,
                        onDelete: handleTaskDelete,
                      },
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
                    {/* Attached files preview */}
                    {attachedFiles.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {attachedFiles.map((f, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/[0.06] border border-white/[0.08] text-xs text-gray-300"
                          >
                            <Paperclip className="w-3 h-3 text-gray-500" />
                            <span className="truncate max-w-[140px]">{f.name}</span>
                            <button
                              onClick={() => removeAttachedFile(i)}
                              className="p-0.5 hover:bg-white/[0.1] rounded transition-colors"
                            >
                              <X className="w-3 h-3 text-gray-500" />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}

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
                              ref={fileInputRef2}
                              type="file"
                              multiple
                              className="hidden"
                              onChange={(e) =>
                                e.target.files && handleFilesSelected(e.target.files)
                              }
                            />
                            <input
                              ref={folderInputRef}
                              type="file"
                              // @ts-ignore
                              webkitdirectory=""
                              directory=""
                              multiple
                              className="hidden"
                              onChange={(e) =>
                                e.target.files && handleFilesSelected(e.target.files)
                              }
                            />
                            <button
                              onClick={() => fileInputRef2.current?.click()}
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
                                setShowS3Browser(true);
                              }}
                              className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                            >
                              <HardDrive className="w-4 h-4 text-gray-500" />
                              Browse S3 data
                            </button>
                          </div>
                        )}
                      </div>

                      <MentionInput
                        draft={draft}
                        onChange={setDraft}
                        onSubmit={() =>
                          isRunning && isDraftEmpty(draft) && attachedFiles.length === 0
                            ? handleStop()
                            : handleSend()
                        }
                        placeholder="Ask anything"
                        className="flex-1 py-1.5"
                        projectId={activeProjectId}
                        experiments={experiments}
                        attachedFilesInSession={sessionAttachedFiles}
                      />
                      {isRunning && isDraftEmpty(draft) && attachedFiles.length === 0 ? (
                        <button
                          onClick={handleStop}
                          className="p-2 bg-red-600 hover:bg-red-700 rounded-xl transition-colors shrink-0"
                          title="Stop agent"
                        >
                          <Square className="w-4 h-4 text-white" />
                        </button>
                      ) : (
                        <button
                          onClick={handleSend}
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
                <WorkspaceSidebar
                  experimentId={activeExperimentId || ''}
                  sessionId={activeSessionId || ''}
                  canvasContent={canvasContent}
                  canvasTitle={canvasTitle}
                  generatedFiles={generatedFiles}
                  fileTree={fileTree}
                  metricPoints={metricPoints}
                  chartConfig={chartConfig}
                  sessionState={sessionState}
                  onClose={() => workspacePanelRef.current?.collapse()}
                />
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

// ---------------------------------------------------------------------------
// File icon helper
// ---------------------------------------------------------------------------

function getFileIconInfo(name: string): { icon: typeof FileText; color: string } {
  if (name.endsWith('.py')) return { icon: Code2, color: 'text-yellow-400' };
  if (name.endsWith('.md')) return { icon: FileText, color: 'text-blue-400' };
  if (/\.(png|jpg|jpeg|svg|gif)$/i.test(name)) return { icon: Image, color: 'text-purple-400' };
  if (name.endsWith('.csv')) return { icon: Table, color: 'text-green-400' };
  if (name.endsWith('.parquet')) return { icon: Database, color: 'text-amber-400' };
  if (name.endsWith('.json')) return { icon: Braces, color: 'text-orange-400' };
  if (name.endsWith('.pkl') || name.endsWith('.joblib'))
    return { icon: Cpu, color: 'text-red-400' };
  return { icon: FileIcon, color: 'text-gray-400' };
}

const DIR_LABELS: Record<string, string> = {
  eda: 'eda',
  prep: 'prep',
  train: 'train',
};

const DIR_COLORS: Record<string, string> = {
  eda: 'text-blue-400',
  prep: 'text-amber-400',
  train: 'text-green-400',
};

// ---------------------------------------------------------------------------
// FileTreeRow -- recursive, github.dev style
// ---------------------------------------------------------------------------

function FileTreeRow({
  node,
  depth,
  expandedDirs,
  toggleDir,
  selectedFile,
  onSelectFile,
}: {
  node: FileTreeNode;
  depth: number;
  expandedDirs: Set<string>;
  toggleDir: (path: string) => void;
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
}) {
  const isDir = node.type === 'directory';
  const isExpanded = expandedDirs.has(node.path);
  const isSelected = !isDir && selectedFile === node.path;
  const pl = 12 + depth * 16;

  if (isDir) {
    const color = DIR_COLORS[node.name] || 'text-gray-400';
    return (
      <>
        <button
          onClick={() => toggleDir(node.path)}
          className="w-full flex items-center gap-1.5 h-[26px] text-[13px] transition-colors hover:bg-white/[0.04] text-gray-300 group"
          style={{ paddingLeft: `${pl}px`, paddingRight: '10px' }}
        >
          <ChevronRight
            className={`w-3 h-3 shrink-0 text-gray-500 transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}`}
          />
          {isExpanded ? (
            <FolderOpen className={`w-4 h-4 shrink-0 ${color}`} />
          ) : (
            <Folder className={`w-4 h-4 shrink-0 ${color}`} />
          )}
          <span className="flex-1 text-left truncate">{DIR_LABELS[node.name] || node.name}</span>
        </button>
        {isExpanded &&
          node.children &&
          node.children.map((child) => (
            <FileTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedDirs={expandedDirs}
              toggleDir={toggleDir}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
            />
          ))}
      </>
    );
  }

  const { icon: FIcon, color } = getFileIconInfo(node.name);
  return (
    <button
      onClick={() => onSelectFile(node.path)}
      className={`w-full flex items-center gap-1.5 h-[26px] text-[13px] transition-colors ${
        isSelected
          ? 'bg-primary-500/10 text-primary-300'
          : 'text-gray-400 hover:bg-white/[0.04] hover:text-gray-200'
      }`}
      style={{ paddingLeft: `${pl + 15}px`, paddingRight: '10px' }}
    >
      <FIcon className={`w-4 h-4 shrink-0 ${color}`} />
      <span className="flex-1 text-left truncate">{node.name}</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// FileViewer -- displays file content based on type
// ---------------------------------------------------------------------------

const FileViewer = memo(function FileViewer({
  filePath,
  sessionId,
}: {
  filePath: string;
  sessionId: string;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fileName = filePath.split('/').pop() || '';
  const isImage = /\.(png|jpg|jpeg|svg|gif)$/i.test(fileName);
  const isPdf = fileName.toLowerCase().endsWith('.pdf');
  const isPython = fileName.endsWith('.py');
  const isMarkdown = fileName.endsWith('.md');
  const isJSON = fileName.endsWith('.json');
  const isNotebook = fileName.endsWith('.ipynb');
  const isBinary = /\.(pkl|joblib|parquet|h5|hdf5|pt|pth|onnx)$/i.test(fileName);

  // Notebook files are rendered inline by a dedicated component — skip the
  // regular read-file flow entirely (it would fetch the raw JSON as text).
  const notebookName = useMemo(() => {
    if (!isNotebook) return null;
    const m = filePath.match(/\/notebooks\/([^/]+)\.ipynb$/);
    return m ? m[1] : null;
  }, [filePath, isNotebook]);

  useEffect(() => {
    if (isImage || isPdf || isBinary || isNotebook) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    api
      .readFile(filePath)
      .then((res) => {
        setContent(res.content);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [filePath, isImage, isPdf, isBinary, isNotebook]);

  if (isNotebook && notebookName) {
    return <Notebook sessionId={sessionId} notebookName={notebookName} variant="inline" />;
  }

  return (
    <div className="h-full flex flex-col bg-black">
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="w-5 h-5 text-gray-500 animate-spin" />
          </div>
        ) : error ? (
          <div className="p-4 text-sm text-red-400">{error}</div>
        ) : isImage ? (
          <div className="p-6 flex items-center justify-center bg-black">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(filePath)}`}
              alt={fileName}
              className="max-w-full max-h-[60vh] rounded-lg"
            />
          </div>
        ) : isPdf ? (
          <iframe
            src={`${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(filePath)}#view=FitH`}
            title={fileName}
            className="w-full h-full min-h-[80vh] bg-white border-0"
          />
        ) : isBinary ? (
          <div className="flex flex-col items-center justify-center h-32 text-gray-500">
            <Cpu className="w-8 h-8 mb-2" />
            <p className="text-sm">Binary file</p>
            <p className="text-xs text-gray-600 mt-1">{fileName}</p>
          </div>
        ) : isPython || isJSON ? (
          <SyntaxHighlighter
            language={isPython ? 'python' : 'json'}
            style={oneDark}
            customStyle={{
              margin: 0,
              padding: '16px',
              background: '#0d1117',
              fontSize: '13px',
              lineHeight: '1.6',
            }}
            showLineNumbers
            lineNumberStyle={{
              color: '#3b4048',
              fontSize: '12px',
              paddingRight: '16px',
              minWidth: '2.5em',
            }}
          >
            {content || ''}
          </SyntaxHighlighter>
        ) : isMarkdown ? (
          <div className="p-6 markdown-content">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                img: ({ src, alt }) => {
                  let imgSrc = src || '';
                  if (imgSrc.startsWith('/data/')) {
                    imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(imgSrc)}`;
                  } else if (imgSrc && !imgSrc.startsWith('http')) {
                    const dir = filePath.substring(0, filePath.lastIndexOf('/'));
                    imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(dir + '/' + imgSrc)}`;
                  }
                  return (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={imgSrc}
                      alt={alt || ''}
                      className="max-w-full rounded-lg shadow-md my-4"
                    />
                  );
                },
              }}
            >
              {content || ''}
            </ReactMarkdown>
          </div>
        ) : (
          <pre className="p-4 text-[13px] text-gray-300 font-mono whitespace-pre-wrap leading-relaxed">
            {content || ''}
          </pre>
        )}
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// ReportMarkdown -- the canvas report tab body. Memoized so parent renders
// triggered by chat / SSE / task ticks don't re-parse the markdown tree.
// ---------------------------------------------------------------------------

const ReportMarkdown = memo(function ReportMarkdown({
  content,
  sessionId,
}: {
  content: string;
  sessionId: string;
}) {
  // Stable `components` map: only rebuilt when sessionId changes (effectively
  // never within a session). Without useMemo the inline `img` lambda would
  // be a fresh ref each render and ReactMarkdown would re-key its tree.
  const components = useMemo(
    () => ({
      img: ({ src, alt }: { src?: string; alt?: string }) => {
        let imgSrc = src || '';
        if (imgSrc.startsWith('/data/')) {
          imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(imgSrc)}`;
        } else if (imgSrc && !imgSrc.startsWith('http')) {
          const workspace = `/sessions/${sessionId}/eda`;
          imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(workspace + '/' + imgSrc)}`;
        }
        return (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imgSrc} alt={alt || ''} className="max-w-full rounded-lg shadow-md my-4" />
        );
      },
    }),
    [sessionId],
  );
  return (
    <div className="h-full overflow-y-auto p-6 bg-black">
      <div className="markdown-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {content}
        </ReactMarkdown>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Workspace Panel -- github.dev-style: tree sidebar + tabbed editor
// ---------------------------------------------------------------------------

interface OpenTab {
  id: string;
  label: string;
  icon: typeof FileText;
  iconColor: string;
  type: 'file' | 'report' | 'metrics' | 'lineage';
}

const REPORT_TAB_ID = '__report__';
const METRICS_TAB_ID = '__metrics__';
const LINEAGE_TAB_ID = '__lineage__';

function WorkspaceSidebar({
  experimentId,
  sessionId,
  canvasContent,
  canvasTitle,
  generatedFiles,
  fileTree,
  metricPoints,
  chartConfig,
  sessionState,
  onClose,
}: {
  experimentId: string;
  sessionId: string;
  canvasContent: string;
  canvasTitle: string;
  generatedFiles: any[];
  fileTree: FileTreeNode;
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  sessionState: string;
  onClose: () => void;
}) {
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  // MRU list of tab ids that are currently mounted in the DOM. Capped to
  // MAX_MOUNTED_TABS so opening a long parade of files doesn't pin every
  // FileViewer in memory. The active tab is always at the head and
  // therefore never evicted.
  const [mountedTabIds, setMountedTabIds] = useState<string[]>([]);

  useEffect(() => {
    const MAX_MOUNTED_TABS = 8;
    setMountedTabIds((prev) => {
      const stillOpen = new Set(openTabs.map((t) => t.id));
      const carried = prev.filter((id) => stillOpen.has(id));
      const next =
        activeTabId && stillOpen.has(activeTabId)
          ? [activeTabId, ...carried.filter((id) => id !== activeTabId)]
          : carried;
      return next.slice(0, MAX_MOUNTED_TABS);
    });
  }, [activeTabId, openTabs]);

  // Lineage tab state — fetched lazily once the user opens the tab (or
  // an SSE event auto-opens it). Re-fetched on lineage-changed events.
  const [lineageData, setLineageData] = useState<LineageGraphPayload | null>(null);
  const [lineageLoading, setLineageLoading] = useState(false);
  const [lineageNode, setLineageNode] = useState<LineageNode | null>(null);

  // Stable handlers so memoized children (LineageGraph, NodeMetadataPanel)
  // don't bail out of memo every time the parent re-renders.
  const handleLineageNodeClick = useCallback((n: LineageNode) => setLineageNode(n), []);
  const handleLineageNodeClose = useCallback(() => setLineageNode(null), []);

  // Listen for "open metrics tab" event from header button
  useEffect(() => {
    const handler = () => {
      setOpenTabs((prev) => {
        if (prev.find((t) => t.id === METRICS_TAB_ID)) return prev;
        return [
          ...prev,
          {
            id: METRICS_TAB_ID,
            label: 'Metrics',
            icon: BarChart3,
            iconColor: 'text-emerald-400',
            type: 'metrics',
          },
        ];
      });
      setActiveTabId(METRICS_TAB_ID);
    };
    window.addEventListener('trainable:open-metrics-tab', handler);
    return () => window.removeEventListener('trainable:open-metrics-tab', handler);
  }, []);

  // Listen for "open lineage tab" event (dispatched on experiment_created
  // SSE) so the user lands on the new graph without manually clicking.
  useEffect(() => {
    const handler = () => {
      setOpenTabs((prev) => {
        if (prev.find((t) => t.id === LINEAGE_TAB_ID)) return prev;
        return [
          ...prev,
          {
            id: LINEAGE_TAB_ID,
            label: 'Lineage',
            icon: GitBranch,
            iconColor: 'text-violet-400',
            type: 'lineage',
          },
        ];
      });
      setActiveTabId(LINEAGE_TAB_ID);
    };
    window.addEventListener('trainable:open-lineage-tab', handler);
    return () => window.removeEventListener('trainable:open-lineage-tab', handler);
  }, []);

  // Refetch lineage on session change or when an SSE event signals a
  // change. Keeping the fetch keyed on sessionId so reopening a closed
  // canvas doesn't double-fire.
  const refetchLineage = useCallback(async () => {
    if (!sessionId) {
      setLineageData(null);
      return;
    }
    setLineageLoading(true);
    try {
      const g = await api.sessionLineage(sessionId);
      setLineageData(g);
    } catch (err) {
      console.warn('lineage fetch failed', err);
    } finally {
      setLineageLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    refetchLineage();
  }, [refetchLineage]);

  useEffect(() => {
    const handler = () => refetchLineage();
    window.addEventListener('trainable:lineage-changed', handler);
    return () => window.removeEventListener('trainable:lineage-changed', handler);
  }, [refetchLineage]);

  // Auto-expand directories when tree updates
  useEffect(() => {
    if (fileTree?.children) {
      setExpandedDirs((prev) => {
        const next = new Set(prev);
        for (const child of fileTree.children || []) {
          if (child.type === 'directory') {
            next.add(child.path);
            for (const sub of child.children || []) {
              if (sub.type === 'directory') next.add(sub.path);
            }
          }
        }
        return next;
      });
    }
  }, [fileTree]);

  // Auto-open report tab when report arrives
  useEffect(() => {
    if (canvasContent) {
      setOpenTabs((prev) => {
        if (prev.find((t) => t.id === REPORT_TAB_ID)) {
          return prev;
        }
        return [
          ...prev,
          {
            id: REPORT_TAB_ID,
            label: canvasTitle || 'Report',
            icon: FileText,
            iconColor: 'text-blue-400',
            type: 'report',
          },
        ];
      });
      setActiveTabId((prev) => prev || REPORT_TAB_ID);
    }
  }, [canvasContent, canvasTitle]);

  // Auto-open metrics tab when first metric arrives
  const hasMetrics = metricPoints.length > 0;
  useEffect(() => {
    if (hasMetrics) {
      setOpenTabs((prev) => {
        if (prev.find((t) => t.id === METRICS_TAB_ID)) return prev;
        return [
          ...prev,
          {
            id: METRICS_TAB_ID,
            label: 'Metrics',
            icon: BarChart3,
            iconColor: 'text-emerald-400',
            type: 'metrics',
          },
        ];
      });
      setActiveTabId((prev) => prev || METRICS_TAB_ID);
    }
  }, [hasMetrics]);

  const toggleDir = (path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      next.has(path) ? next.delete(path) : next.add(path);
      return next;
    });
  };

  const openFile = useCallback((filePath: string) => {
    const name = filePath.split('/').pop() || '';
    const { icon, color } = getFileIconInfo(name);
    setActiveTabId(filePath);
    setOpenTabs((prev) => {
      if (prev.find((t) => t.id === filePath)) return prev;
      return [...prev, { id: filePath, label: name, icon, iconColor: color, type: 'file' }];
    });
  }, []);

  // Listen for top-level requests to open a specific workspace file
  // (e.g. an agent creating a notebook → auto-open it).
  useEffect(() => {
    const handler = (e: Event) => {
      const path = (e as CustomEvent).detail?.path as string | undefined;
      if (path) openFile(path);
    };
    window.addEventListener('trainable:open-file', handler as EventListener);
    return () => window.removeEventListener('trainable:open-file', handler as EventListener);
  }, [openFile]);

  // Pick a sensible default tab when the canvas opens with no active tab.
  // Priority: existing report → live metrics → notebook → README/report.md →
  // first browseable data file → first file at all. Skips when the user
  // already has a tab active so reopening the canvas never overrides them.
  const openMetricsTab = useCallback(() => {
    setOpenTabs((prev) => {
      if (prev.find((t) => t.id === METRICS_TAB_ID)) return prev;
      return [
        ...prev,
        {
          id: METRICS_TAB_ID,
          label: 'Metrics',
          icon: BarChart3,
          iconColor: 'text-emerald-400',
          type: 'metrics',
        },
      ];
    });
    setActiveTabId(METRICS_TAB_ID);
  }, []);

  const openReportTab = useCallback(() => {
    setOpenTabs((prev) => {
      if (prev.find((t) => t.id === REPORT_TAB_ID)) return prev;
      return [
        ...prev,
        {
          id: REPORT_TAB_ID,
          label: canvasTitle || 'Report',
          icon: FileText,
          iconColor: 'text-blue-400',
          type: 'report',
        },
      ];
    });
    setActiveTabId(REPORT_TAB_ID);
  }, [canvasTitle]);

  const flattenFilePaths = useCallback((root: FileTreeNode): string[] => {
    const out: string[] = [];
    const walk = (n: FileTreeNode) => {
      if (n.type === 'file' && n.path) out.push(n.path);
      for (const c of n.children || []) walk(c);
    };
    walk(root);
    return out;
  }, []);

  const pickDefaultTab = useCallback((): (() => void) | null => {
    if (canvasContent) return openReportTab;
    if (metricPoints.length > 0) return openMetricsTab;
    const all = flattenFilePaths(fileTree);
    const notebook = all.find((p) => p.endsWith('.ipynb'));
    if (notebook) return () => openFile(notebook);
    const readme = all.find((p) => /\/(readme|report)\.md$/i.test(p));
    if (readme) return () => openFile(readme);
    const data = all.find((p) => /\.(csv|parquet|json|png|jpg|jpeg|svg)$/i.test(p));
    if (data) return () => openFile(data);
    if (all[0]) return () => openFile(all[0]);
    return null;
  }, [
    canvasContent,
    metricPoints.length,
    fileTree,
    flattenFilePaths,
    openFile,
    openMetricsTab,
    openReportTab,
  ]);

  useEffect(() => {
    const handler = () => {
      if (activeTabId) return;
      const action = pickDefaultTab();
      action?.();
    };
    window.addEventListener('trainable:canvas-opened', handler);
    return () => window.removeEventListener('trainable:canvas-opened', handler);
  }, [activeTabId, pickDefaultTab]);

  const closeTab = useCallback((tabId: string) => {
    setOpenTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === tabId);
      const next = prev.filter((t) => t.id !== tabId);
      setActiveTabId((currentId) => {
        if (currentId !== tabId) return currentId;
        if (next.length === 0) return null;
        const neighborIdx = Math.min(idx, next.length - 1);
        return next[neighborIdx].id;
      });
      return next;
    });
  }, []);

  const totalFiles = countFiles(fileTree);
  const hasTree = fileTree.children && fileTree.children.length > 0;
  const activeTab = openTabs.find((t) => t.id === activeTabId);
  const breadcrumb = activeTab?.type === 'file' ? fileBreadcrumb(activeTab.id) : [];

  return (
    <div className="h-full border-l border-surface-border flex flex-row bg-black">
      {/* Left: file tree sidebar */}
      <div className="w-[220px] shrink-0 flex flex-col border-r border-white/[0.06] bg-surface">
        {/* Tree header */}
        <div className="flex items-center justify-between px-3 h-9 border-b border-white/[0.06] shrink-0">
          <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-gray-500 font-semibold">
            Explorer
            {totalFiles > 0 && (
              <span className="px-1 py-0.5 rounded bg-white/[0.06] text-[10px] text-gray-500 normal-case tracking-normal font-normal">
                {totalFiles}
              </span>
            )}
          </div>
          <div className="flex items-center gap-0.5">
            <button
              onClick={() => {
                setOpenTabs((prev) => {
                  if (prev.find((t) => t.id === METRICS_TAB_ID)) return prev;
                  return [
                    ...prev,
                    {
                      id: METRICS_TAB_ID,
                      label: 'Metrics',
                      icon: BarChart3,
                      iconColor: 'text-emerald-400',
                      type: 'metrics',
                    },
                  ];
                });
                setActiveTabId(METRICS_TAB_ID);
              }}
              className="p-1 hover:bg-white/[0.06] rounded transition-colors"
              title="Open Metrics"
            >
              <BarChart3 className="w-3 h-3 text-gray-600" />
            </button>
            <button
              onClick={onClose}
              className="p-1 hover:bg-white/[0.06] rounded transition-colors"
            >
              <X className="w-3 h-3 text-gray-600" />
            </button>
          </div>
        </div>

        {/* Tree */}
        <div className="flex-1 overflow-y-auto">
          {hasTree ? (
            <div className="py-1">
              {fileTree.children!.map((node) => (
                <FileTreeRow
                  key={node.path}
                  node={node}
                  depth={0}
                  expandedDirs={expandedDirs}
                  toggleDir={toggleDir}
                  selectedFile={activeTab?.type === 'file' ? activeTab.id : null}
                  onSelectFile={openFile}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 text-gray-600 px-4">
              <FolderOpen className="w-7 h-7 mb-2 text-gray-700" />
              <p className="text-[11px] text-center">Files will appear here as the agent runs</p>
            </div>
          )}
        </div>
      </div>

      {/* Right: tabbed editor area */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        {/* Tab strip */}
        {openTabs.length > 0 && (
          <div className="flex items-end h-[35px] bg-surface border-b border-white/[0.06] shrink-0 overflow-x-auto">
            {openTabs.map((tab) => {
              const isActive = tab.id === activeTabId;
              const TabIcon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTabId(tab.id)}
                  className={`flex items-center gap-1.5 px-3 h-[34px] text-xs border-r border-white/[0.04] shrink-0 transition-colors ${
                    isActive
                      ? 'bg-black text-gray-200 border-t-2 border-t-primary-500'
                      : 'bg-surface text-gray-500 hover:text-gray-300 border-t-2 border-t-transparent'
                  }`}
                >
                  <TabIcon className={`w-3.5 h-3.5 shrink-0 ${tab.iconColor}`} />
                  <span className="truncate max-w-[120px]">{tab.label}</span>
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`Close ${tab.label} tab`}
                    title="Close tab"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        e.stopPropagation();
                        closeTab(tab.id);
                      }
                    }}
                    className="ml-1 p-0.5 rounded hover:bg-white/[0.1] transition-colors"
                  >
                    <X className="w-3 h-3" />
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {/* Breadcrumb (for file tabs) */}
        {activeTab?.type === 'file' && breadcrumb.length > 0 && (
          <div className="flex items-center gap-1 px-3 h-6 bg-black border-b border-white/[0.04] shrink-0">
            {breadcrumb.map((seg, i) => (
              <span key={i} className="flex items-center gap-1 text-[11px]">
                {i > 0 && <ChevronRight className="w-2.5 h-2.5 text-gray-700" />}
                <span className={i === breadcrumb.length - 1 ? 'text-gray-400' : 'text-gray-600'}>
                  {seg}
                </span>
              </span>
            ))}
          </div>
        )}

        {/* Content area — every opened tab stays mounted so scroll position,
            fetched file content, legend toggles, lineage pan/zoom, and chart
            animations all persist when switching tabs. The MRU mountedTabIds
            list bounds memory by evicting the oldest non-active tab past the
            cap. */}
        <div className="flex-1 overflow-hidden relative">
          {openTabs.map((tab) => {
            if (!mountedTabIds.includes(tab.id)) return null;
            // Report tab can be open but content-less while waiting for an
            // agent to render it — skip the wrapper so the shared empty
            // state below renders instead.
            if (tab.type === 'report' && !canvasContent) return null;
            const isActive = tab.id === activeTabId;
            return (
              <div key={tab.id} className={`h-full ${isActive ? 'block' : 'hidden'}`}>
                {tab.type === 'file' ? (
                  <FileViewer filePath={tab.id} sessionId={sessionId} />
                ) : tab.type === 'report' ? (
                  <ReportMarkdown content={canvasContent || ''} sessionId={sessionId} />
                ) : tab.type === 'metrics' ? (
                  <div className="h-full overflow-hidden bg-black">
                    <MetricsTab
                      metricPoints={metricPoints}
                      chartConfig={chartConfig}
                      state={sessionState}
                    />
                  </div>
                ) : tab.type === 'lineage' ? (
                  <div className="h-full overflow-hidden relative bg-white">
                    <LineageGraph
                      data={lineageData}
                      loading={lineageLoading}
                      height="100%"
                      onNodeClick={handleLineageNodeClick}
                    />
                    {lineageNode ? (
                      <NodeMetadataPanel
                        node={lineageNode}
                        data={lineageData}
                        onClose={handleLineageNodeClose}
                      />
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
          {(!activeTab || (activeTab.type === 'report' && !canvasContent)) && (
            <div className="flex flex-col items-center justify-center h-full text-gray-600 bg-black">
              <Code2 className="w-8 h-8 mb-2 text-gray-700" />
              <p className="text-xs">
                Workspace is empty — files will appear here as the agent works.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fun verbs for tool cards
// ---------------------------------------------------------------------------

const FUN_VERBS = [
  'Schlepping',
  'Noodling',
  'Crunching',
  'Wrangling',
  'Percolating',
  'Tinkering',
  'Brewing',
  'Conjuring',
  'Finagling',
  'Rummaging',
  'Simmering',
  'Whittling',
  'Pondering',
  'Juggling',
  'Untangling',
];

const PAST_VERBS = [
  'Schlepped',
  'Noodled',
  'Crunched',
  'Wrangled',
  'Percolated',
  'Tinkered',
  'Brewed',
  'Conjured',
  'Finagled',
  'Rummaged',
  'Simmered',
  'Whittled',
  'Pondered',
  'Juggled',
  'Untangled',
];

function useFunVerb(isAnimating: boolean) {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * FUN_VERBS.length));
  useEffect(() => {
    if (!isAnimating) return;
    const id = setInterval(() => {
      setIndex((prev) => (prev + 1) % FUN_VERBS.length);
    }, 10000);
    return () => clearInterval(id);
  }, [isAnimating]);
  return FUN_VERBS[index];
}

// ---------------------------------------------------------------------------
// ToolGroupCard -- groups consecutive tool executions into a single card
// ---------------------------------------------------------------------------

function ToolGroupCard({ items }: { items: ChatItem[] }) {
  const [expanded, setExpanded] = useState(false);

  // Check if any tool in the group is still running
  const hasRunning = items.some((i) => i.type === 'tool_start');
  const toolItems = items.filter((i) => i.type === 'tool_start' || i.type === 'tool_end');
  const count = toolItems.length;
  const totalDuration = toolItems.reduce(
    (sum, i) => sum + (i.type === 'tool_end' ? i.meta?.duration || 0 : 0),
    0,
  );

  // If only 1 tool, render it directly without group wrapper
  if (count === 1) {
    return <CollapsibleToolCard item={toolItems[0]} />;
  }

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-amber-500/20">
        <Code2 className="w-3.5 h-3.5 text-amber-400" />
      </div>
      <div className="flex-1 min-w-0 rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border overflow-hidden">
        <button
          type="button"
          aria-expanded={expanded}
          className="w-full flex items-center gap-2 px-4 py-2.5 cursor-pointer select-none text-left"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {hasRunning ? (
            <Loader2 className="w-3.5 h-3.5 text-amber-400 animate-spin" />
          ) : (
            <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
          )}
          <span className="text-sm text-gray-300 flex-1">
            {hasRunning
              ? `Running ${count} steps...`
              : `Ran ${count} steps${totalDuration > 0 ? ` in ${totalDuration}s` : ''}`}
          </span>
          <ChevronRight
            className={`w-3.5 h-3.5 text-gray-500 transition-transform duration-150 ${
              expanded ? 'rotate-90' : ''
            }`}
          />
        </button>
        {expanded && (
          <div className="border-t border-surface-border px-2 py-2 space-y-1">
            {toolItems.map((item) => (
              <CollapsibleToolCard key={item.id} item={item} inline />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CollapsibleToolCard -- amber themed tool execution card
// ---------------------------------------------------------------------------

function CollapsibleToolCard({ item, inline }: { item: ChatItem; inline?: boolean }) {
  const isStart = item.type === 'tool_start';
  const [collapsed, setCollapsed] = useState(true);
  const funVerb = useFunVerb(isStart);
  const [doneLabel] = useState(() => PAST_VERBS[Math.floor(Math.random() * PAST_VERBS.length)]);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!isStart) return;
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - item.timestamp) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [isStart, item.timestamp]);

  const card = (
    <div
      className={`${inline ? '' : 'max-w-[85%] '}rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border overflow-hidden`}
    >
      <button
        type="button"
        aria-expanded={!collapsed}
        className={`w-full flex items-center gap-2 ${inline ? 'px-3 py-1.5' : 'px-4 py-2.5'} cursor-pointer select-none text-left`}
        onClick={() => setCollapsed((prev) => !prev)}
      >
        {isStart ? (
          <Loader2
            className={`${inline ? 'w-3 h-3' : 'w-3.5 h-3.5'} text-amber-400 animate-spin`}
          />
        ) : (
          <CheckCircle2 className={`${inline ? 'w-3 h-3' : 'w-3.5 h-3.5'} text-green-400`} />
        )}
        <span className={`${inline ? 'text-xs' : 'text-sm'} text-gray-300 flex-1`}>
          {isStart
            ? `${funVerb}...${elapsed > 0 ? ` ${elapsed}s` : ''}`
            : `${doneLabel} for ${item.meta?.duration || 1}s`}
        </span>
        <ChevronRight
          className={`w-3.5 h-3.5 text-gray-500 transition-transform duration-150 ${
            !collapsed ? 'rotate-90' : ''
          }`}
        />
      </button>
      {!collapsed && (
        <>
          {item.meta?.code && (
            <pre className="px-4 py-2 text-xs text-gray-400 font-mono max-h-24 overflow-y-auto border-t border-surface-border whitespace-pre-wrap">
              {item.meta.code.length > 300 ? item.meta.code.slice(0, 300) + '...' : item.meta.code}
            </pre>
          )}
          {item.meta?.outputs?.length > 0 && (
            <div className="px-4 py-2 border-t border-surface-border max-h-32 overflow-y-auto">
              {item.meta.outputs.map((o: { text: string; stream: string }, i: number) => (
                <pre
                  key={i}
                  className={`text-xs font-mono whitespace-pre-wrap break-all ${
                    o.stream === 'stderr' ? 'text-red-400/70' : 'text-gray-500'
                  }`}
                >
                  {o.text}
                </pre>
              ))}
            </div>
          )}
          {item.meta?.output && (
            <pre className="px-4 py-2 text-xs text-green-400/80 font-mono max-h-32 overflow-y-auto border-t border-surface-border whitespace-pre-wrap">
              {item.meta.output.length > 500
                ? item.meta.output.slice(0, 500) + '...'
                : item.meta.output}
            </pre>
          )}
        </>
      )}
    </div>
  );

  if (inline) return card;

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-amber-500/20">
        <Code2 className="w-3.5 h-3.5 text-amber-400" />
      </div>
      {card}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SubAgentCard -- teal/cyan themed card for multi-agent sub-agents
// ---------------------------------------------------------------------------

const AGENT_META: Record<string, { label: string; color: string }> = {
  orchestrator: { label: 'Orchestrator', color: 'violet' },
  eda: { label: 'EDA Agent', color: 'blue' },
  data_prep: { label: 'Data Prep Agent', color: 'amber' },
  feature_eng: { label: 'Feature Eng. Agent', color: 'orange' },
  trainer: { label: 'Training Agent', color: 'green' },
  reviewer: { label: 'Review Agent', color: 'rose' },
  chat: { label: 'Chat Agent', color: 'gray' },
};

const AGENT_COLORS: Record<string, { bg: string; text: string; border: string; dot: string }> = {
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

function SubAgentCard({ item }: { item: ChatItem }) {
  const isStart = item.type === 'subagent_start';
  const [collapsed, setCollapsed] = useState(true);
  const [elapsed, setElapsed] = useState(0);

  const agentType = item.content || 'sub-agent';
  const meta = AGENT_META[agentType] || {
    label: agentType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
    color: 'teal',
  };
  const colors = AGENT_COLORS[meta.color] || AGENT_COLORS.teal;
  const modelName = item.meta?.model
    ? item.meta.model.replace('claude-', '').replace(/-/g, ' ')
    : '';

  useEffect(() => {
    if (!isStart) return;
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - item.timestamp) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, [isStart, item.timestamp]);

  return (
    <div className="animate-fade-in my-1">
      <button
        className={`flex items-center gap-2.5 w-full px-3.5 py-2 rounded-xl ${colors.bg} border ${colors.border} hover:brightness-110 transition-all text-left group`}
        onClick={() => setCollapsed((prev) => !prev)}
      >
        {/* Agent icon */}
        <div
          className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${colors.bg}`}
        >
          {isStart ? (
            <Loader2 className={`w-3.5 h-3.5 ${colors.text} animate-spin`} />
          ) : (
            <CheckCircle2 className={`w-3.5 h-3.5 ${colors.text}`} />
          )}
        </div>

        {/* Label + model */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-sm font-medium ${colors.text}`}>{meta.label}</span>
            {modelName && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-white/[0.06] text-gray-500 font-mono">
                {modelName}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 truncate mt-0.5">
            {isStart
              ? `Running...${elapsed > 0 ? ` ${elapsed}s` : ''}`
              : `Completed${item.meta?.duration ? ` in ${item.meta.duration}s` : ''}`}
          </p>
        </div>

        <ChevronRight
          className={`w-3.5 h-3.5 text-gray-600 transition-transform duration-150 shrink-0 ${
            !collapsed ? 'rotate-90' : ''
          }`}
        />
      </button>

      {!collapsed && (
        <div className={`mt-1 ml-4 border-l-2 ${colors.border} pl-3 space-y-1.5`}>
          {item.meta?.task && (
            <div className="text-xs text-gray-400">
              <span className={`${colors.text} font-medium`}>Task: </span>
              {item.meta.task}
            </div>
          )}
          {item.meta?.summary && (
            <div className="text-xs text-gray-400 max-h-48 overflow-y-auto">
              <span className={`${colors.text} font-medium`}>Result: </span>
              <div className="mt-1 markdown-chat">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {item.meta.summary.length > 800
                    ? item.meta.summary.slice(0, 800) + '\n\n...'
                    : item.meta.summary}
                </ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ClarificationCard -- inline reply for sub-agent clarification questions
// ---------------------------------------------------------------------------

function ClarificationCard({ item, sessionId }: { item: ChatItem; sessionId: string | null }) {
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

function AgentToolCard({ item }: { item: ChatItem }) {
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

// ---------------------------------------------------------------------------
// renderGroupedChatItems — groups consecutive tool items together
// ---------------------------------------------------------------------------

function isToolItem(item: ChatItem) {
  return item.type === 'tool_start' || item.type === 'tool_end' || item.type === 'code_output';
}

interface TasksContext {
  tasks: Task[];
  onCreate: (body: TaskCreatePayload) => Promise<void> | void;
  onUpdate: (id: number, body: TaskUpdatePayload) => Promise<void> | void;
  onDelete: (id: number) => Promise<void> | void;
}

function renderGroupedChatItems(
  items: ChatItem[],
  streamingItemId?: string | null,
  sessionId?: string | null,
  tasksCtx?: TasksContext,
) {
  const result: React.ReactNode[] = [];
  let i = 0;

  while (i < items.length) {
    const cur = items[i];

    // A tasks_anchor renders the LIVE tasks card inline at this point in
    // the chat stream. The card always reads from `tasksCtx.tasks`, so
    // every anchor reflects the current global state — when a task
    // status changes, every previously-rendered card updates too.
    if (cur.type === 'tasks_anchor') {
      if (tasksCtx) {
        result.push(
          <InlineTasks
            key={`tasks-${cur.id}`}
            tasks={tasksCtx.tasks}
            onCreate={tasksCtx.onCreate}
            onUpdate={tasksCtx.onUpdate}
            onDelete={tasksCtx.onDelete}
          />,
        );
      }
      i++;
      continue;
    }

    if (isToolItem(cur)) {
      const group: ChatItem[] = [];
      while (i < items.length && isToolItem(items[i])) {
        group.push(items[i]);
        i++;
      }
      result.push(<ToolGroupCard key={`tg-${group[0].id}`} items={group} />);
    } else {
      result.push(renderChatItem(cur, streamingItemId, sessionId));
      i++;
    }
  }

  return result;
}

// ---------------------------------------------------------------------------
// renderChatItem
// ---------------------------------------------------------------------------

function renderChatItem(
  item: ChatItem,
  streamingItemId?: string | null,
  sessionId?: string | null,
) {
  switch (item.type) {
    case 'user': {
      const files: string[] = item.meta?.files || [];
      const mentions: Mention[] | undefined = item.meta?.mentions;
      const hasText = item.content && item.content.trim().length > 0;
      const hasFiles = files.length > 0;
      const tokens = mentions && mentions.length > 0 ? wireToDraft(item.content, mentions) : null;
      return (
        <div key={item.id} className="flex justify-end animate-fade-in">
          <div className="max-w-[80%] rounded-2xl rounded-br-md bg-primary-600 text-white text-sm overflow-hidden">
            {hasFiles && (
              <div className={`flex flex-wrap gap-1.5 px-4 ${hasText ? 'pt-3 pb-1' : 'py-3'}`}>
                {files.map((f, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/15 text-xs"
                  >
                    <Paperclip className="w-3 h-3 opacity-70" />
                    <span className="truncate max-w-[150px]">{f}</span>
                  </span>
                ))}
              </div>
            )}
            {hasText && (
              <div className="px-4 py-2.5 whitespace-pre-wrap break-words">
                {tokens
                  ? tokens.map((t, i) =>
                      t.kind === 'text' ? (
                        <span key={i}>{t.value}</span>
                      ) : (
                        <MentionPill key={i} mention={t.mention} />
                      ),
                    )
                  : item.content}
              </div>
            )}
            {!hasText && !hasFiles && <div className="px-4 py-2.5">{item.content}</div>}
          </div>
        </div>
      );
    }
    case 'assistant': {
      // Color the avatar based on which agent produced this message
      const agentType = item.meta?.agent_type;
      const agentMeta = agentType ? AGENT_META[agentType] : null;
      const agentColor = agentMeta ? AGENT_COLORS[agentMeta.color] : null;
      const avatarBg = agentColor ? agentColor.bg : 'bg-emerald-500/20';
      const avatarText = agentColor ? agentColor.text : 'text-emerald-400';
      const isStreaming = item.id === streamingItemId;

      return (
        <div key={item.id} className="flex gap-3 animate-fade-in">
          <div
            className={`w-7 h-7 rounded-full ${avatarBg} flex items-center justify-center shrink-0 mt-1`}
          >
            <Bot className={`w-3.5 h-3.5 ${avatarText}`} />
          </div>
          <div className="flex-1 min-w-0 text-sm text-gray-200 markdown-content">
            {agentMeta && (
              <div className={`text-[10px] ${avatarText} font-medium mb-1`}>{agentMeta.label}</div>
            )}
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content}</ReactMarkdown>
            {isStreaming && (
              <span className="inline-block w-2 h-5 bg-primary-400 rounded-sm ml-0.5 animate-blink align-text-bottom" />
            )}
          </div>
        </div>
      );
    }
    case 'tool_start':
    case 'tool_end':
      return <CollapsibleToolCard key={item.id} item={item} />;
    case 'code_output':
      return null; // folded into the tool card above
    case 'subagent_start':
    case 'subagent_end':
      return <SubAgentCard key={item.id} item={item} />;
    case 'clarification':
      return <ClarificationCard key={item.id} item={item} sessionId={sessionId ?? null} />;
    case 'agent_tool':
      return <AgentToolCard key={item.id} item={item} />;
    case 'error':
      return (
        <div
          key={item.id}
          className="animate-fade-in flex items-center gap-2 px-3 py-2 bg-red-900/30 border border-red-800/50 rounded-lg text-sm text-red-400"
        >
          <AlertCircle className="w-4 h-4 shrink-0" />
          {item.content}
        </div>
      );
    case 'status':
      return (
        <div key={item.id} className="text-center">
          <span
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium ${
              item.content.includes('running')
                ? 'bg-amber-500/20 text-amber-400'
                : item.content.includes('done')
                  ? 'bg-green-500/20 text-green-400'
                  : item.content === 'failed'
                    ? 'bg-red-500/20 text-red-400'
                    : 'bg-neutral-800 text-gray-400'
            }`}
          >
            {item.content.includes('running') && <Loader2 className="w-3 h-3 animate-spin" />}
            {item.content.includes('done') && <CheckCircle2 className="w-3 h-3" />}
            {item.content.replace(/_/g, ' ')}
          </span>
        </div>
      );
    case 'stage_complete':
      return (
        <div key={item.id} className="flex items-center justify-center py-2 animate-fade-in">
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-green-500/10 border border-green-500/20">
            <CheckCircle2 className="w-4 h-4 text-green-400" />
            <span className="text-sm font-medium text-green-300">{item.content} complete</span>
          </div>
        </div>
      );
    default:
      return null;
  }
}
