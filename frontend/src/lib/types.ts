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
  /** Agent-declared lifecycle parent (post schema-flip). Nullable for
   *  legacy 1:1 rows where the session pointed at the experiment. */
  session_id?: string | null;
  name: string;
  description: string;
  /** 1-3 sentence statement of what this experiment tests. AI-written. */
  hypothesis?: string;
  /** Lifecycle state — created | prepping | training | trained |
   *  failed | abandoned. Defaults to created on new agent-declared rows. */
  state?: string;
  started_at?: string | null;
  completed_at?: string | null;
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

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high';

export interface ThinkingSpec {
  default: ThinkingLevel;
  levels: ThinkingLevel[];
}

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  tier: 'premium' | 'standard' | 'fast';
  context: string;
  input_cost: number;
  output_cost: number;
  description: string;
  experimental?: boolean;
  thinking?: ThinkingSpec;
}

export interface ProviderInfo {
  id: string;
  available: boolean;
  /** Env var names that would enable this provider when set. */
  missing_env: string[];
  /** Whether the agent runner can actually dispatch to this provider today. */
  runner_supported: boolean;
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

export interface SessionUsageRow {
  session_id: string;
  cost_usd: number;
  llm_cost_usd: number;
  compute_cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  compute_seconds: number;
  llm_calls: number;
  compute_runs: number;
  agents: string[];
  models: string[];
  first_seen: string | null;
  last_seen: string | null;
}

export interface UsageSummary {
  totals: {
    input_tokens: number;
    output_tokens: number;
    cache_read_input_tokens: number;
    cache_creation_input_tokens: number;
    cost_usd: number;
    llm_cost_usd: number;
    compute_cost_usd: number;
    sandbox_seconds: number; // legacy alias of compute_seconds
    compute_seconds: number;
    llm_calls: number;
    sandbox_runs: number; // legacy alias of compute_runs
    compute_runs: number;
  };
  by_day: Array<{
    date: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    llm_cost_usd: number;
    compute_cost_usd: number;
    sandbox_seconds: number;
  }>;
  by_agent: Array<{
    agent: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    llm_cost_usd: number;
    compute_cost_usd: number;
    sandbox_seconds: number;
  }>;
  by_model: Array<{
    model: string;
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
  }>;
  by_session: SessionUsageRow[];
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

// ---------------------------------------------------------------------------
// Lineage graph + agent-declared experiment surfaces
// ---------------------------------------------------------------------------

export type LineageNodeType = 'dataset' | 'experiment' | 'model';

export interface LineageNodeBase {
  id: string;
  type: LineageNodeType;
  name: string;
  description?: string;
  created_at?: string;
}

export interface LineageDatasetNode extends LineageNodeBase {
  type: 'dataset';
  kind: 'raw' | 'processed';
  path: string;
  size_bytes: number;
  hash: string;
  source_session_id: string | null;
  source_experiment_id: string | null;
  metadata: Record<string, unknown>;
}

export interface LineageExperimentNode extends LineageNodeBase {
  type: 'experiment';
  experiment_id: string;
  session_id: string | null;
  hypothesis: string;
  state: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface LineageModelNode extends LineageNodeBase {
  type: 'model';
  model_id: string;
  experiment_id: string | null;
  framework: string;
  metrics_summary: Record<string, number>;
  hyperparams: Record<string, unknown>;
  version: number;
}

export type LineageNode = LineageDatasetNode | LineageExperimentNode | LineageModelNode;

export interface LineageEdge {
  id: string;
  source: string;
  target: string;
  kind: 'derives_from' | 'feeds' | 'produces';
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

// Project-level dataset detail (with kind/description/parent_id from the
// agent-declared schema flip).
export interface DatasetVersionDetail {
  id: number;
  project_id: string;
  kind: 'raw' | 'processed';
  name: string;
  description: string;
  hash: string;
  path: string;
  size_bytes: number;
  parent_id: number | null;
  parent_hash: string | null;
  source_session_id: string | null;
  source_experiment_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

// Sidebar tree row for the new Project → Session → Experiment hierarchy.
export interface SessionRow {
  id: string;
  project_id: string | null;
  experiment_id: string | null;
  state: string;
  model: string | null;
  created_at: string;
  updated_at: string;
}

// Standalone experiment detail page payload — the experiment row plus its
// linked datasets (with role), the registered model, and the snapshot.
export interface ExperimentFullDetail {
  id: string;
  project_id: string;
  session_id: string | null;
  name: string;
  description: string;
  hypothesis: string;
  state: string;
  started_at: string | null;
  completed_at: string | null;
  dataset_ref: string;
  instructions: string;
  tags: string[];
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
  datasets: Array<DatasetVersionDetail & { role: string }>;
  model: RegisteredModel | null;
  snapshot: RunSnapshotRow | null;
  /** Sessions attached to this experiment — both the canonical
   *  Experiment.session_id (new schema) and any legacy
   *  Session.experiment_id children, deduped by id. */
  sessions: Session[];
}
