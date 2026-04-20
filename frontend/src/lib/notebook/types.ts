// Minimal TS mirror of nbformat v4 — only what the UI needs.

export type OutputType = 'stream' | 'display_data' | 'execute_result' | 'error';

export interface StreamOutput {
  output_type: 'stream';
  name: 'stdout' | 'stderr';
  text: string | string[];
}

export interface DisplayOutput {
  output_type: 'display_data' | 'execute_result';
  data: Record<string, string | string[]>;
  metadata?: Record<string, unknown>;
  execution_count?: number | null;
}

export interface ErrorOutput {
  output_type: 'error';
  ename: string;
  evalue: string;
  traceback: string[];
}

export type CellOutput = StreamOutput | DisplayOutput | ErrorOutput;

export interface CodeCell {
  id: string;
  cell_type: 'code';
  source: string | string[];
  outputs: CellOutput[];
  execution_count: number | null;
  metadata?: Record<string, unknown>;
  // Client-side only: last run duration (ms) for the exec-count badge tooltip.
  _last_duration_ms?: number | null;
}

export interface MarkdownCell {
  id: string;
  cell_type: 'markdown';
  source: string | string[];
  metadata?: Record<string, unknown>;
}

export type NotebookCell = CodeCell | MarkdownCell;

export interface Notebook {
  cells: NotebookCell[];
  metadata: Record<string, unknown>;
  nbformat: number;
  nbformat_minor: number;
}

export type KernelState = 'starting' | 'idle' | 'busy' | 'dead';

export interface KernelStatus {
  state: KernelState;
  last_active: number | null;
  created_at?: number;
}

// SSE event payloads from the backend. `notebook_name` identifies which of
// the session's many notebooks the event belongs to.
export interface CellStartedEvent {
  notebook_name: string;
  cell_id: string;
}
export interface CellStreamEvent {
  notebook_name: string;
  cell_id: string;
  name: 'stdout' | 'stderr';
  text: string;
}
export interface CellDisplayEvent {
  notebook_name: string;
  cell_id: string;
  data: Record<string, string | string[]>;
  metadata?: Record<string, unknown>;
}
export interface CellErrorEvent {
  notebook_name: string;
  cell_id: string;
  ename: string;
  evalue: string;
  traceback: string[];
}
export interface CellCompletedEvent {
  notebook_name: string;
  cell_id: string;
  exec_count: number | null;
  duration_ms?: number | null;
  had_error?: boolean;
}
export interface KernelStateEvent {
  state: KernelState;
}
export interface NotebookCreatedEvent {
  notebook_name: string;
  notebook_path: string;
}

export interface NotebookListItem {
  name: string;
  path: string;
  cells: number | null;
}

export function sourceToString(src: string | string[]): string {
  return Array.isArray(src) ? src.join('') : src;
}
