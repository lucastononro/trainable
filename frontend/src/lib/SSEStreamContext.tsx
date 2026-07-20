'use client';

import { createContext, useCallback, useContext, useMemo, useRef, type ReactNode } from 'react';
import type { SSEEvent } from './types';

/**
 * Why a second context (and not AppContext)?
 *
 * `lib/AGENTS.md` rule 1 says new global state belongs in AppContext, and its
 * ❌ list explicitly keeps "SSE events" out of the global store. This context
 * honors that: it holds **no state at all** — just two identity-stable
 * functions (`subscribe`/`publish`) forming a page-scoped pub/sub channel, so
 * it never triggers a re-render. Putting raw SSE fan-out into AppContext is
 * exactly what the ❌ bullet prohibits, and the bus is only meaningful inside
 * the page that owns the EventSource (`HomePage`), so an app-wide provider
 * would be the wrong scope (rule 2: local stays local).
 */

type SSEListener = (sessionId: string, event: SSEEvent) => void;

interface SSEStreamState {
  /** Subscribe to every parsed message from the single page-level session
   *  EventSource (`HomePage.connectSSE`, `/api/sessions/{id}/stream`).
   *  Listeners receive the session id the connection was opened for, so they
   *  can drop events that belong to another session (the backend does not
   *  embed a session id in the event payload — the session is the channel).
   *  Returns an unsubscribe function. Consumers that need the same events
   *  (e.g. the notebook) should use this instead of opening a second
   *  EventSource to the identical endpoint. */
  subscribe: (listener: SSEListener) => () => void;
  /** Internal: invoked by `connectSSE` for every parsed message so
   *  subscribers stay in sync with the single connection. `sessionId` is the
   *  session the publishing EventSource belongs to. Not meant to be called by
   *  consumers of the stream. */
  publish: (sessionId: string, event: SSEEvent) => void;
}

const SSEStreamContext = createContext<SSEStreamState | null>(null);

export function useSSEStream(): SSEStreamState {
  const ctx = useContext(SSEStreamContext);
  if (!ctx) throw new Error('useSSEStream must be used within SSEStreamProvider');
  return ctx;
}

export function SSEStreamProvider({ children }: { children: ReactNode }) {
  const listenersRef = useRef<Set<SSEListener>>(new Set());

  const subscribe = useCallback((listener: SSEListener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  const publish = useCallback((sessionId: string, event: SSEEvent) => {
    listenersRef.current.forEach((listener) => listener(sessionId, event));
  }, []);

  const value = useMemo<SSEStreamState>(() => ({ subscribe, publish }), [subscribe, publish]);

  return <SSEStreamContext.Provider value={value}>{children}</SSEStreamContext.Provider>;
}
