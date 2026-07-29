'use client';

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { useApp } from '@/lib/AppContext';
import { useSSEStream } from '@/lib/SSEStreamContext';
import { api } from '@/lib/api';
import type {
  SSEEvent,
  FileTreeNode,
  MetricPoint,
  ChartConfig,
  LogEvent,
  HtmlArtifact,
  Mention,
  Task,
  GeneratedFile,
  UsageEvent,
  BudgetInfo,
  EdaFinding,
} from '@/lib/types';
import { type ChatItem, metaStr, metaNum } from '@/lib/chatItems';
import type { ActiveAgent } from '@/components/AgentStatusIndicator';
import type { UsageTotals } from '@/components/CostBadge';
import { buildTreeFromFlatList, insertNodeIntoTree, unwrapTree } from '@/lib/useFileTree';

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

export interface SessionStream {
  // Chat / session state
  chatItems: ChatItem[];
  addItem: (item: Omit<ChatItem, 'id' | 'timestamp'>) => void;
  sessionState: string;
  loading: boolean;
  sseConnected: boolean;
  tasks: Task[];
  /** Id of the chat item currently receiving streamed assistant tokens. */
  streamingItemIdRef: MutableRefObject<string | null>;
  /** Whether the user is scrolled near the bottom of the chat pane (see
   *  ChatPane's scroll tracking). Owned here because `addItem` re-pins it
   *  when the user sends a message. */
  pinnedToBottomRef: MutableRefObject<boolean>;
  // Workspace state
  canvasContent: string;
  canvasTitle: string;
  generatedFiles: GeneratedFile[];
  fileTree: FileTreeNode;
  htmlArtifacts: Map<string, HtmlArtifact>;
  /** Structured EDA findings (issue #111) — rendered as canvas action cards. */
  edaFindings: EdaFinding[];
  // Metrics state
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  logEvents: LogEvent[];
  // Live usage totals for the active session (cost badge in header)
  usageTotals: UsageTotals;
  recentUsage: UsageEvent[];
  /** Project budget vs. spend (issue #107) — hydrated with session usage,
   *  flipped to exceeded by the budget_exceeded SSE event. */
  budgetInfo: BudgetInfo | null;
  // Active agents tracking (for header indicator)
  activeAgents: ActiveAgent[];
}

// ---------------------------------------------------------------------------
// useSessionStream — owns the single per-session EventSource, the big
// `onmessage` reducer, and every state slice that reducer (plus the
// reload-hydration path) writes: chat items, tasks, canvas/report content,
// file tree, metrics, rich log events, HTML artifacts, usage totals, and
// active-agent indicators. The view layer only reads these slices; the
// only mutations it can perform are `addItem` (append a local chat item)
// and the two refs (streaming id, pinned-to-bottom).
// ---------------------------------------------------------------------------

export function useSessionStream(
  activeExperimentId: string | null,
  activeSessionId: string | null,
  {
    openCanvas,
    collapseCanvas,
    onReset,
  }: {
    /** Opens the workspace panel (idempotent; also kicks the default-tab picker). */
    openCanvas: () => void;
    /** Collapses the workspace panel (used when resetting session state). */
    collapseCanvas: () => void;
    /** Called whenever session state is reset (experiment switch / clear) so
     *  the view can clear its own per-session state (e.g. the input draft).
     *  Must be referentially stable (wrap in useCallback). */
    onReset: () => void;
  },
): SessionStream {
  const { refreshExperiments, setIsRunning } = useApp();
  // Broadcasts every parsed message from the single connectSSE EventSource
  // below to any other subscriber (e.g. the notebook) so nobody else has to
  // open a second EventSource to the same `/api/sessions/{id}/stream`.
  const { publish } = useSSEStream();

  // Chat / session state
  const [chatItems, setChatItems] = useState<ChatItem[]>([]);
  const [sessionState, setSessionState] = useState('created');
  const [loading, setLoading] = useState(false);
  const [sseConnected, setSseConnected] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);

  // Workspace state
  const [canvasContent, setCanvasContent] = useState('');
  const [canvasTitle, setCanvasTitle] = useState('Report');
  const [generatedFiles, setGeneratedFiles] = useState<GeneratedFile[]>([]);
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
  // Rich log payloads (image grids, tables, confusion matrices, …) — keyed
  // dedup so backend resends + reload-hydrate don't double-render.
  const [logEvents, setLogEvents] = useState<LogEvent[]>([]);
  const logEventKeysRef = useRef(new Set<string>());
  // Agent-published HTML artifacts: keyed by `key` so regenerating an
  // artifact in the same session overwrites instead of piling tabs.
  const [htmlArtifacts, setHtmlArtifacts] = useState<Map<string, HtmlArtifact>>(() => new Map());
  // Structured EDA findings (issue #111). Appended per eda_findings batch,
  // deduped on (type + columns + summary) so a backend resend or a re-run
  // publishing the same findings doesn't double the cards.
  const [edaFindings, setEdaFindings] = useState<EdaFinding[]>([]);
  const edaFindingKeysRef = useRef(new Set<string>());

  // Whether the user is scrolled near the bottom of the chat pane. The
  // auto-scroll effect (in ChatPane) only fires while this stays true —
  // otherwise a user who scrolls up to read earlier output during a long
  // streaming response gets yanked back to the bottom on every token. A ref
  // (not state) because the scroll handler runs on every native scroll
  // event and we don't want that to trigger a re-render.
  const pinnedToBottomRef = useRef(true);
  const sseRef = useRef<EventSource | null>(null);
  const prevExperimentIdRef = useRef<string | null>(null);
  const streamingItemIdRef = useRef<string | null>(null);

  // Live usage totals for the active session (cost badge in header)
  const [usageTotals, setUsageTotals] = useState<UsageTotals>(ZERO_USAGE);
  const [recentUsage, setRecentUsage] = useState<UsageEvent[]>([]);
  const [budgetInfo, setBudgetInfo] = useState<BudgetInfo | null>(null);

  // Active agents tracking (for header indicator)
  const [activeAgents, setActiveAgents] = useState<ActiveAgent[]>([]);
  const activeAgentsRef = useRef<ActiveAgent[]>([]);
  // Keep ref in sync for use inside SSE handler closure
  useEffect(() => {
    activeAgentsRef.current = activeAgents;
  }, [activeAgents]);

  // ---------------------------------------------------------------------------
  // addItem helper
  // ---------------------------------------------------------------------------

  const addItem = useCallback((item: Omit<ChatItem, 'id' | 'timestamp'>) => {
    // The user just sent something — they're at the input, not mid-read of
    // scrollback, so re-pin to bottom even if they'd scrolled up earlier.
    if (item.type === 'user') pinnedToBottomRef.current = true;
    setChatItems((prev) => [
      ...prev,
      { ...item, id: `${Date.now()}-${Math.random()}`, timestamp: Date.now() },
    ]);
  }, []);

  // Append a batch of structured EDA findings (live SSE or reload-hydrate),
  // deduped so resends don't double the cards. Opens the canvas when new
  // cards land — same contract as metrics/log events.
  const appendEdaFindings = useCallback(
    (items: unknown) => {
      if (!Array.isArray(items)) return;
      const fresh: EdaFinding[] = [];
      for (const raw of items) {
        const f = raw as Partial<EdaFinding> | null;
        if (!f || typeof f.summary !== 'string' || typeof f.recommendation !== 'string') continue;
        if (!f.summary || !f.recommendation) continue;
        const columns = Array.isArray(f.columns) ? f.columns.map(String) : [];
        const key = `${f.finding_type || 'other'}:${columns.join(',')}:${f.summary}`;
        if (edaFindingKeysRef.current.has(key)) continue;
        edaFindingKeysRef.current.add(key);
        fresh.push({
          finding_type: f.finding_type || 'other',
          columns,
          severity: f.severity === 'info' || f.severity === 'critical' ? f.severity : 'warning',
          summary: f.summary,
          recommendation: f.recommendation,
        });
      }
      if (fresh.length > 0) {
        setEdaFindings((prev) => [...prev, ...fresh]);
        openCanvas();
      }
    },
    [openCanvas],
  );

  // ---------------------------------------------------------------------------
  // SSE connection
  // ---------------------------------------------------------------------------

  const connectSSE = useCallback(
    (sid: string) => {
      if (sseRef.current) sseRef.current.close();
      const url = `/api/sessions/${sid}/stream`;
      const source = new EventSource(url);

      source.onopen = () => setSseConnected(true);
      source.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data) as SSEEvent;
          // Fan out the parsed event to any other subscriber (e.g. the
          // notebook) before/independent of the switch below — this is the
          // single EventSource for the session, so everyone shares it. `sid`
          // tags the event with its owning session so subscribers can ignore
          // stale cross-session deliveries during a session switch.
          publish(sid, event);

          // Narrowing on `event.type` below gives each case a correctly
          // typed `event.data` (see the `SSEEvent` union in lib/types.ts) —
          // no blanket `as any` needed. Cases are wrapped in their own
          // `{ }` block so each can bind its own locally-scoped `data`.
          switch (event.type) {
            case 'state_change': {
              const data = event.data;
              setSessionState(data.state);
              if (data.state.includes('running')) {
                setIsRunning(true);
                // A fresh ROOT-level run is starting. Wipe any leftover
                // sub-agent indicators from previous runs in this session
                // (or from the previous session if the user just switched
                // before the reset propagated). Sub-agents (depth > 0) must
                // NOT trigger this reset, otherwise they'd wipe their own
                // siblings mid-flight.
                const depth = data.depth ?? 0;
                if (depth === 0) {
                  setActiveAgents([]);
                  activeAgentsRef.current = [];
                }
              }
              if (
                data.state.includes('done') ||
                data.state === 'failed' ||
                data.state === 'cancelled' ||
                data.state === 'budget_exceeded'
              ) {
                streamingItemIdRef.current = null;
                setIsRunning(false);
                // Clear all active agents when session finishes
                setActiveAgents([]);
                // Defensive sweep: when the run terminates, any leftover
                // tool_start / subagent_start chat items mean their matching
                // *_end event was dropped (queue backpressure, handler raised
                // before emitting it, etc.) — convert them so the cards stop
                // spinning. Mirrors the orphan cleanup the restore-on-reload
                // path already does for persisted history.
                setChatItems((prev) => {
                  let mutated = false;
                  const next = prev.map((it) => {
                    if (it.type === 'tool_start') {
                      mutated = true;
                      return { ...it, type: 'tool_end' as const };
                    }
                    if (it.type === 'subagent_start') {
                      mutated = true;
                      return { ...it, type: 'subagent_end' as const };
                    }
                    return it;
                  });
                  return mutated ? next : prev;
                });
                // Same for the inline tasks card: any task left in_progress
                // when the run ended would otherwise spin forever. Flip them
                // back to pending so the user can re-trigger or close them.
                setTasks((prev) => {
                  let mutated = false;
                  const next = prev.map((t) => {
                    if (t.status === 'in_progress') {
                      mutated = true;
                      return { ...t, status: 'pending' as const };
                    }
                    return t;
                  });
                  return mutated ? next : prev;
                });
                // Pull the now-final state into the experiments array so
                // non-active sidebar rows reflect "done"/"failed"/"cancelled"
                // without waiting on the user to trigger an unrelated refresh.
                void refreshExperiments();
              }
              if (data.state.endsWith('_done')) {
                const stageName = data.state.replace('_done', '');
                if (stageName !== 'chat') {
                  addItem({ type: 'stage_complete', content: stageName.toUpperCase() });
                }
              }
              break;
            }
            case 'agent_token':
            case 'agent_message': {
              const data = event.data;
              // Prefer the agent_type carried on the event itself — the
              // backend stamps it via agent_meta in save_and_publish, so it's
              // always the authoritative source for which agent produced the
              // text. Fall back to the activeAgents heuristic only when the
              // event is missing the field (legacy events).
              const eventAgentType = data.agent_type || undefined;
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
            case 'tool_start': {
              const data = event.data;
              streamingItemIdRef.current = null;
              addItem({ type: 'tool_start', content: data.tool, meta: data.input });
              break;
            }
            case 'tool_end': {
              const data = event.data;
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
            }
            case 'code_output': {
              const data = event.data;
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
            }
            case 'agent_error': {
              const data = event.data;
              streamingItemIdRef.current = null;
              addItem({ type: 'error', content: data.error });
              setIsRunning(false);
              break;
            }
            case 'usage_event': {
              const ev = event.data;
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
            case 'report_ready': {
              const data = event.data;
              setCanvasContent(data.content);
              setCanvasTitle(`${(data.stage || 'EDA').toUpperCase()} Report`);
              openCanvas();
              break;
            }
            case 'files_ready': {
              const data = event.data;
              const stage = data.stage || '';
              const newFiles = data.files || [];
              setGeneratedFiles((prev) => {
                const existingPaths = new Set(prev.map((f) => f.path));
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
              // Same auto-open contract as file_created — some agent stages
              // emit only the batch (files_ready) at end-of-stage without
              // per-file file_created events, so we open here too. openCanvas
              // is idempotent (no-op if already open) and the picker only
              // runs when no tab is active.
              if (newFiles.length > 0) openCanvas();
              break;
            }
            case 'file_created': {
              const data = event.data;
              const stage = data.stage || '';
              setFileTree((prev) =>
                insertNodeIntoTree(
                  prev,
                  {
                    name: data.name,
                    path: data.path,
                    type: 'file',
                  },
                  `/sessions/${sid}`,
                  stage,
                ),
              );
              // The ONLY auto-open trigger. Sending a message no longer
              // forces the canvas open just because the agent reports back —
              // we wait for an actual file to land. openCanvas() dispatches
              // 'trainable:canvas-opened' which kicks the picker so the new
              // file (or notebook / report / metrics) gets surfaced.
              openCanvas();
              break;
            }
            case 'agent_aborted':
              streamingItemIdRef.current = null;
              addItem({ type: 'status', content: 'Agent stopped' });
              setIsRunning(false);
              break;
            case 'session_resumed': {
              // Backend relaunched the agent with recovered progress (resume/
              // retry endpoint). The subsequent state_change → *_running event
              // flips isRunning too, but set it eagerly so the UI reacts even
              // if that event races the reconnect.
              const data = event.data;
              streamingItemIdRef.current = null;
              addItem({
                type: 'status',
                content:
                  data.mode === 'retry'
                    ? 'Retrying — continuing from recovered progress'
                    : 'Resuming — continuing from recovered progress',
              });
              setIsRunning(true);
              break;
            }
            case 'budget_exceeded': {
              // Hard-stop guardrail (#107): the runner halted the agent
              // because project spend crossed its cap.
              const data = event.data;
              streamingItemIdRef.current = null;
              addItem({ type: 'error', content: data.error });
              setBudgetInfo({
                project_id: data.project_id,
                budget_usd: data.budget_usd ?? null,
                spent_usd: data.spent_usd ?? 0,
                remaining_usd: 0,
                exceeded: true,
              });
              setIsRunning(false);
              break;
            }
            case 'metrics_batch': {
              const data = event.data;
              const items = data.items || [];
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
              const data = event.data;
              const key = `${data.step}:${data.name}:${data.run_tag || ''}`;
              if (!metricKeysRef.current.has(key)) {
                metricKeysRef.current.add(key);
                setMetricPoints((prev) => {
                  if (prev.length === 0) openCanvas();
                  return [
                    ...prev,
                    {
                      step: data.step,
                      name: data.name,
                      value: data.value,
                      stage: data.stage,
                      run_tag: data.run_tag || null,
                      created_at: new Date().toISOString(),
                    },
                  ];
                });
              }
              break;
            }
            case 'chart_config': {
              const data = event.data;
              if (data.charts && Array.isArray(data.charts)) {
                setChartConfig({ charts: data.charts });
              }
              break;
            }
            case 'log_event': {
              // Rich (non-scalar) panel payload — image grid, table,
              // confusion matrix, etc. Keyed by (key, step) so a backend
              // resend or reload-hydrate doesn't double-append. Fields are
              // optional on the wire (unvalidated backend payload), hence
              // the defensive checks below even though we have a real type.
              const ev = event.data;
              if (!ev || !ev.key || ev.step === undefined || !ev.type) break;
              const dedupKey = `${ev.key}:${ev.step}:${ev.run_tag || ''}`;
              if (logEventKeysRef.current.has(dedupKey)) break;
              logEventKeysRef.current.add(dedupKey);
              const entry: LogEvent = {
                step: Number(ev.step),
                key: String(ev.key),
                type: ev.type,
                stage: ev.stage,
                run_tag: ev.run_tag || null,
                payload: ev.data || {},
              };
              setLogEvents((prev) => {
                if (prev.length === 0) openCanvas();
                return [...prev, entry];
              });
              break;
            }
            case 'canvas_html': {
              // Agent published a self-contained HTML artifact. Overwrite
              // by key so regeneration reuses the tab. Open the canvas and
              // ask the WorkspaceCanvas to open/focus the tab. Fields are
              // optional on the wire, hence the defensive checks.
              const ev = event.data;
              if (!ev || !ev.key || !ev.path) break;
              const artifact: HtmlArtifact = {
                key: String(ev.key),
                title: String(ev.title || ev.key),
                path: String(ev.path),
                size: typeof ev.size === 'number' ? ev.size : null,
                ts: typeof ev.ts === 'number' ? ev.ts : null,
                step: typeof ev.step === 'number' ? ev.step : null,
                stage: ev.stage || null,
              };
              setHtmlArtifacts((prev) => {
                const next = new Map(prev);
                next.set(artifact.key, artifact);
                return next;
              });
              openCanvas();
              window.dispatchEvent(
                new CustomEvent('trainable:open-html-tab', {
                  detail: { key: artifact.key, title: artifact.title },
                }),
              );
              break;
            }
            // Multi-agent events
            case 'subagent_start': {
              const data = event.data;
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
              const data = event.data;
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
              const data = event.data;
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
              const data = event.data;
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
            // Structured EDA findings (issue #111) — canvas action cards.
            case 'eda_findings': {
              appendEdaFindings(event.data.findings);
              break;
            }
            // HITL approval gate (issue #108): agent posted a consequential
            // decision and is blocked until the user approves or edits it.
            case 'approval_request': {
              const data = event.data;
              addItem({
                type: 'approval',
                content: data.content || '',
                meta: {
                  approval_id: data.approval_id,
                  title: data.title,
                  kind: data.kind,
                  context: data.context,
                  asker_agent_id: data.asker_agent_id,
                  asker_agent_type: data.asker_agent_type,
                  depth: data.depth,
                  status: 'pending',
                },
              });
              break;
            }
            case 'approval_resolved': {
              const data = event.data;
              const aid = data.approval_id;
              setChatItems((prev) =>
                prev.map((it) =>
                  it.type === 'approval' && it.meta?.approval_id === aid
                    ? {
                        ...it,
                        meta: {
                          ...it.meta,
                          status: 'resolved',
                          decision: data.decision,
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
              const data = event.data;
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
              const data = event.data;
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
              const path = event.data.notebook_path;
              if (path) {
                openCanvas();
                window.dispatchEvent(new CustomEvent('trainable:open-file', { detail: { path } }));
              }
              break;
            }
            // Lineage events from agent-declared experiment lifecycle.
            // Notify the WorkspaceCanvas so its lineage tab refetches; on
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
                // No openCanvas() — only file_created auto-opens the canvas.
                // If the canvas is already open (e.g. files have landed), the
                // open-lineage-tab event still switches to the lineage view.
                window.dispatchEvent(new CustomEvent('trainable:open-lineage-tab'));
              }
              break;
            }
            // Tasks live to-do list — agent calls (add/update/delete) and
            // user REST CRUD both publish these events. The card is rendered
            // ONCE at the bottom of the chat (not inline per event), so we
            // only update the tasks state here.
            case 'task_created':
            case 'task_updated': {
              const t = event.data;
              setTasks((prev) => {
                const idx = prev.findIndex((x) => x.id === t.id);
                if (idx >= 0) {
                  const next = [...prev];
                  next[idx] = t;
                  return next;
                }
                return [...prev, t];
              });
              break;
            }
            case 'task_deleted': {
              const id = event.data.id;
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
    // INVARIANT: every dep here must be referentially stable, because
    // `connectSSE` is itself a dep of the session-load effect below — a new
    // identity would re-run that effect, tearing down the EventSource and
    // reloading the session. Verified stable: `addItem` (useCallback []),
    // `appendEdaFindings` (useCallback []), `openCanvas` (page-level
    // useCallback [], per the options contract), `publish` (SSEStreamContext
    // useCallback []), `refreshExperiments` (AppContext useCallback []),
    // `setIsRunning` (raw useState setter).
    // If you add a dep, keep it stable or the invariant breaks silently.
    [addItem, appendEdaFindings, openCanvas, publish, refreshExperiments, setIsRunning],
  );

  // ---------------------------------------------------------------------------
  // Reset state when active experiment changes
  // ---------------------------------------------------------------------------

  const resetSessionState = useCallback(() => {
    setChatItems([]);
    onReset();
    setIsRunning(false);
    streamingItemIdRef.current = null;
    pinnedToBottomRef.current = true;
    setSessionState('created');
    collapseCanvas();
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
    setLogEvents([]);
    logEventKeysRef.current = new Set();
    setHtmlArtifacts(new Map());
    setEdaFindings([]);
    edaFindingKeysRef.current = new Set();
    // Critical: clear per-session agent indicators. If we don't, the previous
    // session's running sub-agents leak into the new one and `agent_message`
    // events get mis-tagged with the wrong agent_type (the stale entry from
    // the previous session). Reset both the state AND the ref synchronously
    // so the SSE handler closure sees an empty list immediately.
    setActiveAgents([]);
    activeAgentsRef.current = [];
    setUsageTotals(ZERO_USAGE);
    setRecentUsage([]);
    setBudgetInfo(null);
    setTasks([]);
  }, [collapseCanvas, onReset, setIsRunning]);

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
          setBudgetInfo(s.budget ?? null);
        })
        .catch(() => {
          /* historical usage is best-effort; live SSE will fill in */
        });

      try {
        await api.getExperiment(activeExperimentId);
        if (cancelled) return;

        const sid = activeSessionId;
        const sessionData = await api.getSession(sid);
        if (cancelled) return;

        // Reconstruct chat from saved messages
        const restored: ChatItem[] = [];
        let restoredCanvasContent = '';
        let restoredCanvasTitle = 'Report';
        let restoredCanvasOpen = false;
        let restoredFiles: (GeneratedFile & { _stage?: string })[] = [];
        const restoredHtmlArtifacts = new Map<string, HtmlArtifact>();
        const restoredEdaFindings: unknown[] = [];

        if (sessionData.messages?.length > 0) {
          // Events persisted for introspection/telemetry only — never rendered as bubbles.
          const NON_VISIBLE_EVENTS = new Set([
            'agent_thought',
            'file_created',
            'files_ready',
            'metric',
            'metrics_batch',
            'chart_config',
            'log_event',
            'canvas_html',
            'validation_result',
            's3_sync_complete',
            'metadata_ready',
          ]);

          for (const msg of sessionData.messages) {
            const eventType = msg.metadata?.event_type as string | undefined;
            // Capture canvas_html (persisted as system Messages) before
            // the NON_VISIBLE skip so reload restores the canvas tabs.
            if (eventType === 'canvas_html') {
              const m = msg.metadata || {};
              if (m.key && m.path) {
                restoredHtmlArtifacts.set(String(m.key), {
                  key: String(m.key),
                  title: String(m.title || m.key),
                  path: String(m.path),
                  size: typeof m.size === 'number' ? m.size : null,
                  ts: typeof m.ts === 'number' ? m.ts : null,
                  step: typeof m.step === 'number' ? m.step : null,
                  stage: (m.stage as string | null) || null,
                });
              }
              continue;
            }
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
                  meta: { code: metaStr((msg.metadata?.input as { code?: unknown })?.code) },
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
                    output: metaStr(msg.metadata?.output),
                    duration: metaNum(msg.metadata?.duration) || null,
                  },
                };
              } else {
                restored.push(
                  mkItem({
                    type: 'tool_end',
                    content: (msg.metadata?.tool as string) || 'execute_code',
                    meta: { output: metaStr(msg.metadata?.output) },
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
                      {
                        text: msg.content || metaStr(msg.metadata?.text) || '',
                        stream: metaStr(msg.metadata?.stream),
                      },
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
              const newFiles = (msg.metadata?.files || []) as GeneratedFile[];
              const existingPaths = new Set(restoredFiles.map((f) => f.path));
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
                    task: metaStr(msg.metadata?.task) || metaStr(msg.metadata?.description) || '',
                    model: metaStr(msg.metadata?.model) || '',
                    depth: metaNum(msg.metadata?.depth) || 1,
                    agent_id: metaStr(msg.metadata?.agent_id) || '',
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
                    summary: metaStr(msg.metadata?.summary) || metaStr(msg.metadata?.result) || '',
                    duration: metaNum(msg.metadata?.duration) || null,
                  },
                };
              } else {
                restored.push(
                  mkItem({
                    type: 'subagent_end',
                    content: (msg.metadata?.agent_type as string) || 'sub-agent',
                    meta: {
                      summary:
                        metaStr(msg.metadata?.summary) || metaStr(msg.metadata?.result) || '',
                      duration: metaNum(msg.metadata?.duration) || null,
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
                    call_id: metaStr(msg.metadata?.call_id),
                    tool_name: metaStr(msg.metadata?.tool_name),
                    asker_agent_type: metaStr(msg.metadata?.asker_agent_type),
                    target_agent_type: metaStr(msg.metadata?.target_agent_type),
                    answerer_agent_type: metaStr(msg.metadata?.answerer_agent_type),
                    depth: metaNum(msg.metadata?.depth) || 0,
                    duration_s: metaNum(msg.metadata?.duration_s),
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
                    call_id: metaStr(msg.metadata?.call_id),
                    tool_name: 'request_clarification',
                    asker_agent_type: metaStr(msg.metadata?.asker_agent_type),
                    answerer_agent_type: metaStr(msg.metadata?.answerer_agent_type),
                    depth: metaNum(msg.metadata?.depth) || 0,
                    duration_s: metaNum(msg.metadata?.duration_s),
                    variant: 'clarification_exchange',
                  },
                }),
              );
            } else if (eventType === 'eda_findings') {
              // Structured EDA findings (issue #111) — restored into the
              // canvas cards, never rendered as a chat bubble.
              const items = msg.metadata?.findings;
              if (Array.isArray(items)) restoredEdaFindings.push(...items);
            } else if (eventType === 'approval_request') {
              // Restore the approval card (pending until a matching
              // approval_resolved row flips it). Critical for reload-mid-gate:
              // the backend agent stays blocked on the approval future, so
              // the card must come back actionable or the session stalls
              // until the approval window times out.
              restored.push(
                mkItem({
                  type: 'approval',
                  content: msg.content || '',
                  meta: {
                    approval_id: metaStr(msg.metadata?.approval_id),
                    title: metaStr(msg.metadata?.title),
                    kind: metaStr(msg.metadata?.kind),
                    context: metaStr(msg.metadata?.context),
                    asker_agent_id: metaStr(msg.metadata?.asker_agent_id),
                    asker_agent_type: metaStr(msg.metadata?.asker_agent_type),
                    depth: metaNum(msg.metadata?.depth),
                    status: 'pending',
                  },
                }),
              );
            } else if (eventType === 'approval_resolved') {
              const aid = metaStr(msg.metadata?.approval_id);
              const idx = restored.findLastIndex(
                (i) => i.type === 'approval' && i.meta?.approval_id === aid,
              );
              if (idx >= 0) {
                restored[idx] = {
                  ...restored[idx],
                  meta: {
                    ...restored[idx].meta,
                    status: 'resolved',
                    decision: msg.metadata?.decision as
                      'approve' | 'edit' | 'timeout' | 'cancelled' | undefined,
                    answer: metaStr(msg.metadata?.answer),
                    answered_by: metaStr(msg.metadata?.answered_by),
                  },
                };
              }
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
        appendEdaFindings(restoredEdaFindings);
        setCanvasContent(restoredCanvasContent);
        setCanvasTitle(restoredCanvasTitle);
        if (restoredHtmlArtifacts.size > 0) {
          setHtmlArtifacts(restoredHtmlArtifacts);
        }
        if (restoredCanvasOpen || restoredHtmlArtifacts.size > 0) {
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

        // Load historical rich-log events (image grids, tables, …)
        api
          .getLogEvents(sid)
          .then((events) => {
            if (!cancelled && events.length > 0) {
              setLogEvents(events);
              openCanvas();
              for (const e of events) {
                logEventKeysRef.current.add(`${e.key}:${e.step}:${e.run_tag || ''}`);
              }
            }
          })
          .catch((e) => console.error('Failed to load historical log events', e));

        // Load existing tasks for this session. The card is rendered
        // at the bottom of the chat from `tasks` state, so we just
        // hydrate it — no chat-stream entry needed.
        api
          .getTasks(sid)
          .then((rows) => {
            if (cancelled) return;
            setTasks(rows);
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
  }, [
    activeExperimentId,
    activeSessionId,
    connectSSE,
    addItem,
    appendEdaFindings,
    resetSessionState,
    openCanvas,
    setIsRunning,
  ]);

  return {
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
    edaFindings,
    metricPoints,
    chartConfig,
    logEvents,
    usageTotals,
    recentUsage,
    budgetInfo,
    activeAgents,
  };
}
