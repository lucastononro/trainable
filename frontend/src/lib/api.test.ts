import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

function jsonResponse(body: unknown, init?: { ok?: boolean; status?: number }) {
  return {
    ok: init?.ok ?? true,
    status: init?.status ?? 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe('api (fetchJSON-backed helpers)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('listProjects GETs /api/projects and returns the parsed body', async () => {
    const projects = [{ id: 'p1', name: 'Iris' }];
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(projects));

    const result = await api.listProjects();

    expect(fetch).toHaveBeenCalledWith(
      '/api/projects',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    );
    expect(result).toEqual(projects);
  });

  it('createProject POSTs a JSON body with name/description', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ project: { id: 'p1' } }));

    await api.createProject('My Project', 'a description');

    expect(fetch).toHaveBeenCalledWith(
      '/api/projects',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'My Project', description: 'a description' }),
      }),
    );
  });

  it('listExperiments builds a query string only from the params that are set', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));

    await api.listExperiments({ projectId: 'proj-1', pinned: true });

    const [url] = vi.mocked(fetch).mock.calls[0];
    const parsed = new URL(url as string, 'http://localhost');
    expect(parsed.pathname).toBe('/api/experiments');
    expect(parsed.searchParams.get('project_id')).toBe('proj-1');
    expect(parsed.searchParams.get('pinned')).toBe('true');
    expect(parsed.searchParams.has('q')).toBe(false);
    expect(parsed.searchParams.has('archived')).toBe(false);
  });

  it('listExperiments omits the query string entirely when no params are given', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse([]));

    await api.listExperiments();

    expect(fetch).toHaveBeenCalledWith('/api/experiments', expect.anything());
  });

  it('throws a descriptive Error when the response is not ok', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse('not found', { ok: false, status: 404 }));

    await expect(api.getExperiment('missing-id')).rejects.toThrow(/API error 404: "not found"/);
  });

  it('sendMessage only includes agent_models/agent_thinking/mentions when non-empty', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 'm1' }));

    await api.sendMessage('sess-1', 'hello');

    const [, options] = vi.mocked(fetch).mock.calls[0];
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body).toEqual({ content: 'hello', run_agent: false });
  });

  it('sendMessage includes agent_models/mentions when provided', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ id: 'm1' }));

    await api.sendMessage('sess-1', 'hello', true, { trainer: 'gpt' }, [
      { id: 'ds-1', label: 'ds', kind: 'dataset' } as never,
    ]);

    const [, options] = vi.mocked(fetch).mock.calls[0];
    const body = JSON.parse((options as RequestInit).body as string);
    expect(body.run_agent).toBe(true);
    expect(body.agent_models).toEqual({ trainer: 'gpt' });
    expect(body.mentions).toHaveLength(1);
    expect(body.agent_thinking).toBeUndefined();
  });
});

describe('api (raw fetch / FormData helpers)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('createExperiment POSTs the given FormData without forcing a JSON content-type', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ experiment: { id: 'e1' } }));
    const form = new FormData();
    form.append('name', 'iris');

    await api.createExperiment(form);

    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('/api/experiments');
    expect((options as RequestInit).method).toBe('POST');
    expect((options as RequestInit).body).toBe(form);
    expect((options as RequestInit).headers).toBeUndefined();
  });

  it('createExperiment throws when the upload fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({ ok: false, status: 500 } as Response);

    await expect(api.createExperiment(new FormData())).rejects.toThrow(/Upload failed: 500/);
  });

  it('attachData prefers webkitRelativePath over the bare file name for folder uploads', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ok: true }));
    const file = new File(['a,b\n1,2'], 'x.csv', { type: 'text/csv' });
    Object.defineProperty(file, 'webkitRelativePath', {
      value: 'mydata/train/x.csv',
    });

    await api.attachData('exp-1', [file]);

    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('/api/experiments/exp-1/attach');
    const body = (options as RequestInit).body as FormData;
    const uploaded = body.get('files') as File;
    expect(uploaded.name).toBe('mydata/train/x.csv');
  });

  it('attachData falls back to the bare file name when webkitRelativePath is absent', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ ok: true }));
    const file = new File(['a,b\n1,2'], 'plain.csv', { type: 'text/csv' });

    await api.attachData('exp-1', [file]);

    const [, options] = vi.mocked(fetch).mock.calls[0];
    const body = (options as RequestInit).body as FormData;
    const uploaded = body.get('files') as File;
    expect(uploaded.name).toBe('plain.csv');
  });
});

// The frontend must only ever emit *relative* /api URLs (issue #96): the
// browser resolves them against the page origin and the Next.js rewrite in
// next.config.js proxies them to the backend. These tests pin the URL-builder
// contract so a hardcoded scheme/host/port can't sneak back in.

describe('api.filesRawUrl', () => {
  it('builds a relative /api URL (no scheme, host, or port)', () => {
    const url = api.filesRawUrl('/data/uploads/report.html');
    expect(url).toBe('/api/files/raw?path=%2Fdata%2Fuploads%2Freport.html');
    expect(url.startsWith('/api/')).toBe(true);
    expect(url).not.toMatch(/^https?:\/\//);
    expect(url).not.toContain('localhost');
    expect(url).not.toContain(':8000');
  });

  it('percent-encodes query-breaking characters in the path', () => {
    const url = api.filesRawUrl('/sessions/s 1/eda/plot#1&x=?.png');
    // Everything after `path=` must be a single encoded query value.
    const [, query] = url.split('?path=');
    expect(query).not.toContain('#');
    expect(query).not.toContain('&');
    expect(query).not.toContain('?');
    expect(query).not.toContain(' ');
    expect(decodeURIComponent(query)).toBe('/sessions/s 1/eda/plot#1&x=?.png');
  });

  it('round-trips arbitrary session paths through URLSearchParams', () => {
    const path = '/sessions/abc-123/eda/übersicht (v2).pdf';
    const url = new URL(api.filesRawUrl(path), 'http://any-origin.example');
    expect(url.pathname).toBe('/api/files/raw');
    expect(url.searchParams.get('path')).toBe(path);
  });
});

describe('api.modelDownloadUrl', () => {
  it('builds a relative /api URL', () => {
    expect(api.modelDownloadUrl('m-42')).toBe('/api/models/m-42/download');
  });
});
