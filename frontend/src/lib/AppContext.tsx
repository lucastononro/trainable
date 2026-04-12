'use client';

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { api } from './api';
import type { Experiment, ModelInfo } from './types';

interface AppState {
  experiments: Experiment[];
  activeExperimentId: string | null;
  activeSessionId: string | null;
  selectedModel: string;
  sidebarOpen: boolean;
  models: ModelInfo[];
  /** Per-agent model overrides, persisted in localStorage. */
  agentModels: Record<string, string>;
  setActiveExperiment: (id: string | null, sessionId?: string | null) => void;
  refreshExperiments: () => Promise<void>;
  setSidebarOpen: (open: boolean) => void;
  setSelectedModel: (model: string) => void;
  setAgentModel: (agentType: string, modelId: string | null) => void;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [activeExperimentId, setActiveExperimentId] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-6');
  // Always start false for SSR — hydrate from localStorage in effect below
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [agentModels, setAgentModelsState] = useState<Record<string, string>>({});

  // Hydrate client-only state from localStorage on mount (prevents SSR mismatch)
  useEffect(() => {
    const storedSidebar = localStorage.getItem('trainable:sidebar');
    if (storedSidebar === 'true') setSidebarOpen(true);
    const storedAgentModels = localStorage.getItem('trainable:agent-models');
    if (storedAgentModels) {
      try {
        const parsed = JSON.parse(storedAgentModels);
        if (parsed && typeof parsed === 'object') setAgentModelsState(parsed);
      } catch {
        // ignore corrupt value
      }
    }
    setHydrated(true);
  }, []);

  const setAgentModel = useCallback(
    (agentType: string, modelId: string | null) => {
      setAgentModelsState((prev) => {
        const next = { ...prev };
        if (modelId) {
          next[agentType] = modelId;
        } else {
          delete next[agentType];
        }
        return next;
      });
    },
    [],
  );

  // Persist agent model overrides to localStorage (after hydration)
  useEffect(() => {
    if (hydrated && typeof window !== 'undefined') {
      localStorage.setItem('trainable:agent-models', JSON.stringify(agentModels));
    }
  }, [agentModels, hydrated]);

  const refreshExperiments = useCallback(async () => {
    try {
      const list = await api.listExperiments();
      setExperiments(list);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    refreshExperiments();
    api.listModels().then(setModels).catch(() => {});
  }, [refreshExperiments]);

  useEffect(() => {
    // Only persist after hydration completes to avoid overwriting stored value
    if (hydrated && typeof window !== 'undefined') {
      localStorage.setItem('trainable:sidebar', String(sidebarOpen));
    }
  }, [sidebarOpen, hydrated]);

  const setActiveExperiment = useCallback(
    (id: string | null, sessionId?: string | null) => {
      setActiveExperimentId(id);
      if (sessionId !== undefined) {
        setActiveSessionId(sessionId ?? null);
      } else if (id) {
        const exp = experiments.find((e) => e.id === id);
        setActiveSessionId(exp?.latest_session_id ?? null);
      } else {
        setActiveSessionId(null);
      }
    },
    [experiments],
  );

  return (
    <AppContext.Provider
      value={{
        experiments,
        activeExperimentId,
        activeSessionId,
        selectedModel,
        sidebarOpen,
        models,
        agentModels,
        setActiveExperiment,
        refreshExperiments,
        setSidebarOpen,
        setSelectedModel,
        setAgentModel,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}
