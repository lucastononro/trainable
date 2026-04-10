'use client';

import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Loader2, CheckCircle2, XCircle, Circle } from 'lucide-react';

export interface ActiveAgent {
  id: string;
  type: string;
  status: 'running' | 'completed' | 'failed';
  task?: string;
  depth: number;
  startedAt: number;
}

const AGENT_META: Record<string, { label: string; color: string }> = {
  orchestrator: { label: 'Orchestrator', color: 'violet' },
  eda: { label: 'EDA Agent', color: 'blue' },
  data_prep: { label: 'Data Prep Agent', color: 'amber' },
  feature_eng: { label: 'Feature Eng. Agent', color: 'orange' },
  trainer: { label: 'Training Agent', color: 'green' },
  reviewer: { label: 'Review Agent', color: 'rose' },
  chat: { label: 'Chat Agent', color: 'gray' },
};

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
  };
}

function getColors(color: string) {
  return COLOR_MAP[color] || COLOR_MAP.teal;
}

interface Props {
  agents: ActiveAgent[];
  isRunning: boolean;
}

export default function AgentStatusIndicator({ agents, isRunning }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Find the deepest running agent (the one currently active)
  const runningAgents = agents.filter(a => a.status === 'running');
  const currentAgent = runningAgents.length > 0
    ? runningAgents[runningAgents.length - 1]
    : null;

  // If nothing is running and no agents tracked, show idle state
  const displayType = currentAgent?.type || 'chat';
  const meta = getAgentMeta(displayType);
  const colors = getColors(meta.color);
  const anyRunning = isRunning || runningAgents.length > 0;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg transition-colors text-xs ${
          anyRunning ? `${colors.bg} ${colors.text}` : 'hover:bg-white/[0.06] text-gray-400'
        }`}
      >
        {/* Animated dot */}
        <span className="relative flex items-center justify-center w-3 h-3">
          <span className={`w-2 h-2 rounded-full ${anyRunning ? colors.dot : 'bg-gray-600'}`} />
          {anyRunning && (
            <span className={`absolute w-3 h-3 rounded-full ${colors.dot} opacity-40 animate-ping`} />
          )}
        </span>

        <span className="font-medium">{meta.label}</span>

        {agents.length > 0 && (
          <ChevronDown className={`w-3 h-3 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`} />
        )}
      </button>

      {open && agents.length > 0 && (
        <div className="absolute top-full right-0 mt-1 w-64 bg-[#1a1a1a] border border-white/[0.08] rounded-xl shadow-xl z-50 overflow-hidden animate-scale-in">
          <div className="px-3 py-2 border-b border-white/[0.06]">
            <span className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
              Agent Stack
            </span>
          </div>
          <div className="py-1 max-h-[300px] overflow-y-auto">
            {/* Root agent (chat) — always show */}
            <div className="flex items-center gap-2 px-3 py-1.5">
              <StatusIcon status={anyRunning && !currentAgent ? 'running' : (agents.length > 0 ? 'idle' : 'running')} color="gray" />
              <span className="text-xs text-gray-400 flex-1">Chat Agent</span>
              <span className="text-[10px] text-gray-600">main</span>
            </div>

            {/* Tracked agents */}
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
                  {agent.status === 'running' && (
                    <Elapsed since={agent.startedAt} />
                  )}
                  {agent.status === 'completed' && agent.task && (
                    <span className="text-[10px] text-gray-600 truncate max-w-[80px]" title={agent.task}>
                      done
                    </span>
                  )}
                  {agent.status === 'failed' && (
                    <span className="text-[10px] text-red-400">failed</span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
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
  // idle
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
