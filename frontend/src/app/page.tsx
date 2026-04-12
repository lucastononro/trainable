'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useApp } from '@/lib/AppContext';
import { api } from '@/lib/api';
import { SSEEvent, FileTreeNode, MetricPoint, ChartConfig } from '@/lib/types';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
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
} from 'lucide-react';
import Sidebar from '@/components/Sidebar';
import ModelSelector from '@/components/ModelSelector';
import AgentStatusIndicator, { ActiveAgent } from '@/components/AgentStatusIndicator';
import MetricsTab from '@/components/MetricsTab';
import S3FileBrowserModal from '@/components/S3FileBrowserModal';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  buildTreeFromFlatList,
  insertNodeIntoTree,
  ensureStageFolders,
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
    | 'subagent_end';
  content: string;
  meta?: any;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// Welcome screen suggestions
// ---------------------------------------------------------------------------

const SUGGESTIONS = [
  { icon: BarChart3, label: 'Explore a dataset', prompt: 'Analyze this dataset \u2014 perform a full EDA.' },
  { icon: Cpu, label: 'Train a model', prompt: 'Train a model on this dataset.' },
  { icon: Database, label: 'Clean & prep data', prompt: 'Clean and prepare this dataset for modeling.' },
  { icon: Terminal, label: 'Write a script', prompt: 'Write a Python script to process this data.' },
];

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function HomePage() {
  const {
    experiments,
    activeExperimentId,
    activeSessionId,
    selectedModel,
    sidebarOpen,
    setSidebarOpen,
    setActiveExperiment,
    refreshExperiments,
  } = useApp();

  // Chat / session state
  const [chatItems, setChatItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [sessionState, setSessionState] = useState('created');
  const [loading, setLoading] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [experimentName, setExperimentName] = useState('');


  // Workspace state
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasContent, setCanvasContent] = useState('');
  const [canvasTitle, setCanvasTitle] = useState('Report');
  const [generatedFiles, setGeneratedFiles] = useState<any[]>([]);
  const [fileTree, setFileTree] = useState<FileTreeNode>(() =>
    ensureStageFolders({
      name: 'workspace',
      path: '/',
      type: 'directory',
      children: [],
    }),
  );

  // Metrics state
  const [metricPoints, setMetricPoints] = useState<MetricPoint[]>([]);
  const [chartConfig, setChartConfig] = useState<ChartConfig | null>(null);
  const metricKeysRef = useRef(new Set<string>());

  const bottomRef = useRef<HTMLDivElement>(null);
  const sseRef = useRef<EventSource | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevExperimentIdRef = useRef<string | null>(null);
  const streamingItemIdRef = useRef<string | null>(null);

  // Active agents tracking (for header indicator)
  const [activeAgents, setActiveAgents] = useState<ActiveAgent[]>([]);
  const activeAgentsRef = useRef<ActiveAgent[]>([]);
  // Keep ref in sync for use inside SSE handler closure
  useEffect(() => { activeAgentsRef.current = activeAgents; }, [activeAgents]);

  // File attachment state
  const [attachedFiles, setAttachedFiles] = useState<File[]>([]);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showS3Browser, setShowS3Browser] = useState(false);
  const [attachingFiles, setAttachingFiles] = useState(false);
  const fileInputRef2 = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const attachMenuRef = useRef<HTMLDivElement>(null);

  // Pending message ref: when we auto-create an experiment, we queue the message
  const pendingMessageRef = useRef<string | null>(null);

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
              if (data.state.includes('running')) setIsRunning(true);
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
                const stageName = data.state.replace('_done', '').toUpperCase();
                addItem({ type: 'stage_complete', content: stageName });
              }
              break;
            case 'agent_message': {
              // Tag message with the currently active agent type
              const running = activeAgentsRef.current.filter(a => a.status === 'running');
              const currentAgentType = running.length > 0 ? running[running.length - 1].type : undefined;
              addItem({ type: 'assistant', content: data.text, meta: { agent_type: currentAgentType } });
              break;
            }
            case 'agent_token':
              setChatItems((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.type === 'assistant' && last.id === streamingItemIdRef.current) {
                  return [...prev.slice(0, -1), { ...last, content: last.content + data.text }];
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
                  },
                ];
              });
              break;
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
                addItem({ type: 'code_output', content: data.text, meta: { stream: data.stream } });
                return prev;
              });
              break;
            case 'agent_error':
              streamingItemIdRef.current = null;
              addItem({ type: 'error', content: data.error });
              setIsRunning(false);
              break;
            case 'report_ready':
              setCanvasContent(data.content);
              setCanvasTitle(`${(data.stage || 'EDA').toUpperCase()} Report`);
              setCanvasOpen(true);
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
                return ensureStageFolders(merged);
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
                  if (prev.length === 0) setCanvasOpen(true);
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
                  if (prev.length === 0) setCanvasOpen(true);
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
          }
        } catch {
          /* ignore parse errors */
        }
      };
      source.onerror = () => setSseConnected(false);
      sseRef.current = source;
    },
    [addItem],
  );

  // ---------------------------------------------------------------------------
  // Reset state when active experiment changes
  // ---------------------------------------------------------------------------

  const resetSessionState = useCallback(() => {
    setChatItems([]);
    setInput('');
    setIsRunning(false);
    streamingItemIdRef.current = null;
    setSessionState('created');
    setCanvasOpen(false);
    setCanvasContent('');
    setCanvasTitle('Report');
    setGeneratedFiles([]);
    setFileTree(
      ensureStageFolders({
        name: 'workspace',
        path: '/',
        type: 'directory',
        children: [],
      }),
    );
    setMetricPoints([]);
    setChartConfig(null);
    metricKeysRef.current = new Set();
    setExperimentName('');
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
          for (const msg of sessionData.messages) {
            const eventType = msg.metadata?.event_type;
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
                const stageName = st.replace('_done', '').toUpperCase();
                restored.push(
                  mkItem({ type: 'stage_complete', content: stageName }),
                );
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
            } else if (msg.role === 'user') {
              if (msg.metadata?.event_type === 'file_attached') continue;
              restored.push(mkItem({ type: 'user', content: msg.content }));
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

        // Convert orphaned subagent_start to subagent_end
        for (let i = 0; i < restored.length; i++) {
          if (restored[i].type === 'subagent_start') {
            restored[i] = { ...restored[i], type: 'subagent_end' };
          }
        }

        setChatItems(restored);
        setCanvasContent(restoredCanvasContent);
        setCanvasTitle(restoredCanvasTitle);
        setCanvasOpen(restoredCanvasOpen);
        setGeneratedFiles(restoredFiles);

        // Build file tree from restored files
        if (restoredFiles.length > 0) {
          setFileTree(buildTreeFromFlatList(restoredFiles, `/sessions/${sid}`));
        }
        // Fetch live tree from volume
        api
          .getFileTree(sid)
          .then((tree) => {
            if (!cancelled) setFileTree(ensureStageFolders(unwrapTree(tree)));
          })
          .catch(() => {});

        // Load historical metrics
        api
          .getMetrics(sid)
          .then((metrics) => {
            if (!cancelled && metrics.length > 0) {
              setMetricPoints(metrics);
              setCanvasOpen(true);
              for (const m of metrics) {
                metricKeysRef.current.add(`${m.step}:${m.name}:${m.run_tag || ''}`);
              }
            }
          })
          .catch(() => {});

        // Set running state from session
        if (sessionData.state) setSessionState(sessionData.state);
        if (sessionData.state?.includes('running')) {
          setIsRunning(true);
        }

        connectSSE(sid);
      } catch (e) {
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
  }, [activeExperimentId, activeSessionId, connectSSE, addItem, resetSessionState]);

  // ---------------------------------------------------------------------------
  // Send pending message once SSE is connected (after auto-create)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (pendingMessageRef.current && activeSessionId && sseConnected) {
      const text = pendingMessageRef.current;
      pendingMessageRef.current = null;
      addItem({ type: 'user', content: text });
      setIsRunning(true);
      api.sendMessage(activeSessionId, text, true).catch((e: any) => {
        addItem({ type: 'error', content: e.message });
        setIsRunning(false);
      });
    }
  }, [activeSessionId, sseConnected, addItem]);

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

  const handleSend = async () => {
    // If there are attached files, use the attach-and-send flow
    if (attachedFiles.length > 0) {
      await handleAttachAndSend();
      return;
    }

    if (!input.trim()) return;
    const text = input.trim();
    setInput('');

    // If no active experiment, auto-create one
    if (!activeExperimentId || !activeSessionId) {
      try {
        const result = await api.quickCreate(undefined, text);
        await refreshExperiments();
        setActiveExperiment(result.id, result.session_id);
        // Queue the message to be sent once SSE connects
        pendingMessageRef.current = text;
      } catch (e: any) {
        addItem({ type: 'error', content: `Failed to create: ${e.message}` });
      }
      return;
    }

    addItem({ type: 'user', content: text });
    setIsRunning(true);

    try {
      await api.sendMessage(activeSessionId, text, true);
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
    if (attachedFiles.length === 0 && !input.trim()) return;
    setAttachingFiles(true);

    try {
      let expId = activeExperimentId;
      let sesId = activeSessionId;

      // Auto-create experiment if none active
      if (!expId || !sesId) {
        const result = await api.quickCreate();
        await refreshExperiments();
        setActiveExperiment(result.id, result.session_id);
        expId = result.id;
        sesId = result.session_id;
      }

      // Upload files if any
      if (attachedFiles.length > 0 && expId) {
        await api.attachData(expId, attachedFiles, undefined, sesId || undefined);
        await refreshExperiments();
        addItem({
          type: 'status',
          content: `Attached ${attachedFiles.length} file${attachedFiles.length > 1 ? 's' : ''}`,
        });
        setAttachedFiles([]);
      }

      // Send message if any
      if (input.trim() && sesId) {
        const text = input.trim();
        setInput('');
        addItem({ type: 'user', content: text });
        setIsRunning(true);
        await api.sendMessage(sesId, text, true);
      }
    } catch (e: any) {
      addItem({ type: 'error', content: e.message });
    } finally {
      setAttachingFiles(false);
    }
  }, [attachedFiles, input, activeExperimentId, activeSessionId, refreshExperiments, setActiveExperiment, addItem]);

  const handleS3Select = useCallback(
    async (s3Path: string) => {
      setShowS3Browser(false);
      setAttachingFiles(true);
      try {
        let expId = activeExperimentId;
        if (!expId) {
          const result = await api.quickCreate();
          await refreshExperiments();
          setActiveExperiment(result.id, result.session_id);
          expId = result.id;
        }
        if (expId) {
          await api.attachData(expId, undefined, s3Path, activeSessionId || undefined);
          await refreshExperiments();
          addItem({ type: 'status', content: `Attached S3 data: ${s3Path}` });
        }
      } catch (e: any) {
        addItem({ type: 'error', content: e.message });
      } finally {
        setAttachingFiles(false);
      }
    },
    [activeExperimentId, refreshExperiments, setActiveExperiment, addItem],
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
    setInput(prompt);
    inputRef.current?.focus();
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const hasActiveSession = !!activeExperimentId && !!activeSessionId;

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

          {hasActiveSession && (
            <AgentStatusIndicator agents={activeAgents} isRunning={isRunning} />
          )}

          <ModelSelector />

          {hasActiveSession && (
            <>
              <button
                onClick={() => {
                  setCanvasOpen(true);
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
                onClick={() => setCanvasOpen((prev) => !prev)}
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
        ) : !hasActiveSession ? (
          // -------------------------------------------------------------------
          // Welcome screen
          // -------------------------------------------------------------------
          <div className="flex-1 flex flex-col items-center justify-center px-4">
            <div className="w-full max-w-2xl space-y-8">
              {/* Logo + title */}
              <div className="text-center space-y-3">
                <div className="flex items-center justify-center gap-3">
                  <img src="/logo-brain.png" alt="Trainable" className="h-10 w-auto" />
                </div>
                <h1 className="text-2xl font-semibold text-white">What would you like to explore?</h1>
                <p className="text-sm text-gray-500">
                  Upload a dataset or describe what you want to build. Trainable will handle the rest.
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
                <div className="flex items-center gap-2 bg-surface-elevated border border-surface-border rounded-2xl px-3 py-3 focus-within:border-primary-500 transition-colors">
                  {/* Attach button */}
                  <div className="relative" ref={attachMenuRef}>
                    <button
                      onClick={() => setShowAttachMenu(!showAttachMenu)}
                      className={`p-1.5 rounded-xl transition-colors shrink-0 ${
                        showAttachMenu ? 'bg-white/[0.1] text-white' : 'hover:bg-white/[0.08] text-gray-400 hover:text-gray-300'
                      }`}
                      title="Attach files or data"
                    >
                      <Plus className="w-5 h-5" />
                    </button>
                    {showAttachMenu && (
                      <div className="absolute bottom-full left-0 mb-2 w-52 bg-[#1a1a1a] border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
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
                          onClick={() => { setShowAttachMenu(false); setShowS3Browser(true); }}
                          className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                        >
                          <HardDrive className="w-4 h-4 text-gray-500" />
                          Browse S3 data
                        </button>
                      </div>
                    )}
                  </div>

                  <input
                    ref={inputRef}
                    type="text"
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
                    placeholder="Describe your task, ask a question, or upload data..."
                    className="flex-1 bg-transparent text-white text-sm placeholder-gray-500 focus:outline-none py-1"
                  />
                  <button
                    onClick={handleSend}
                    disabled={!input.trim() && attachedFiles.length === 0}
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
          <PanelGroup direction="horizontal" className="flex-1">
            {/* Chat panel */}
            <Panel defaultSize={canvasOpen ? 25 : 100} minSize={15}>
              <div className="h-full flex flex-col min-w-0">
                <div className="flex-1 overflow-y-auto px-4 py-4">
                  <div
                    className={`mx-auto w-full space-y-4 ${canvasOpen ? 'max-w-3xl' : 'max-w-5xl'}`}
                  >
                    {chatItems.map((item) => renderChatItem(item, streamingItemIdRef.current))}

                    {isRunning && !streamingItemIdRef.current && (() => {
                      const last = chatItems[chatItems.length - 1];
                      return !last || last.type !== 'tool_start';
                    })() && (
                      <div className="flex gap-3 animate-fade-in">
                        <div className="w-7 h-7 rounded-full bg-emerald-500/20 flex items-center justify-center shrink-0">
                          <Bot className="w-3.5 h-3.5 text-emerald-400" />
                        </div>
                        <div className="flex items-center gap-1.5 px-4 py-2.5 rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border">
                          <span className="w-2 h-2 rounded-full bg-gray-400 animate-typing" style={{ animationDelay: '0ms' }} />
                          <span className="w-2 h-2 rounded-full bg-gray-400 animate-typing" style={{ animationDelay: '150ms' }} />
                          <span className="w-2 h-2 rounded-full bg-gray-400 animate-typing" style={{ animationDelay: '300ms' }} />
                        </div>
                      </div>
                    )}
                    <div ref={bottomRef} />
                  </div>
                </div>

                {/* Input bar */}
                <div className="border-t border-surface-border bg-surface px-4 py-3">
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

                    <div className="flex items-center gap-1 bg-surface-elevated border border-surface-border rounded-2xl px-2 py-1.5 focus-within:border-primary-500 transition-colors">
                      {/* Attach menu */}
                      <div className="relative" ref={attachMenuRef}>
                        <button
                          type="button"
                          onClick={() => setShowAttachMenu(!showAttachMenu)}
                          className={`p-2 rounded-xl transition-colors shrink-0 ${
                            showAttachMenu ? 'bg-white/[0.1] text-white' : 'hover:bg-neutral-700 text-gray-400 hover:text-gray-300'
                          }`}
                          title="Attach files or data"
                        >
                          <Plus className="w-4 h-4" />
                        </button>
                        {showAttachMenu && (
                          <div className="absolute bottom-full left-0 mb-2 w-52 bg-[#1a1a1a] border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
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
                              onClick={() => { setShowAttachMenu(false); setShowS3Browser(true); }}
                              className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-gray-300 hover:bg-white/[0.06] transition-colors"
                            >
                              <HardDrive className="w-4 h-4 text-gray-500" />
                              Browse S3 data
                            </button>
                          </div>
                        )}
                      </div>

                      <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onKeyDown={(e) =>
                          e.key === 'Enter' &&
                          !e.shiftKey &&
                          (isRunning && !input.trim() && attachedFiles.length === 0 ? handleStop() : handleSend())
                        }
                        placeholder="Ask anything"
                        className="flex-1 bg-transparent text-white text-sm placeholder-gray-500 focus:outline-none py-1.5"
                      />
                      {isRunning && !input.trim() && attachedFiles.length === 0 ? (
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
                          disabled={!input.trim() && attachedFiles.length === 0}
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
            {canvasOpen && (
              <>
                <PanelResizeHandle className="w-1.5 bg-surface-border hover:bg-primary-500/50 active:bg-primary-500/70 transition-colors relative group flex items-center justify-center">
                  <div className="opacity-0 group-hover:opacity-100 transition-opacity">
                    <GripVertical className="w-3 h-3 text-gray-400" />
                  </div>
                </PanelResizeHandle>
                <Panel defaultSize={75} minSize={30}>
                  <WorkspaceSidebar
                    experimentId={activeExperimentId}
                    sessionId={activeSessionId}
                    canvasContent={canvasContent}
                    canvasTitle={canvasTitle}
                    generatedFiles={generatedFiles}
                    fileTree={fileTree}
                    metricPoints={metricPoints}
                    chartConfig={chartConfig}
                    sessionState={sessionState}
                    onClose={() => setCanvasOpen(false)}
                  />
                </Panel>
              </>
            )}
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

function FileViewer({ filePath, sessionId }: { filePath: string; sessionId: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fileName = filePath.split('/').pop() || '';
  const isImage = /\.(png|jpg|jpeg|svg|gif)$/i.test(fileName);
  const isPython = fileName.endsWith('.py');
  const isMarkdown = fileName.endsWith('.md');
  const isJSON = fileName.endsWith('.json');
  const isBinary = /\.(pkl|joblib|parquet|h5|hdf5|pt|pth|onnx)$/i.test(fileName);

  useEffect(() => {
    if (isImage || isBinary) {
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
  }, [filePath]);

  return (
    <div className="h-full flex flex-col bg-[#0d1117]">
      <div className="flex-1 overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="w-5 h-5 text-gray-500 animate-spin" />
          </div>
        ) : error ? (
          <div className="p-4 text-sm text-red-400">{error}</div>
        ) : isImage ? (
          <div className="p-6 flex items-center justify-center bg-[#0d1117]">
            <img
              src={`${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(filePath)}`}
              alt={fileName}
              className="max-w-full max-h-[60vh] rounded-lg"
            />
          </div>
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
}

// ---------------------------------------------------------------------------
// Workspace Panel -- github.dev-style: tree sidebar + tabbed editor
// ---------------------------------------------------------------------------

interface OpenTab {
  id: string;
  label: string;
  icon: typeof FileText;
  iconColor: string;
  type: 'file' | 'report' | 'metrics';
}

const REPORT_TAB_ID = '__report__';
const METRICS_TAB_ID = '__metrics__';

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
    <div className="h-full border-l border-surface-border flex flex-row bg-[#0d1117]">
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
                      ? 'bg-[#0d1117] text-gray-200 border-t-2 border-t-primary-500'
                      : 'bg-surface text-gray-500 hover:text-gray-300 border-t-2 border-t-transparent'
                  }`}
                >
                  <TabIcon className={`w-3.5 h-3.5 shrink-0 ${tab.iconColor}`} />
                  <span className="truncate max-w-[120px]">{tab.label}</span>
                  <span
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
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
          <div className="flex items-center gap-1 px-3 h-6 bg-[#0d1117] border-b border-white/[0.04] shrink-0">
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

        {/* Content area */}
        <div className="flex-1 overflow-hidden">
          {activeTab?.type === 'file' ? (
            <FileViewer filePath={activeTab.id} sessionId={sessionId} />
          ) : activeTab?.type === 'report' && canvasContent ? (
            <div className="h-full overflow-y-auto p-6 bg-[#0d1117]">
              <div className="markdown-content">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    img: ({ src, alt }) => {
                      let imgSrc = src || '';
                      if (imgSrc.startsWith('/data/')) {
                        imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(imgSrc)}`;
                      } else if (imgSrc && !imgSrc.startsWith('http')) {
                        const workspace = `/sessions/${sessionId}/eda`;
                        imgSrc = `${getBackendUrl()}/api/files/raw?path=${encodeURIComponent(workspace + '/' + imgSrc)}`;
                      }
                      return (
                        <img
                          src={imgSrc}
                          alt={alt || ''}
                          className="max-w-full rounded-lg shadow-md my-4"
                        />
                      );
                    },
                  }}
                >
                  {canvasContent}
                </ReactMarkdown>
              </div>
            </div>
          ) : activeTab?.type === 'metrics' ? (
            <div className="h-full overflow-hidden bg-[#0d1117]">
              <MetricsTab
                metricPoints={metricPoints}
                chartConfig={chartConfig}
                state={sessionState}
              />
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-gray-600 bg-[#0d1117]">
              <Code2 className="w-8 h-8 mb-2 text-gray-700" />
              <p className="text-xs">Select a file to view</p>
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
// CollapsibleToolCard -- amber themed tool execution card
// ---------------------------------------------------------------------------

function CollapsibleToolCard({ item }: { item: ChatItem }) {
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

  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 bg-amber-500/20">
        <Code2 className="w-3.5 h-3.5 text-amber-400" />
      </div>
      <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-surface-elevated border border-surface-border overflow-hidden">
        <div
          className="flex items-center gap-2 px-4 py-2.5 cursor-pointer select-none"
          onClick={() => setCollapsed((prev) => !prev)}
        >
          {isStart ? (
            <Loader2 className="w-3.5 h-3.5 text-amber-400 animate-spin" />
          ) : (
            <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
          )}
          <span className="text-sm text-gray-300 flex-1">
            {isStart
              ? `${funVerb}...${elapsed > 0 ? ` ${elapsed}s` : ''}`
              : `${doneLabel} for ${item.meta?.duration || 1}s`}
          </span>
          <ChevronRight
            className={`w-3.5 h-3.5 text-gray-500 transition-transform duration-150 ${
              !collapsed ? 'rotate-90' : ''
            }`}
          />
        </div>
        {!collapsed && (
          <>
            {item.meta?.code && (
              <pre className="px-4 py-2 text-xs text-gray-400 font-mono max-h-24 overflow-y-auto border-t border-surface-border whitespace-pre-wrap">
                {item.meta.code.length > 300
                  ? item.meta.code.slice(0, 300) + '...'
                  : item.meta.code}
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
  blue: { bg: 'bg-blue-500/15', text: 'text-blue-400', border: 'border-blue-500/20', dot: 'bg-blue-400' },
  amber: { bg: 'bg-amber-500/15', text: 'text-amber-400', border: 'border-amber-500/20', dot: 'bg-amber-400' },
  green: { bg: 'bg-green-500/15', text: 'text-green-400', border: 'border-green-500/20', dot: 'bg-green-400' },
  orange: { bg: 'bg-orange-500/15', text: 'text-orange-400', border: 'border-orange-500/20', dot: 'bg-orange-400' },
  rose: { bg: 'bg-rose-500/15', text: 'text-rose-400', border: 'border-rose-500/20', dot: 'bg-rose-400' },
  violet: { bg: 'bg-violet-500/15', text: 'text-violet-400', border: 'border-violet-500/20', dot: 'bg-violet-400' },
  gray: { bg: 'bg-gray-500/15', text: 'text-gray-400', border: 'border-gray-500/20', dot: 'bg-gray-400' },
  teal: { bg: 'bg-teal-500/15', text: 'text-teal-400', border: 'border-teal-500/20', dot: 'bg-teal-400' },
};

function SubAgentCard({ item }: { item: ChatItem }) {
  const isStart = item.type === 'subagent_start';
  const [collapsed, setCollapsed] = useState(true);
  const [elapsed, setElapsed] = useState(0);

  const agentType = item.content || 'sub-agent';
  const meta = AGENT_META[agentType] || { label: agentType.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()), color: 'teal' };
  const colors = AGENT_COLORS[meta.color] || AGENT_COLORS.teal;
  const modelName = item.meta?.model ? item.meta.model.replace('claude-', '').replace(/-/g, ' ') : '';

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
        <div className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 ${colors.bg}`}>
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
// renderChatItem
// ---------------------------------------------------------------------------

function renderChatItem(item: ChatItem, streamingItemId?: string | null) {
  switch (item.type) {
    case 'user':
      return (
        <div key={item.id} className="flex justify-end animate-fade-in">
          <div className="max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-primary-600 text-white text-sm">
            {item.content}
          </div>
        </div>
      );
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
          <div className={`w-7 h-7 rounded-full ${avatarBg} flex items-center justify-center shrink-0 mt-1`}>
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
