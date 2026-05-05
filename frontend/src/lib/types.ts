export interface SandboxProfile {
  gpu?: string | null;
  timeout?: number | null;
}

export interface SandboxConfig {
  default?: SandboxProfile | null;
  training?: SandboxProfile | null;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  sandbox_config: SandboxConfig;
  created_at: string;
  updated_at: string;
  experiment_count: number;
  dataset_count: number;
  model_count: number;
}

export interface ProjectDetail extends Project {
  experiments: Experiment[];
}

export interface CreateProjectResponse {
  project: Project;
  experiment: Experiment;
  session_id: string;
}

export interface Experiment {
  id: string;
  project_id: string;
  name: string;
  description: string;
  dataset_ref: string;
  instructions: string;
  tags?: string[];
  pinned?: boolean;
  archived?: boolean;
  created_at: string;
  updated_at: string;
  latest_session_id: string | null;
  latest_state: string | null;
}

export interface Session {
  id: string;
  experiment_id: string;
  state: string;
  model?: string;
  created_at: string;
  updated_at: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  tier: 'premium' | 'standard' | 'fast';
  context: string;
  input_cost: number;
  output_cost: number;
  description: string;
}

export interface Message {
  id: number;
  role: 'user' | 'assistant' | 'tool';
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface Artifact {
  id: number;
  stage: string;
  artifact_type: string;
  name: string;
  path: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface MetricPoint {
  step: number;
  name: string;
  value: number;
  stage?: string;
  run_tag?: string | null;
  created_at: string;
}

export interface ChartConfigEntry {
  title: string;
  metrics: string[];
  type: 'line' | 'bar' | 'area';
}

export interface ChartConfig {
  charts: ChartConfigEntry[];
}

export type Stage = 'eda' | 'prep' | 'train';

export type MentionKind = 'file' | 'session';
export interface Mention {
  kind: MentionKind;
  ref: string;
  label: string;
  sandbox_path?: string;
  experiment_id?: string;
}
export type DraftToken = { kind: 'text'; value: string } | { kind: 'mention'; mention: Mention };
export type Draft = DraftToken[];

export interface SSEEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface ExperimentDetail extends Experiment {
  sessions: Session[];
}

export interface SessionDetail extends Session {
  experiment: Experiment | null;
  messages: Message[];
  artifacts: Artifact[];
  processed_meta: Record<string, unknown> | null;
  is_running?: boolean;
}

export interface FileTreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileTreeNode[];
}

// API response shapes
export interface CreateExperimentResponse extends Experiment {
  session_id: string;
  uploaded_files?: string[];
}
export interface DeleteResponse {
  status: string;
}
export interface AbortResponse {
  status: string;
}

// SSE event data shapes
export interface ToolEventData {
  tool: string;
  input?: { code?: string };
  output?: string;
}
export interface AgentMessageData {
  text: string;
}
export interface AgentErrorData {
  error: string;
}
export interface StateChangeData {
  state: string;
}
export interface CodeOutputData {
  text: string;
  stream: string;
}
export interface FileCreatedData {
  path: string;
  name: string;
  type: string;
  stage: string;
}
export interface FilesReadyData {
  files: Array<{ path: string; type: string }>;
  stage: string;
  workspace?: string;
}
export interface ReportReadyData {
  content: string;
  stage: string;
}
export interface MetricEventData {
  step: number;
  metrics: Record<string, number>;
  run?: string;
}
export interface ChartConfigEventData {
  charts: Array<{ title: string; metrics: string[]; type: string }>;
}
export interface GeneratedFile {
  path: string;
  type: string;
}

export interface UsageEvent {
  id: number;
  session_id: string;
  project_id: string | null;
  kind: 'llm' | 'sandbox';
  agent_type: string | null;
  agent_id: string | null;
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  sandbox_seconds: number;
  gpu_type: string | null;
  cost_usd: number;
  is_error: boolean;
  extra: Record<string, unknown>;
  created_at: string;
  cache_hit_pct?: number;
}

export interface UsageSummary {
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
    cost_usd: number;
    sandbox_seconds: number;
    llm_calls: number;
    sandbox_runs: number;
  };
  by_day: Array<{
    date: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    sandbox_seconds: number;
  }>;
  by_agent: Array<{
    agent: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    sandbox_seconds: number;
  }>;
  by_model: Array<{
    model: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
  events: UsageEvent[];
}

export interface SkillCatalogEntry {
  name: string;
  slug: string;
  description: string;
  when_to_use: string;
  version: string;
  files: number;
}

export interface RegisteredModel {
  id: string;
  project_id: string;
  name: string;
  version: number;
  source_session_id: string;
  artifact_uri: string;
  artifact_size_bytes: number;
  metrics_summary: Record<string, number>;
  framework: string | null;
  status: string;
  created_at: string;
}

export interface DeploymentRow {
  id: string;
  model_id: string;
  endpoint_url: string | null;
  status: string;
  error: string | null;
  modal_app: string | null;
  modal_function: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunSnapshotRow {
  id: number;
  session_id: string;
  dataset_hash: string | null;
  code_hash: string | null;
  hyperparams: Record<string, unknown>;
  env_lockfile_size: number;
  manifest_uri: string | null;
  created_at: string;
}

export interface DatasetVersionRow {
  id: number;
  project_id: string;
  hash: string;
  path: string;
  size_bytes: number;
  parent_hash: string | null;
  created_at: string;
}

export interface CompareResponse {
  sessions: Array<{
    id: string;
    experiment_id?: string;
    experiment_name?: string;
    state?: string;
    model?: string | null;
    created_at?: string;
    missing: boolean;
  }>;
  metrics: Record<
    string,
    Array<{
      session_id: string;
      points: Array<{ step: number; value: number; stage: string }>;
    }>
  >;
  feature_overlap?: {
    common: string[];
    per_session: Record<string, string[]>;
  };
  totals: Record<
    string,
    {
      cost_usd: number;
      input_tokens: number;
      output_tokens: number;
      sandbox_seconds: number;
    }
  >;
}


export type TaskStatus = "pending" | "in_progress" | "completed";

export interface Task {
  id: number;
  session_id: string;
  subject: string;
  active_form: string | null;
  short_description: string;
  description: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
}

export interface TaskCreatePayload {
  subject: string;
  short_description?: string;
  description?: string;
  active_form?: string | null;
  status?: TaskStatus;
}

export type TaskUpdatePayload = Partial<TaskCreatePayload>;

// SSE payloads for task_created and task_updated. Server pushes the full
// Task dict — UI just upserts by id.
export type TaskEventData = Task;
