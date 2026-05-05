'use client';

import { useState, useRef, useEffect } from 'react';
import {
  ChevronDown,
  Loader2,
  CheckCircle2,
  XCircle,
  Circle,
  Settings,
  RotateCcw,
  ArrowLeft,
  Wand2,
} from 'lucide-react';
import { useApp } from '@/lib/AppContext';

export interface ActiveAgent {
  id: string;
  type: string;
  status: 'running' | 'completed' | 'failed';
  task?: string;
  depth: number;
  startedAt: number;
}

const AGENT_META: Record<string, { label: string; color: string; description: string }> = {
  orchestrator: {
    label: 'Orchestrator',
    color: 'violet',
    description: 'Plans the workflow, delegates to specialists',
  },
  eda: {
    label: 'EDA Agent',
    color: 'blue',
    description: 'Exploratory data analysis, profiling, visualization',
  },
  data_prep: {
    label: 'Data Prep Agent',
    color: 'amber',
    description: 'Cleans, transforms, splits data',
  },
  feature_eng: {
    label: 'Feature Eng. Agent',
    color: 'orange',
    description: 'Creates and selects features',
  },
  trainer: { label: 'Training Agent', color: 'green', description: 'Trains and tunes models' },
  reviewer: {
    label: 'Review Agent',
    color: 'rose',
    description: 'Catches bugs, data leakage, methodology issues',
  },
  chat: {
    label: 'Chat Agent',
    color: 'gray',
    description: 'Root conversational agent (always main)',
  },
  deploy: {
    label: 'Deploy Agent',
    color: 'emerald',
    description: 'Generates Modal serving apps + ships models to production',
  },
  research: {
    label: 'Research Agent',
    color: 'teal',
    description: 'Reads PDFs + arxiv, extracts training recipes, briefs specialists',
  },
  data_search: {
    label: 'Data Search Agent',
    color: 'teal',
    description: 'Finds where datasets and papers live (HF Hub, Kaggle, arxiv) — never downloads',
  },
};

// Order matches the natural ML pipeline so the picker reads
// chat → orchestrator → EDA → prep → feat eng → train → review →
// deploy. Adding a new agent? Drop it here AND in
// AGENT_TYPE_INFO above so it gets a colour + description.
const ALL_AGENT_TYPES = [
  'chat',
  'orchestrator',
  'research',
  'data_search',
  'eda',
  'data_prep',
  'feature_eng',
  'trainer',
  'reviewer',
  'deploy',
];

const COLOR_MAP: Record<string, { bg: string; text: string; dot: string; ring: string }> = {
  violet: {
    bg: 'bg-violet-500/15',
    text: 'text-violet-400',
    dot: 'bg-violet-400',
    ring: 'ring-violet-400/30',
  },
  blue: {
    bg: 'bg-blue-500/15',
    text: 'text-blue-400',
    dot: 'bg-blue-400',
    ring: 'ring-blue-400/30',
  },
  amber: {
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    dot: 'bg-amber-400',
    ring: 'ring-amber-400/30',
  },
  green: {
    bg: 'bg-green-500/15',
    text: 'text-green-400',
    dot: 'bg-green-400',
    ring: 'ring-green-400/30',
  },
  orange: {
    bg: 'bg-orange-500/15',
    text: 'text-orange-400',
    dot: 'bg-orange-400',
    ring: 'ring-orange-400/30',
  },
  rose: {
    bg: 'bg-rose-500/15',
    text: 'text-rose-400',
    dot: 'bg-rose-400',
    ring: 'ring-rose-400/30',
  },
  gray: {
    bg: 'bg-gray-500/15',
    text: 'text-gray-400',
    dot: 'bg-gray-500',
    ring: 'ring-gray-400/30',
  },
  teal: {
    bg: 'bg-teal-500/15',
    text: 'text-teal-400',
    dot: 'bg-teal-400',
    ring: 'ring-teal-400/30',
  },
  emerald: {
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    dot: 'bg-emerald-400',
    ring: 'ring-emerald-400/30',
  },
};

function getAgentMeta(type: string) {
  return (
    AGENT_META[type] || {
      label: type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      color: 'teal',
      description: '',
    }
  );
}

function getColors(color: string) {
  return COLOR_MAP[color] || COLOR_MAP.teal;
}

interface Props {
  agents: ActiveAgent[];
  isRunning: boolean;
}

type View = 'status' | 'config';

export default function AgentStatusIndicator({ agents, isRunning }: Props) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<View>('status');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setView('status');
      }
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Find the deepest running agent (the one currently active)
  const runningAgents = agents.filter((a) => a.status === 'running');
  const currentAgent = runningAgents.length > 0 ? runningAgents[runningAgents.length - 1] : null;

  const displayType = currentAgent?.type || 'chat';
  const meta = getAgentMeta(displayType);
  const colors = getColors(meta.color);
  const anyRunning = isRunning || runningAgents.length > 0;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => {
          setOpen(!open);
          setView('status');
        }}
        className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg transition-colors text-xs ${
          anyRunning ? `${colors.bg} ${colors.text}` : 'hover:bg-white/[0.06] text-gray-400'
        }`}
      >
        <span className="relative flex items-center justify-center w-3 h-3">
          <span className={`w-2 h-2 rounded-full ${anyRunning ? colors.dot : 'bg-gray-600'}`} />
          {anyRunning && (
            <span
              className={`absolute w-3 h-3 rounded-full ${colors.dot} opacity-40 animate-ping`}
            />
          )}
        </span>
        <span className="font-medium">{meta.label}</span>
        <ChevronDown
          className={`w-3 h-3 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute top-full right-0 mt-1 w-72 bg-black border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
          {view === 'status' ? (
            <StatusView
              agents={agents}
              anyRunning={anyRunning}
              currentAgent={currentAgent}
              onOpenConfig={() => setView('config')}
            />
          ) : (
            <ConfigView onBack={() => setView('status')} />
          )}
        </div>
      )}
    </div>
  );
}

/* ---------- STATUS VIEW (active agents list) ---------- */

function StatusView({
  agents,
  anyRunning,
  currentAgent,
  onOpenConfig,
}: {
  agents: ActiveAgent[];
  anyRunning: boolean;
  currentAgent: ActiveAgent | null;
  onOpenConfig: () => void;
}) {
  return (
    <>
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/[0.06]">
        <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
          Agent Stack
        </span>
        <button
          onClick={onOpenConfig}
          className="p-1 rounded-md hover:bg-white/[0.08] transition-colors text-gray-500 hover:text-gray-300"
          title="Configure agent models"
        >
          <Settings className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="py-1 max-h-[320px] overflow-y-auto">
        {/* Root agent (chat) — always show */}
        <div className="flex items-center gap-2 px-3 py-1.5">
          <StatusIcon
            status={
              anyRunning && !currentAgent ? 'running' : agents.length > 0 ? 'idle' : 'running'
            }
            color="gray"
          />
          <span className="text-xs text-gray-400 flex-1">Chat Agent</span>
          <span className="text-[10px] text-gray-600">main</span>
        </div>

        {agents.length === 0 && !anyRunning && (
          <div className="px-3 py-2 text-[11px] text-gray-600">No sub-agents active.</div>
        )}

        {agents.map((agent) => {
          const am = getAgentMeta(agent.type);
          const ac = getColors(am.color);
          return (
            <div
              key={agent.id}
              className="flex items-center gap-2 px-3 py-1.5"
              style={{ paddingLeft: `${12 + agent.depth * 12}px` }}
            >
              <StatusIcon status={agent.status} color={am.color} />
              <span
                className={`text-xs flex-1 ${agent.status === 'running' ? ac.text : 'text-gray-500'}`}
              >
                {am.label}
              </span>
              {agent.status === 'running' && <Elapsed since={agent.startedAt} />}
              {agent.status === 'completed' && (
                <span className="text-[10px] text-gray-600">done</span>
              )}
              {agent.status === 'failed' && (
                <span className="text-[10px] text-red-400">failed</span>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ---------- CONFIG VIEW (per-agent model overrides) ---------- */

function ConfigView({ onBack }: { onBack: () => void }) {
  const { models, providers, agentModels, setAgentModel, agentThinking, setAgentThinking } =
    useApp();

  // Per-agent provider filter — UI-only, not persisted: it just narrows the
  // model dropdown. The actual model id is what gets sent to the backend.
  const [providerFilter, setProviderFilter] = useState<Record<string, string>>({});

  // Map provider id → availability info from /api/providers. A provider is
  // selectable only when both flags are true:
  //   - `available`        — API key / OAuth present
  //   - `runner_supported` — agent runner can actually dispatch to it
  // The frontend tooltips explain whichever is missing first.
  type Avail = { available: boolean; missing_env: string[]; runner_supported: boolean };
  const availabilityById: Record<string, Avail> = {};
  for (const p of providers) {
    availabilityById[p.id] = {
      available: p.available,
      missing_env: p.missing_env,
      runner_supported: p.runner_supported,
    };
  }
  // Fallback when /api/providers hasn't responded yet — be permissive so
  // we don't gray out everything on load.
  const getAvail = (pid: string): Avail =>
    availabilityById[pid] ?? { available: true, missing_env: [], runner_supported: true };
  const isProviderUsable = (pid: string) => {
    const a = getAvail(pid);
    return a.available && a.runner_supported;
  };
  /** Reason a provider is unusable, or null if it's fine. */
  const unusableReason = (pid: string): string | null => {
    const a = getAvail(pid);
    if (!a.available) {
      return `${pid} is not configured — set ${a.missing_env.join(' or ')} in .env`;
    }
    if (!a.runner_supported) {
      return `${pid} not yet supported by the agent runner — only Claude works today`;
    }
    return null;
  };

  // Stable list of providers seen in the catalog. Sorted with usable ones
  // first so the picker leads with what the user can actually run.
  const allProviders = Array.from(new Set(models.map((m) => m.provider).filter(Boolean)));
  allProviders.sort((a, b) => {
    const av = isProviderUsable(a) ? 0 : 1;
    const bv = isProviderUsable(b) ? 0 : 1;
    return av - bv || a.localeCompare(b);
  });

  // "Set all" bulk control. The dropdown reflects the shared override only
  // when every agent has the same model id set; otherwise it shows blank
  // (= per-agent / mixed). Picking a model writes it to every agent and
  // clears thinking so each picks up the new model's default.
  const overrideValues = ALL_AGENT_TYPES.map((t) => agentModels[t] || '');
  const allSame = overrideValues.every((v) => v === overrideValues[0]);
  const bulkValue = allSame ? overrideValues[0] : '';
  const bulkSelected = bulkValue ? models.find((m) => m.id === bulkValue) : undefined;
  const [bulkProvider, setBulkProvider] = useState<string>('');
  const activeBulkProvider = bulkProvider || bulkSelected?.provider || '';
  const filteredBulkModels = activeBulkProvider
    ? models.filter((m) => m.provider === activeBulkProvider)
    : models;
  const anyOverride =
    overrideValues.some((v) => v) || ALL_AGENT_TYPES.some((t) => agentThinking[t]);

  const applyToAll = (modelId: string) => {
    ALL_AGENT_TYPES.forEach((t) => {
      setAgentModel(t, modelId || null);
      setAgentThinking(t, null);
    });
    setProviderFilter({});
  };

  const resetAll = () => {
    ALL_AGENT_TYPES.forEach((t) => {
      setAgentModel(t, null);
      setAgentThinking(t, null);
    });
    setProviderFilter({});
    setBulkProvider('');
  };

  return (
    <>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/[0.06]">
        <button
          onClick={onBack}
          className="p-0.5 rounded-md hover:bg-white/[0.08] transition-colors text-gray-500 hover:text-gray-300"
          title="Back"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
        </button>
        <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold flex-1">
          Agent Models
        </span>
      </div>
      <div className="py-1 max-h-[480px] overflow-y-auto">
        <div className="px-3 py-2 text-[10px] text-gray-600 border-b border-white/[0.04]">
          Override the default model, provider, and reasoning level per agent. Persisted locally.
        </div>
        <div className="px-3 py-2 border-b border-white/[0.06] bg-white/[0.02]">
          <div className="flex items-center justify-between mb-1.5">
            <div className="flex items-center gap-1.5">
              <Wand2 className="w-3 h-3 text-violet-400" />
              <span className="text-[11px] font-medium text-gray-300">Set all agents</span>
              {!allSame && anyOverride && (
                <span
                  className="text-[10px] text-gray-600"
                  title="Agents currently have different overrides"
                >
                  · mixed
                </span>
              )}
            </div>
            {anyOverride && (
              <button
                onClick={resetAll}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-gray-500 hover:text-gray-300 hover:bg-white/[0.06] transition-colors"
                title="Clear all per-agent overrides"
              >
                <RotateCcw className="w-3 h-3" />
                Reset all
              </button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-1.5">
            <select
              value={activeBulkProvider}
              onChange={(e) => {
                const next = e.target.value;
                setBulkProvider(next);
                // If the current bulk model isn't in the new provider, clear all overrides
                // so the dropdown returns to "Default" — picking a new model below re-applies.
                if (next && bulkSelected && bulkSelected.provider !== next) {
                  applyToAll('');
                }
              }}
              className="text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
              title="Filter by provider"
            >
              <option value="">Any provider</option>
              {allProviders.map((p) => {
                const reason = unusableReason(p);
                const ok = !reason;
                const a = getAvail(p);
                const suffix = !a.runner_supported
                  ? ' · runner-only Claude'
                  : !a.available
                    ? ' · no key'
                    : '';
                return (
                  <option key={p} value={p} disabled={!ok} title={reason ?? undefined}>
                    {p}
                    {suffix}
                  </option>
                );
              })}
            </select>
            <select
              value={allSame ? bulkValue : ''}
              onChange={(e) => {
                const nextId = e.target.value;
                if (nextId) {
                  const m = models.find((x) => x.id === nextId);
                  if (m && !isProviderUsable(m.provider)) return;
                }
                applyToAll(nextId);
              }}
              className="text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
            >
              <option value="">
                {allSame ? 'Per-agent defaults' : 'Mixed — pick one to sync'}
              </option>
              {filteredBulkModels.map((m) => {
                const reason = unusableReason(m.provider);
                const ok = !reason;
                const a = getAvail(m.provider);
                const suffix = !a.runner_supported
                  ? ' · not yet supported'
                  : !a.available
                    ? ' · no key'
                    : '';
                return (
                  <option key={m.id} value={m.id} disabled={!ok} title={reason ?? undefined}>
                    {m.name}
                    {m.experimental ? ' (preview)' : ''}
                    {suffix}
                  </option>
                );
              })}
            </select>
          </div>
        </div>
        {ALL_AGENT_TYPES.map((agentType) => {
          const am = getAgentMeta(agentType);
          const ac = getColors(am.color);
          const override = agentModels[agentType];
          const selectedModel = models.find((m) => m.id === override);
          const activeProvider = providerFilter[agentType] || selectedModel?.provider || '';
          const filteredModels = activeProvider
            ? models.filter((m) => m.provider === activeProvider)
            : models;
          const thinkingSpec = selectedModel?.thinking;
          const thinkingValue = agentThinking[agentType] ?? thinkingSpec?.default ?? '';
          const overrideReason = selectedModel ? unusableReason(selectedModel.provider) : null;
          return (
            <div
              key={agentType}
              className="flex items-start gap-2 px-3 py-2 border-b border-white/[0.03] last:border-0"
            >
              <span className={`w-2 h-2 rounded-full ${ac.dot} shrink-0 mt-1.5`} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-xs font-medium ${ac.text}`}>{am.label}</span>
                  {(override || agentThinking[agentType]) && (
                    <button
                      onClick={() => {
                        setAgentModel(agentType, null);
                        setAgentThinking(agentType, null);
                        setProviderFilter((prev) => {
                          const next = { ...prev };
                          delete next[agentType];
                          return next;
                        });
                      }}
                      className="p-0.5 rounded hover:bg-white/[0.08] text-gray-600 hover:text-gray-400"
                      title="Reset to defaults"
                    >
                      <RotateCcw className="w-3 h-3" />
                    </button>
                  )}
                </div>
                {am.description && (
                  <p className="text-[10px] text-gray-600 mt-0.5 leading-tight">{am.description}</p>
                )}
                <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                  <select
                    value={activeProvider}
                    onChange={(e) => {
                      const next = e.target.value;
                      setProviderFilter((prev) => ({ ...prev, [agentType]: next }));
                      // If the currently-chosen model isn't in the new provider, clear it
                      if (next && selectedModel && selectedModel.provider !== next) {
                        setAgentModel(agentType, null);
                        setAgentThinking(agentType, null);
                      }
                    }}
                    className="text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
                    title="Filter by provider"
                  >
                    <option value="">Any provider</option>
                    {allProviders.map((p) => {
                      const reason = unusableReason(p);
                      const ok = !reason;
                      const a = getAvail(p);
                      const suffix = !a.runner_supported
                        ? ' · runner-only Claude'
                        : !a.available
                          ? ' · no key'
                          : '';
                      return (
                        <option key={p} value={p} disabled={!ok} title={reason ?? undefined}>
                          {p}
                          {suffix}
                        </option>
                      );
                    })}
                  </select>
                  <select
                    value={override || ''}
                    onChange={(e) => {
                      const nextId = e.target.value;
                      // Refuse to select an unusable model (the option is
                      // disabled, but be defensive).
                      if (nextId) {
                        const m = models.find((x) => x.id === nextId);
                        if (m && !isProviderUsable(m.provider)) return;
                      }
                      setAgentModel(agentType, nextId || null);
                      setAgentThinking(agentType, null);
                    }}
                    className="text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
                  >
                    <option value="">
                      Default{' '}
                      {agentType === 'reviewer' ? '· Claude Haiku 4.5' : '· Claude Sonnet 4.6'}
                    </option>
                    {filteredModels.map((m) => {
                      const reason = unusableReason(m.provider);
                      const ok = !reason;
                      const a = getAvail(m.provider);
                      const suffix = !a.runner_supported
                        ? ' · not yet supported'
                        : !a.available
                          ? ' · no key'
                          : '';
                      return (
                        <option key={m.id} value={m.id} disabled={!ok} title={reason ?? undefined}>
                          {m.name}
                          {m.experimental ? ' (preview)' : ''}
                          {suffix}
                        </option>
                      );
                    })}
                  </select>
                </div>
                {overrideReason && (
                  <p
                    className="mt-1 text-[10px] text-amber-400/80 leading-tight"
                    title={overrideReason}
                  >
                    {overrideReason}
                  </p>
                )}
                {thinkingSpec && thinkingSpec.levels.length > 0 && (
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span className="text-[10px] text-gray-500 shrink-0">Thinking</span>
                    <select
                      value={thinkingValue}
                      onChange={(e) => setAgentThinking(agentType, e.target.value || null)}
                      className="flex-1 text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
                      title="Reasoning effort"
                    >
                      {thinkingSpec.levels.map((lvl) => (
                        <option key={lvl} value={lvl}>
                          {lvl}
                          {lvl === thinkingSpec.default ? ' · default' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

export function StatusIcon({ status, color }: { status: string; color: string }) {
  const c = getColors(color);
  if (status === 'running') {
    return <Loader2 className={`w-3 h-3 ${c.text} animate-spin shrink-0`} />;
  }
  if (status === 'completed') {
    return <CheckCircle2 className="w-3 h-3 text-gray-600 shrink-0" />;
  }
  if (status === 'failed') {
    return <XCircle className="w-3 h-3 text-red-400 shrink-0" />;
  }
  return <Circle className="w-3 h-3 text-gray-700 shrink-0" />;
}

function Elapsed({ since }: { since: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed(Math.round((Date.now() - since) / 1000)), 1000);
    return () => clearInterval(id);
  }, [since]);
  if (elapsed < 1) return null;
  return <span className="text-[10px] text-gray-600 tabular-nums shrink-0">{elapsed}s</span>;
}
