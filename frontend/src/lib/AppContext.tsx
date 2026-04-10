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
  setActiveExperiment: (id: string | null, sessionId?: string | null) => void;
  refreshExperiments: () => Promise<void>;
  setSidebarOpen: (open: boolean) => void;
  setSelectedModel: (model: string) => void;
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
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window === 'undefined') return false;
    const stored = localStorage.getItem('trainable:sidebar');
    return stored === 'true';
  });
  const [models, setModels] = useState<ModelInfo[]>([]);

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
    if (typeof window !== 'undefined') {
      localStorage.setItem('trainable:sidebar', String(sidebarOpen));
    }
  }, [sidebarOpen]);

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
        setActiveExperiment,
        refreshExperiments,
        setSidebarOpen,
        setSelectedModel,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}
