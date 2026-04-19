export interface Project {
  id: string;
  name: string;
  description: string;
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
export type DraftToken =
  | { kind: 'text'; value: string }
  | { kind: 'mention'; mention: Mention };
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
