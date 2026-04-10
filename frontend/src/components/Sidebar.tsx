'use client';

import { useState, useRef, useCallback } from 'react';
import {
  Plus,
  MessageSquare,
  Loader2,
  Trash2,
  PanelLeftOpen,
  PanelLeftClose,
} from 'lucide-react';
import { useApp } from '@/lib/AppContext';
import { api } from '@/lib/api';

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

export default function Sidebar() {
  const {
    experiments,
    activeExperimentId,
    setActiveExperiment,
    refreshExperiments,
    sidebarOpen,
    setSidebarOpen,
  } = useApp();
  const [creating, setCreating] = useState(false);

  const handleNewChat = useCallback(async () => {
    setCreating(true);
    try {
      const result = await api.quickCreate();
      await refreshExperiments();
      setActiveExperiment(result.id, result.session_id);
    } catch {
      // silent
    } finally {
      setCreating(false);
    }
  }, [refreshExperiments, setActiveExperiment]);

  const handleDelete = useCallback(
    async (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      try {
        await api.deleteExperiment(id);
        if (activeExperimentId === id) {
          setActiveExperiment(null);
        }
        await refreshExperiments();
      } catch {
        // silent
      }
    },
    [activeExperimentId, setActiveExperiment, refreshExperiments],
  );

  return (
    <div
      className={`shrink-0 h-full flex flex-col bg-[#0f0f0f] border-r border-white/[0.06] transition-all duration-300 ease-in-out overflow-hidden ${
        sidebarOpen ? 'w-[260px]' : 'w-[52px]'
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-2.5 py-3 shrink-0">
        {sidebarOpen ? (
          <>
            <a href="/" className="shrink-0 ml-0.5">
              <img src="/logo-brain.png" alt="Trainable" className="h-5 w-auto" />
            </a>
            <span className="text-sm font-semibold text-gray-300 truncate">Trainable</span>
            <div className="flex-1" />
            <button
              onClick={handleNewChat}
              disabled={creating}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-400 hover:text-white"
              title="New chat"
            >
              {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
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
            <button
              onClick={() => setSidebarOpen(true)}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-500 hover:text-gray-300"
              title="Expand sidebar"
            >
              <PanelLeftOpen className="w-4 h-4" />
            </button>
            <button
              onClick={handleNewChat}
              disabled={creating}
              className="p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors text-gray-400 hover:text-white"
              title="New chat"
            >
              {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            </button>
          </div>
        )}
      </div>

      {/* Experiment list */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-1.5 space-y-0.5">
        {sidebarOpen ? (
          <>
            {experiments.length === 0 && !creating && (
              <div className="px-3 py-8 text-center">
                <MessageSquare className="w-8 h-8 text-gray-700 mx-auto mb-2" />
                <p className="text-xs text-gray-600">No conversations yet</p>
                <p className="text-[10px] text-gray-700 mt-1">Click + to start</p>
              </div>
            )}
            {experiments.map((exp) => {
              const isActive = exp.id === activeExperimentId;
              return (
                <button
                  key={exp.id}
                  onClick={() => setActiveExperiment(exp.id, exp.latest_session_id)}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left transition-colors group ${
                    isActive
                      ? 'bg-white/[0.08] text-white'
                      : 'text-gray-400 hover:bg-white/[0.04] hover:text-gray-300'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(exp.latest_state)}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{exp.name}</p>
                    {exp.dataset_ref && (
                      <p className="text-[10px] text-gray-600 truncate mt-0.5">
                        {exp.dataset_ref.split('/').pop()}
                      </p>
                    )}
                  </div>
                  <span className="text-[10px] text-gray-600 shrink-0">{timeAgo(exp.created_at)}</span>
                  <button
                    onClick={(e) => handleDelete(e, exp.id)}
                    className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.1] transition-all shrink-0"
                  >
                    <Trash2 className="w-3 h-3 text-gray-500" />
                  </button>
                </button>
              );
            })}
          </>
        ) : (
          /* Collapsed: icon-only experiment dots */
          <div className="flex flex-col items-center gap-1 pt-1">
            {experiments.slice(0, 12).map((exp) => {
              const isActive = exp.id === activeExperimentId;
              return (
                <button
                  key={exp.id}
                  onClick={() => setActiveExperiment(exp.id, exp.latest_session_id)}
                  className={`w-8 h-8 rounded-lg flex items-center justify-center transition-colors ${
                    isActive
                      ? 'bg-white/[0.1] ring-1 ring-white/[0.15]'
                      : 'hover:bg-white/[0.06]'
                  }`}
                  title={exp.name}
                >
                  <span className={`w-2.5 h-2.5 rounded-full ${statusDot(exp.latest_state)}`} />
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
