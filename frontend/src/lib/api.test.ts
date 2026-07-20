import { describe, expect, it } from 'vitest';

import { api } from './api';

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
