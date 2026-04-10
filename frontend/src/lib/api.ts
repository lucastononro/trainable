import type {
  Experiment,
  ExperimentDetail,
  CreateExperimentResponse,
  Session,
  SessionDetail,
  Message,
  Artifact,
  MetricPoint,
  ModelInfo,
  FileTreeNode,
  StageStartResponse,
  DeleteResponse,
  AbortResponse,
} from './types';

const API_BASE = '/api';

async function fetchJSON<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  // Experiments
  listExperiments: () => fetchJSON<Experiment[]>('/experiments'),

  createExperiment: async (data: FormData): Promise<CreateExperimentResponse> => {
    const res = await fetch(`${API_BASE}/experiments`, {
      method: 'POST',
      body: data,
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
    return res.json();
  },

  createExperimentFromS3: async (data: FormData): Promise<CreateExperimentResponse> => {
    const res = await fetch(`${API_BASE}/experiments/from-s3`, {
      method: 'POST',
      body: data,
    });
    if (!res.ok) throw new Error(`Create failed: ${res.status}`);
    return res.json();
  },

  getExperiment: (id: string) => fetchJSON<ExperimentDetail>(`/experiments/${id}`),

  deleteExperiment: (id: string) =>
    fetchJSON<DeleteResponse>(`/experiments/${id}`, { method: 'DELETE' }),

  // Sessions
  createSession: (experimentId: string) =>
    fetchJSON<Session>(`/experiments/${experimentId}/sessions`, { method: 'POST' }),

  getSession: (id: string) => fetchJSON<SessionDetail>(`/sessions/${id}`),

  sendMessage: (sessionId: string, content: string, runAgent: boolean = false) =>
    fetchJSON<Message>(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content, run_agent: runAgent }),
    }),

  getMessages: (sessionId: string) => fetchJSON<Message[]>(`/sessions/${sessionId}/messages`),

  startStage: (sessionId: string, stage: string, gpu?: string, instructions?: string) =>
    fetchJSON<StageStartResponse>(`/sessions/${sessionId}/stages/${stage}/start`, {
      method: 'POST',
      body: JSON.stringify({ gpu: gpu || null, instructions: instructions || null }),
    }),

  getArtifacts: (sessionId: string) => fetchJSON<Artifact[]>(`/sessions/${sessionId}/artifacts`),

  getMetrics: (sessionId: string) => fetchJSON<MetricPoint[]>(`/sessions/${sessionId}/metrics`),

  abortSession: (sessionId: string) =>
    fetchJSON<AbortResponse>(`/sessions/${sessionId}/abort`, { method: 'POST' }),

  // Files
  getFileTree: (sessionId: string) =>
    fetchJSON<FileTreeNode>(`/files/tree?root=/sessions/${sessionId}`),

  readFile: (path: string) =>
    fetchJSON<{ path: string; content: string }>(`/files/read?path=${encodeURIComponent(path)}`),

  // Models
  listModels: () => fetchJSON<ModelInfo[]>('/models'),

  // Quick create (no files required)
  quickCreate: async (name?: string, instructions?: string): Promise<CreateExperimentResponse> => {
    const data = new FormData();
    if (name) data.append('name', name);
    if (instructions) data.append('instructions', instructions);
    const res = await fetch(`${API_BASE}/experiments/quick`, {
      method: 'POST',
      body: data,
    });
    if (!res.ok) throw new Error(`Quick create failed: ${res.status}`);
    return res.json();
  },

  // Attach data to existing experiment
  attachData: async (experimentId: string, files?: File[], s3Path?: string, sessionId?: string) => {
    const data = new FormData();
    if (s3Path) data.append('s3_path', s3Path);
    if (sessionId) data.append('session_id', sessionId);
    if (files) {
      for (const f of files) data.append('files', f);
    }
    const res = await fetch(`${API_BASE}/experiments/${experimentId}/attach`, {
      method: 'POST',
      body: data,
    });
    if (!res.ok) throw new Error(`Attach failed: ${res.status}`);
    return res.json();
  },
};
