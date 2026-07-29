'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  BarChart3,
  ChevronRight,
  Code2,
  FileText,
  FolderOpen,
  GitBranch,
  Globe,
  ListChecks,
  X,
} from 'lucide-react';
import { api } from '@/lib/api';
import type {
  ChartConfig,
  EdaFinding,
  FileTreeNode,
  GeneratedFile,
  HtmlArtifact,
  LineageGraph as LineageGraphPayload,
  LineageNode,
  LogEvent,
  MetricPoint,
} from '@/lib/types';
import { countFiles, fileBreadcrumb } from '@/lib/useFileTree';
import LineageGraph from '@/components/lineage/LineageGraph';
import NodeMetadataPanel from '@/components/lineage/NodeMetadataPanel';
import FileTreeRow from '@/components/workspace/FileTreeRow';
import FileViewer from '@/components/workspace/FileViewer';
import HtmlPanel from '@/components/workspace/HtmlPanel';
import { EdaFindingsPanel } from '@/components/workspace/EdaFindingsPanel';
import MetricsPanel from '@/components/workspace/MetricsPanel';
import ReportMarkdown from '@/components/workspace/ReportMarkdown';
import { getFileIconInfo } from '@/components/workspace/fileIcons';

// ---------------------------------------------------------------------------
// Workspace Panel -- github.dev-style: tree sidebar + tabbed editor
// (formerly `WorkspaceSidebar` inside page.tsx)
// ---------------------------------------------------------------------------

interface OpenTab {
  id: string;
  label: string;
  icon: typeof FileText;
  iconColor: string;
  type: 'file' | 'report' | 'metrics' | 'lineage' | 'html' | 'findings';
  /** For type='html': the artifact key (also the suffix of the tab id). */
  htmlKey?: string;
}

const REPORT_TAB_ID = '__report__';
const METRICS_TAB_ID = '__metrics__';
const LINEAGE_TAB_ID = '__lineage__';
const FINDINGS_TAB_ID = '__findings__';
const HTML_TAB_PREFIX = '__html__:';

export default function WorkspaceCanvas({
  experimentId,
  sessionId,
  canvasContent,
  canvasTitle,
  generatedFiles,
  fileTree,
  metricPoints,
  chartConfig,
  logEvents,
  htmlArtifacts,
  edaFindings,
  onApplyInPrep,
  sessionState,
  onClose,
}: {
  experimentId: string;
  sessionId: string;
  canvasContent: string;
  canvasTitle: string;
  generatedFiles: GeneratedFile[];
  fileTree: FileTreeNode;
  metricPoints: MetricPoint[];
  chartConfig: ChartConfig | null;
  logEvents: LogEvent[];
  htmlArtifacts: Map<string, HtmlArtifact>;
  /** Structured EDA findings (issue #111) — rendered as action cards. */
  edaFindings: EdaFinding[];
  /** "Apply in prep": pre-fill the chat input with a data_prep instruction. */
  onApplyInPrep: (finding: EdaFinding) => void;
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

  // Listen for "open html tab" — fired by the page-level canvas_html SSE
  // handler. Opens (or focuses) the tab for that artifact key. Regenerating
  // an artifact with the same key reuses the existing tab.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { key?: string; title?: string } | undefined;
      const key = detail?.key;
      if (!key) return;
      const tabId = HTML_TAB_PREFIX + key;
      setOpenTabs((prev) => {
        const existing = prev.find((t) => t.id === tabId);
        if (existing) {
          if (detail?.title && existing.label !== detail.title) {
            return prev.map((t) => (t.id === tabId ? { ...t, label: detail.title! } : t));
          }
          return prev;
        }
        return [
          ...prev,
          {
            id: tabId,
            label: detail?.title || key,
            icon: Globe,
            iconColor: 'text-fuchsia-400',
            type: 'html',
            htmlKey: key,
          },
        ];
      });
      setActiveTabId(tabId);
    };
    window.addEventListener('trainable:open-html-tab', handler as EventListener);
    return () => window.removeEventListener('trainable:open-html-tab', handler as EventListener);
  }, []);

  // When session reload hydrates htmlArtifacts in bulk, materialize the
  // tabs on mount (no SSE event will fire for them retroactively).
  useEffect(() => {
    if (htmlArtifacts.size === 0) return;
    setOpenTabs((prev) => {
      const present = new Set(prev.map((t) => t.id));
      const additions: OpenTab[] = [];
      for (const a of Array.from(htmlArtifacts.values())) {
        const id = HTML_TAB_PREFIX + a.key;
        if (!present.has(id)) {
          additions.push({
            id,
            label: a.title || a.key,
            icon: Globe,
            iconColor: 'text-fuchsia-400',
            type: 'html',
            htmlKey: a.key,
          });
        }
      }
      return additions.length > 0 ? [...prev, ...additions] : prev;
    });
  }, [htmlArtifacts]);

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

  // Auto-open the findings tab when structured EDA findings arrive. Only
  // steals focus when no tab is active — same contract as report/metrics.
  const hasFindings = edaFindings.length > 0;
  useEffect(() => {
    if (hasFindings) {
      setOpenTabs((prev) => {
        if (prev.find((t) => t.id === FINDINGS_TAB_ID)) return prev;
        return [
          ...prev,
          {
            id: FINDINGS_TAB_ID,
            label: 'Findings',
            icon: ListChecks,
            iconColor: 'text-amber-400',
            type: 'findings',
          },
        ];
      });
      setActiveTabId((prev) => prev || FINDINGS_TAB_ID);
    }
  }, [hasFindings]);

  // Auto-open metrics tab when the first metric OR rich log payload arrives
  const hasMetrics = metricPoints.length > 0 || logEvents.length > 0;
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
    // Prefer the most recent HTML artifact — the agent went to the
    // trouble of authoring a visual showcase, so surface it on auto-open.
    if (htmlArtifacts.size > 0) {
      let latest: HtmlArtifact | null = null;
      for (const a of Array.from(htmlArtifacts.values())) {
        if (!latest || (a.ts || 0) > (latest.ts || 0)) latest = a;
      }
      if (latest) {
        const target = latest;
        return () =>
          window.dispatchEvent(
            new CustomEvent('trainable:open-html-tab', {
              detail: { key: target.key, title: target.title },
            }),
          );
      }
    }
    if (canvasContent) return openReportTab;
    if (metricPoints.length > 0 || logEvents.length > 0) return openMetricsTab;
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
    htmlArtifacts,
    canvasContent,
    metricPoints.length,
    logEvents.length,
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

  // Cold-open race: WorkspaceCanvas only mounts when canvasOpen flips
  // true, which happens AFTER openCanvas() dispatches 'trainable:canvas-
  // opened'. So on the first auto-open (file_created etc.) the listener
  // above isn't registered in time and the picker never runs. Run it
  // once on mount too — same guard, picks the right default tab.
  useEffect(() => {
    if (activeTabId) return;
    const action = pickDefaultTab();
    action?.();
    // Intentionally mount-only — re-running on activeTabId changes would
    // fight the user's explicit tab choices. The window listener above
    // handles subsequent canvas-open events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
                  <MetricsPanel
                    metricPoints={metricPoints}
                    chartConfig={chartConfig}
                    logEvents={logEvents}
                    sessionState={sessionState}
                  />
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
                ) : tab.type === 'html' ? (
                  <HtmlPanel artifact={tab.htmlKey ? htmlArtifacts.get(tab.htmlKey) : undefined} />
                ) : tab.type === 'findings' ? (
                  <EdaFindingsPanel findings={edaFindings} onApplyInPrep={onApplyInPrep} />
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
