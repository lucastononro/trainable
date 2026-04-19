import type { Notebook, KernelStatus, NotebookListItem } from './types';

const API_BASE = '/api';

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export const notebookApi = {
  list: (sessionId: string) =>
    fetchJSON<{ notebooks: NotebookListItem[] }>(
      `/sessions/${sessionId}/notebooks`,
    ),

  open: (sessionId: string, name: string) =>
    fetchJSON<Notebook>(`/sessions/${sessionId}/notebooks/${name}/open`, {
      method: 'POST',
    }),

  get: (sessionId: string, name: string) =>
    fetchJSON<Notebook>(`/sessions/${sessionId}/notebooks/${name}`),

  put: (sessionId: string, name: string, nb: Notebook) =>
    fetchJSON<{ ok: boolean }>(`/sessions/${sessionId}/notebooks/${name}`, {
      method: 'PUT',
      body: JSON.stringify(nb),
    }),

  executeCell: (
    sessionId: string,
    name: string,
    cellId: string,
    code: string,
  ) =>
    fetchJSON<{ ok: boolean }>(
      `/sessions/${sessionId}/notebooks/${name}/cells/${cellId}/execute`,
      { method: 'POST', body: JSON.stringify({ code }) },
    ),

  startKernel: (sessionId: string) =>
    fetchJSON<{ ok: boolean }>(`/sessions/${sessionId}/notebook/start`, {
      method: 'POST',
    }),

  interrupt: (sessionId: string) =>
    fetchJSON<{ ok: boolean }>(`/sessions/${sessionId}/notebook/interrupt`, {
      method: 'POST',
    }),

  shutdown: (sessionId: string) =>
    fetchJSON<{ ok: boolean }>(`/sessions/${sessionId}/notebook/shutdown`, {
      method: 'POST',
    }),

  status: (sessionId: string) =>
    fetchJSON<KernelStatus>(`/sessions/${sessionId}/notebook/status`),

  downloadUrl: (sessionId: string, name: string) =>
    `${API_BASE}/sessions/${sessionId}/notebooks/${name}/download`,

  // File served as raw bytes (images, etc.) — used by markdown cells to
  // render `![](figures/x.png)` references inside the session workspace.
  rawFileUrl: (path: string) =>
    `${API_BASE}/files/raw?path=${encodeURIComponent(path)}`,
};
