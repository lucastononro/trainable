'use client';

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { api } from './api';
import type { Experiment, ModelInfo, Project } from './types';

interface AppState {
  projects: Project[];
  activeProjectId: string | null;
  experiments: Experiment[];
  activeExperimentId: string | null;
  activeSessionId: string | null;
  selectedModel: string;
  sidebarOpen: boolean;
  models: ModelInfo[];
  /** Per-agent model overrides, persisted in localStorage. */
  agentModels: Record<string, string>;
  /** True while the active session is running. Drives sidebar spinner. */
  isRunning: boolean;
  setIsRunning: (running: boolean) => void;
  refreshProjects: () => Promise<Project[]>;
  setActiveProject: (id: string | null) => void;
  setActiveExperiment: (id: string | null, sessionId?: string | null) => void;
  refreshExperiments: () => Promise<Experiment[]>;
  setSidebarOpen: (open: boolean) => void;
  setSelectedModel: (model: string) => void;
  setAgentModel: (agentType: string, modelId: string | null) => void;
}

const AppContext = createContext<AppState | null>(null);

const ACTIVE_PROJECT_STORAGE_KEY = 'trainable:activeProject';

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectIdState] = useState<string | null>(null);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [activeExperimentId, setActiveExperimentId] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('claude-sonnet-4-6');
  // Always start false for SSR — hydrate from localStorage in effect below
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [agentModels, setAgentModelsState] = useState<Record<string, string>>({});
  const [isRunning, setIsRunning] = useState(false);

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
    const storedProject = localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY);
    if (storedProject) setActiveProjectIdState(storedProject);
    setHydrated(true);
  }, []);

  const setAgentModel = useCallback((agentType: string, modelId: string | null) => {
    setAgentModelsState((prev) => {
      const next = { ...prev };
      if (modelId) {
        next[agentType] = modelId;
      } else {
        delete next[agentType];
      }
      return next;
    });
  }, []);

  // Persist agent model overrides to localStorage (after hydration)
  useEffect(() => {
    if (hydrated && typeof window !== 'undefined') {
      localStorage.setItem('trainable:agent-models', JSON.stringify(agentModels));
    }
  }, [agentModels, hydrated]);

  const refreshProjects = useCallback(async () => {
    try {
      const list = await api.listProjects();
      setProjects(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  const refreshExperiments = useCallback(async () => {
    try {
      const list = await api.listExperiments();
      setExperiments(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  // Initial load: projects + experiments + models.
  useEffect(() => {
    (async () => {
      const list = await refreshProjects();
      await refreshExperiments();
      // If the persisted active project no longer exists, clear it.
      setActiveProjectIdState((prev) => {
        if (prev && list.some((p) => p.id === prev)) return prev;
        return null;
      });
    })();
    api
      .listModels()
      .then(setModels)
      .catch(() => {});
  }, [refreshProjects, refreshExperiments]);

  useEffect(() => {
    // Only persist after hydration completes to avoid overwriting stored value
    if (hydrated && typeof window !== 'undefined') {
      localStorage.setItem('trainable:sidebar', String(sidebarOpen));
    }
  }, [sidebarOpen, hydrated]);

  useEffect(() => {
    if (hydrated && typeof window !== 'undefined') {
      if (activeProjectId) {
        localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, activeProjectId);
      } else {
        localStorage.removeItem(ACTIVE_PROJECT_STORAGE_KEY);
      }
    }
  }, [activeProjectId, hydrated]);

  const setActiveProject = useCallback(
    (id: string | null) => {
      setActiveProjectIdState(id);
      // Clear active experiment/session if they no longer belong to this project.
      setActiveExperimentId((prev) => {
        if (!prev) return prev;
        const exp = experiments.find((e) => e.id === prev);
        if (exp && exp.project_id === id) return prev;
        return null;
      });
      setActiveSessionId((prev) => {
        if (!prev) return prev;
        const exp = experiments.find((e) => e.latest_session_id === prev);
        if (exp && exp.project_id === id) return prev;
        return null;
      });
    },
    [experiments],
  );

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
      // Auto-sync active project to match the experiment's project.
      if (id) {
        const exp = experiments.find((e) => e.id === id);
        if (exp) setActiveProjectIdState(exp.project_id);
      }
    },
    [experiments],
  );

  return (
    <AppContext.Provider
      value={{
        projects,
        activeProjectId,
        experiments,
        activeExperimentId,
        activeSessionId,
        selectedModel,
        sidebarOpen,
        models,
        agentModels,
        isRunning,
        setIsRunning,
        refreshProjects,
        setActiveProject,
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
