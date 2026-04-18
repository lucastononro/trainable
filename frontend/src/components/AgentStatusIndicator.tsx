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
  orchestrator: { label: 'Orchestrator', color: 'violet', description: 'Plans the workflow, delegates to specialists' },
  eda: { label: 'EDA Agent', color: 'blue', description: 'Exploratory data analysis, profiling, visualization' },
  data_prep: { label: 'Data Prep Agent', color: 'amber', description: 'Cleans, transforms, splits data' },
  feature_eng: { label: 'Feature Eng. Agent', color: 'orange', description: 'Creates and selects features' },
  trainer: { label: 'Training Agent', color: 'green', description: 'Trains and tunes models' },
  reviewer: { label: 'Review Agent', color: 'rose', description: 'Catches bugs, data leakage, methodology issues' },
  chat: { label: 'Chat Agent', color: 'gray', description: 'Root conversational agent (always main)' },
};

const ALL_AGENT_TYPES = [
  'chat',
  'orchestrator',
  'eda',
  'data_prep',
  'feature_eng',
  'trainer',
  'reviewer',
];

const COLOR_MAP: Record<string, { bg: string; text: string; dot: string; ring: string }> = {
  violet: { bg: 'bg-violet-500/15', text: 'text-violet-400', dot: 'bg-violet-400', ring: 'ring-violet-400/30' },
  blue: { bg: 'bg-blue-500/15', text: 'text-blue-400', dot: 'bg-blue-400', ring: 'ring-blue-400/30' },
  amber: { bg: 'bg-amber-500/15', text: 'text-amber-400', dot: 'bg-amber-400', ring: 'ring-amber-400/30' },
  green: { bg: 'bg-green-500/15', text: 'text-green-400', dot: 'bg-green-400', ring: 'ring-green-400/30' },
  orange: { bg: 'bg-orange-500/15', text: 'text-orange-400', dot: 'bg-orange-400', ring: 'ring-orange-400/30' },
  rose: { bg: 'bg-rose-500/15', text: 'text-rose-400', dot: 'bg-rose-400', ring: 'ring-rose-400/30' },
  gray: { bg: 'bg-gray-500/15', text: 'text-gray-400', dot: 'bg-gray-500', ring: 'ring-gray-400/30' },
  teal: { bg: 'bg-teal-500/15', text: 'text-teal-400', dot: 'bg-teal-400', ring: 'ring-teal-400/30' },
};

function getAgentMeta(type: string) {
  return AGENT_META[type] || {
    label: type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
    color: 'teal',
    description: '',
  };
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
  const runningAgents = agents.filter(a => a.status === 'running');
  const currentAgent = runningAgents.length > 0
    ? runningAgents[runningAgents.length - 1]
    : null;

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
            <span className={`absolute w-3 h-3 rounded-full ${colors.dot} opacity-40 animate-ping`} />
          )}
        </span>
        <span className="font-medium">{meta.label}</span>
        <ChevronDown className={`w-3 h-3 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`} />
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
          <StatusIcon status={anyRunning && !currentAgent ? 'running' : (agents.length > 0 ? 'idle' : 'running')} color="gray" />
          <span className="text-xs text-gray-400 flex-1">Chat Agent</span>
          <span className="text-[10px] text-gray-600">main</span>
        </div>

        {agents.length === 0 && !anyRunning && (
          <div className="px-3 py-2 text-[11px] text-gray-600">
            No sub-agents active.
          </div>
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
              <span className={`text-xs flex-1 ${agent.status === 'running' ? ac.text : 'text-gray-500'}`}>
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
  const { models, agentModels, setAgentModel } = useApp();

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
      <div className="py-1 max-h-[400px] overflow-y-auto">
        <div className="px-3 py-2 text-[10px] text-gray-600 border-b border-white/[0.04]">
          Override the default model each agent uses. Persisted locally.
        </div>
        {ALL_AGENT_TYPES.map((agentType) => {
          const am = getAgentMeta(agentType);
          const ac = getColors(am.color);
          const override = agentModels[agentType];
          return (
            <div
              key={agentType}
              className="flex items-start gap-2 px-3 py-2 border-b border-white/[0.03] last:border-0"
            >
              <span className={`w-2 h-2 rounded-full ${ac.dot} shrink-0 mt-1.5`} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-xs font-medium ${ac.text}`}>{am.label}</span>
                  {override && (
                    <button
                      onClick={() => setAgentModel(agentType, null)}
                      className="p-0.5 rounded hover:bg-white/[0.08] text-gray-600 hover:text-gray-400"
                      title="Reset to default"
                    >
                      <RotateCcw className="w-3 h-3" />
                    </button>
                  )}
                </div>
                {am.description && (
                  <p className="text-[10px] text-gray-600 mt-0.5 leading-tight">{am.description}</p>
                )}
                <select
                  value={override || ''}
                  onChange={(e) => setAgentModel(agentType, e.target.value || null)}
                  className="mt-1.5 w-full text-[11px] bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-gray-300 focus:outline-none focus:border-white/[0.15] cursor-pointer"
                >
                  <option value="">Default</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

function StatusIcon({ status, color }: { status: string; color: string }) {
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
