import type {
  Experiment,
  ExperimentDetail,
  CreateExperimentResponse,
  Project,
  ProjectDetail,
  CreateProjectResponse,
  SandboxConfig,
  Session,
  SessionDetail,
  Message,
  Mention,
  Artifact,
  MetricPoint,
  LogEvent,
  ModelInfo,
  ProviderInfo,
  FileTreeNode,
  DeleteResponse,
  AbortResponse,
  UsageSummary,
  Task,
  TaskCreatePayload,
  TaskUpdatePayload,
  SkillCatalogEntry,
  RegisteredModel,
  DeploymentRow,
  RunSnapshotRow,
  DatasetVersionRow,
  LineageGraph,
  DatasetVersionDetail,
  SessionRow,
  ExperimentFullDetail,
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
  // Projects
  listProjects: () => fetchJSON<Project[]>('/projects'),

  createProject: (name?: string, description?: string) =>
    fetchJSON<CreateProjectResponse>('/projects', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    }),

  getProject: (id: string) => fetchJSON<ProjectDetail>(`/projects/${id}`),

  updateProject: (
    id: string,
    patch: { name?: string; description?: string; sandbox_config?: SandboxConfig },
  ) =>
    fetchJSON<Project>(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  deleteProject: (id: string) => fetchJSON<DeleteResponse>(`/projects/${id}`, { method: 'DELETE' }),

  listProjectFiles: (id: string) =>
    fetchJSON<{
      project_id: string;
      project_name: string;
      datasets_root: string;
      files: Array<{
        path: string;
        name: string;
        relative_path?: string;
        size: number | null;
        mtime: number | null;
        s3_key?: string;
        /** true = in sandbox, false = missing, null = couldn't verify */
        in_sandbox?: boolean | null;
      }>;
      s3_error?: string | null;
      sandbox_error?: string | null;
      sandbox_checked?: boolean;
      sandbox_missing_count?: number;
    }>(`/projects/${id}/files`),

  // Experiments
  listExperiments: (params?: {
    projectId?: string;
    q?: string;
    tag?: string;
    pinned?: boolean;
    archived?: boolean;
  }) => {
    const qs = new URLSearchParams();
    if (params?.projectId) qs.set('project_id', params.projectId);
    if (params?.q) qs.set('q', params.q);
    if (params?.tag) qs.set('tag', params.tag);
    if (params?.pinned !== undefined) qs.set('pinned', String(params.pinned));
    if (params?.archived !== undefined) qs.set('archived', String(params.archived));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return fetchJSON<Experiment[]>(`/experiments${suffix}`);
  },

  updateExperiment: (
    id: string,
    patch: {
      name?: string;
      description?: string;
      project_id?: string;
      instructions?: string;
      tags?: string[];
      pinned?: boolean;
      archived?: boolean;
    },
  ) =>
    fetchJSON<Experiment>(`/experiments/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  // Model registry
  listAllModels: () => fetchJSON<import('./types').AllModelsResponse>(`/registry/models`),
  listProjectModels: (projectId: string) =>
    fetchJSON<RegisteredModel[]>(`/projects/${projectId}/models`),
  getModel: (modelId: string) => fetchJSON<RegisteredModel>(`/models/${modelId}`),
  // Returns the absolute backend URL — the browser hits it as a normal
  // GET so the Content-Disposition header drives a download. We keep
  // this as a URL-builder rather than a fetch so the user clicks a real
  // link and the browser handles the streaming.
  modelDownloadUrl: (modelId: string) => `${API_BASE}/models/${modelId}/download`,
  // Read the Modal serving app source the next deploy will ship.
  getServingApp: (modelId: string) =>
    fetchJSON<{ path: string; code: string }>(`/models/${modelId}/serving-app`),
  // Save user edits to the serving app. Backend ast.parses before
  // writing so we never persist syntactically-broken files.
  putServingApp: (modelId: string, code: string) =>
    fetchJSON<{ ok: boolean; path: string; size: number }>(`/models/${modelId}/serving-app`, {
      method: 'PUT',
      body: JSON.stringify({ code }),
    }),
  promoteSession: (sessionId: string, name?: string) =>
    fetchJSON<RegisteredModel>(`/sessions/${sessionId}/promote`, {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  canPromote: (sessionId: string) =>
    fetchJSON<{ available: boolean; path?: string; size_bytes?: number }>(
      `/sessions/${sessionId}/promote/check`,
    ),
  deployModel: (modelId: string, compute?: string) =>
    fetchJSON<DeploymentRow>(`/models/${modelId}/deploy`, {
      method: 'POST',
      body: JSON.stringify({ compute: compute || 'cpu' }),
    }),
  deployComputeOptions: () =>
    fetchJSON<import('./types').ComputeOption[]>(`/deploy/compute-options`),
  modelDeployments: (modelId: string) =>
    fetchJSON<DeploymentRow[]>(`/models/${modelId}/deployments`),
  // Mark a live deployment as stopped. Backend keeps the row for audit
  // history and stops the Modal app via `modal app stop` if the CLI
  // is configured.
  stopDeployment: (deploymentId: string) =>
    fetchJSON<DeploymentRow>(`/deployments/${deploymentId}`, { method: 'DELETE' }),
  // Generate a fresh X-API-Key + replace the Modal secret. Returns the
  // new key in plaintext so the user can copy it. Running containers
  // keep the old key cached until cold-start; user can click Redeploy
  // to force cutover.
  rotateModelKey: (modelId: string) =>
    fetchJSON<{ model_id: string; api_key: string; modal_secret: string; note: string }>(
      `/models/${modelId}/rotate-key`,
      { method: 'POST' },
    ),

  // Snapshots
  takeSnapshot: (sessionId: string) =>
    fetchJSON<RunSnapshotRow>(`/sessions/${sessionId}/snapshot`, { method: 'POST' }),
  getSnapshot: (sessionId: string) => fetchJSON<RunSnapshotRow>(`/sessions/${sessionId}/snapshot`),

  // Dataset versions
  projectDatasetVersions: (projectId: string) =>
    fetchJSON<DatasetVersionRow[]>(`/projects/${projectId}/dataset-versions`),

  // Lineage graph (project / session / experiment scopes)
  projectLineage: (projectId: string) => fetchJSON<LineageGraph>(`/projects/${projectId}/lineage`),
  sessionLineage: (sessionId: string) => fetchJSON<LineageGraph>(`/sessions/${sessionId}/lineage`),
  experimentLineage: (experimentId: string) =>
    fetchJSON<LineageGraph>(`/experiments/${experimentId}/lineage`),

  // Project-level dataset browser + metadata side panel
  listProjectDatasets: (projectId: string) =>
    fetchJSON<DatasetVersionDetail[]>(`/projects/${projectId}/datasets`),
  getDataset: (datasetId: number) => fetchJSON<DatasetVersionDetail>(`/datasets/${datasetId}`),

  // Sidebar tree (Project → Session → Experiment)
  listProjectSessions: (projectId: string) =>
    fetchJSON<SessionRow[]>(`/projects/${projectId}/sessions`),
  listSessionExperiments: (sessionId: string) =>
    fetchJSON<ExperimentDetail[]>(`/sessions/${sessionId}/experiments`),

  // Standalone experiment detail (datasets + model + snapshot rolled up)
  getExperimentDetail: (experimentId: string) =>
    fetchJSON<ExperimentFullDetail>(`/experiments/${experimentId}/detail`),

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

  sendMessage: (
    sessionId: string,
    content: string,
    runAgent: boolean = false,
    agentModels?: Record<string, string>,
    mentions?: Mention[],
    agentThinking?: Record<string, string>,
  ) =>
    fetchJSON<Message>(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({
        content,
        run_agent: runAgent,
        ...(agentModels && Object.keys(agentModels).length > 0
          ? { agent_models: agentModels }
          : {}),
        ...(agentThinking && Object.keys(agentThinking).length > 0
          ? { agent_thinking: agentThinking }
          : {}),
        ...(mentions && mentions.length > 0 ? { mentions } : {}),
      }),
    }),

  getMessages: (sessionId: string) => fetchJSON<Message[]>(`/sessions/${sessionId}/messages`),

  getArtifacts: (sessionId: string) => fetchJSON<Artifact[]>(`/sessions/${sessionId}/artifacts`),

  getMetrics: (sessionId: string) => fetchJSON<MetricPoint[]>(`/sessions/${sessionId}/metrics`),

  getLogEvents: (sessionId: string) => fetchJSON<LogEvent[]>(`/sessions/${sessionId}/log_events`),

  getTasks: (sessionId: string) => fetchJSON<Task[]>(`/sessions/${sessionId}/tasks`),

  createTask: (sessionId: string, body: TaskCreatePayload) =>
    fetchJSON<Task>(`/sessions/${sessionId}/tasks`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateTask: (sessionId: string, taskId: number, body: TaskUpdatePayload) =>
    fetchJSON<Task>(`/sessions/${sessionId}/tasks/${taskId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  deleteTask: (sessionId: string, taskId: number) =>
    fetchJSON<{ status: string; id: number }>(`/sessions/${sessionId}/tasks/${taskId}`, {
      method: 'DELETE',
    }),

  abortSession: (sessionId: string) =>
    fetchJSON<AbortResponse>(`/sessions/${sessionId}/abort`, { method: 'POST' }),

  replyClarification: (sessionId: string, questionId: string, answer: string) =>
    fetchJSON<{ status: string }>(`/sessions/${sessionId}/clarifications/${questionId}`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),

  // Files
  getFileTree: (sessionId: string) =>
    fetchJSON<FileTreeNode>(`/files/tree?root=/sessions/${sessionId}`),

  readFile: (path: string) =>
    fetchJSON<{ path: string; content: string }>(`/files/read?path=${encodeURIComponent(path)}`),

  // Models
  listModels: () => fetchJSON<ModelInfo[]>('/models'),
  listProviders: () => fetchJSON<ProviderInfo[]>('/providers'),

  // Usage / cost
  usageSummary: () => fetchJSON<UsageSummary>(`/usage/summary`),
  projectUsage: (projectId: string) => fetchJSON<UsageSummary>(`/projects/${projectId}/usage`),
  sessionUsage: (sessionId: string) => fetchJSON<UsageSummary>(`/sessions/${sessionId}/usage`),

  // Skills catalog
  listSkills: () => fetchJSON<SkillCatalogEntry[]>(`/skills`),

  // Quick create (no files required) — requires a project
  quickCreate: async (
    projectId: string,
    name?: string,
    instructions?: string,
  ): Promise<CreateExperimentResponse> => {
    const data = new FormData();
    data.append('project_id', projectId);
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
      for (const f of files) {
        // Folder uploads expose `webkitRelativePath` (e.g. "mydata/train/x.csv").
        // Pass it as the part filename so the backend can preserve folder
        // structure instead of flattening everything to basename.
        const name = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
        data.append('files', f, name);
      }
    }
    const res = await fetch(`${API_BASE}/experiments/${experimentId}/attach`, {
      method: 'POST',
      body: data,
    });
    if (!res.ok) throw new Error(`Attach failed: ${res.status}`);
    return res.json();
  },
};
